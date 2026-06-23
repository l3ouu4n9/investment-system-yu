"""Manual Step 4 workflow: render prompt and ingest order compiler artifacts."""

from __future__ import annotations

from collections.abc import Mapping
import json
from pathlib import Path
from typing import Any

from investment_orchestrator.common.io import ensure_dir, file_exists, read_json, read_text, write_text
from investment_orchestrator.common.paths import repo_root, require_prompt_path
from investment_orchestrator.validators.strategy_settings import parse_strategy_settings_text
from investment_orchestrator.llm.manual_output import (
    ensure_manual_output_metadata_template,
    render_prompt,
    write_rendered_prompt,
)
from investment_orchestrator.parsers.extract_orders_and_summary import extract_orders_and_summary
from investment_orchestrator.state.final_execution_safety_gate import (
    enforce_final_execution_safety_gate,
)
from investment_orchestrator.state.upstream_artifact_guard import enforce_upstream_artifact_guard
from investment_orchestrator.validators.validate_audited_decision_packet import (
    validate_audited_decision_packet,
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
from investment_orchestrator.workflow.step3_audit_engine import (
    step3_audited_decision_packet_path,
    step3_blocked_by_upstream_gate_path,
    step3_prompt_path,
    step3_raw_output_path,
    step3_template3_audit_path,
)


STEP4_DIRNAME = "step4_order_compiler"
PROMPT_FILENAME = "prompt.txt"
RAW_OUTPUT_FILENAME = "raw_output.txt"
TEMPLATE4_ORDERS_FILENAME = "template4_orders.txt"
ORDER_STATE_EXPORT_FILENAME = "order_state_export.txt"
EXEC_SUMMARY_FILENAME = "exec_summary.txt"
STEP4_BLOCKED_BY_UPSTREAM_GATE_FILENAME = "step4_blocked_by_upstream_gate.json"
STEP4_BLOCKED_BY_FINAL_EXECUTION_SAFETY_GATE_FILENAME = (
    "step4_blocked_by_final_execution_safety_gate.json"
)


def current_inputs_dir() -> Path:
    """Return the operator-maintained current input directory."""
    return repo_root() / "inputs" / "current"


def step4_artifact_dir() -> Path:
    """Return the Step 4 artifact directory."""
    return ensure_dir(repo_root() / "artifacts" / "current" / STEP4_DIRNAME)


def step4_prompt_path() -> Path:
    """Return the rendered Step 4 prompt path."""
    return step4_artifact_dir() / PROMPT_FILENAME


def step4_raw_output_path() -> Path:
    """Return the manual Step 4 raw output path."""
    return step4_artifact_dir() / RAW_OUTPUT_FILENAME


def step4_template4_orders_path() -> Path:
    """Return the extracted Template 4 orders path."""
    return step4_artifact_dir() / TEMPLATE4_ORDERS_FILENAME


def step4_order_state_export_path() -> Path:
    """Return the extracted order state export path."""
    return step4_artifact_dir() / ORDER_STATE_EXPORT_FILENAME


def step4_exec_summary_path() -> Path:
    """Return the extracted execution summary path."""
    return step4_artifact_dir() / EXEC_SUMMARY_FILENAME


def step4_blocked_by_upstream_gate_path() -> Path:
    """Return the deterministic Step 4 upstream-gate block artifact path."""
    return step4_artifact_dir() / STEP4_BLOCKED_BY_UPSTREAM_GATE_FILENAME


def step4_blocked_by_final_execution_safety_gate_path() -> Path:
    """Return the deterministic Step 4 final-execution-safety-gate block artifact path."""
    return step4_artifact_dir() / STEP4_BLOCKED_BY_FINAL_EXECUTION_SAFETY_GATE_FILENAME


def enforce_step4_upstream_guard() -> None:
    """Fail closed before Step 4 consumes blocked or missing upstream artifacts."""
    enforce_upstream_artifact_guard(
        blocked_artifact_path=step4_blocked_by_upstream_gate_path(),
        upstream_blocked_artifacts=[
            step2_blocked_by_research_gate_path(),
            step3_blocked_by_upstream_gate_path(),
        ],
        required_artifacts=[
            step2_prompt_path(),
            step2_raw_output_path(),
            step2_template2_output_path(),
            step2_decision_packet_path(),
            step3_prompt_path(),
            step3_raw_output_path(),
            step3_template3_audit_path(),
            step3_audited_decision_packet_path(),
        ],
        repo_root_path=repo_root(),
        permission_fallback_artifacts=[step1_research_degraded_mode_decision_path()],
    )


def enforce_step4_final_execution_safety_gate() -> None:
    """Fail closed before order compilation unless deterministic checks all pass.

    This runs after the upstream guard and before any prompt render / order
    compiler readiness check, so Step 3's LLM self-reported audit_passed /
    order_compiler_ready can no longer be the sole release condition.
    """
    enforce_final_execution_safety_gate(
        blocked_artifact_path=step4_blocked_by_final_execution_safety_gate_path(),
        step1_permission_path=step1_research_degraded_mode_decision_path(),
        step2_decision_packet_path=step2_decision_packet_path(),
        step3_audited_packet_path=step3_audited_decision_packet_path(),
        step2_block_path=step2_blocked_by_research_gate_path(),
        step3_block_path=step3_blocked_by_upstream_gate_path(),
        step4_block_path=step4_blocked_by_upstream_gate_path(),
        repo_root_path=repo_root(),
    )


def _require_non_empty_text(path: Path, *, label: str) -> str:
    """Read a required text input and fail clearly when it is missing or empty."""
    try:
        text = read_text(path)
    except FileNotFoundError as exc:
        raise FileNotFoundError(f"Missing required {label}: {path}") from exc

    if not text.strip():
        raise ValueError(f"Required {label} is empty: {path}")
    return text


def _require_json_object(path: Path, *, label: str) -> dict[str, Any]:
    """Read a required JSON artifact and require a top-level object."""
    try:
        payload = read_json(path)
    except FileNotFoundError as exc:
        raise FileNotFoundError(f"Missing required {label}: {path}") from exc
    except json.JSONDecodeError as exc:
        raise ValueError(f"Required {label} is not valid JSON: {path}: {exc}") from exc

    if not isinstance(payload, dict):
        raise ValueError(f"Required {label} must be a JSON object: {path}")
    return payload


def load_strategy_settings_text() -> str:
    """Read the operator-maintained strategy settings YAML exactly as stored on disk."""
    return _require_non_empty_text(
        current_inputs_dir() / "strategy_settings.yaml",
        label="strategy settings YAML input",
    )


def load_strategy_settings() -> dict[str, Any]:
    """Parse the operator-maintained strategy settings YAML for deterministic checks."""
    return parse_strategy_settings_text(load_strategy_settings_text())


def _max_new_tickers_per_week_total(strategy_settings: Mapping[str, Any]) -> int | None:
    """Derive an integer weekly new-ticker ceiling from settings (sum of sub-buckets)."""
    value = strategy_settings.get("max_new_tickers_per_week")
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, Mapping):
        leaves = [v for v in value.values() if isinstance(v, int) and not isinstance(v, bool)]
        return sum(leaves) if leaves else None
    return None


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


def load_decision_packet() -> dict[str, Any]:
    """Read the parsed Step 2 decision packet artifact."""
    return _require_json_object(
        step2_decision_packet_path(),
        label="Step 2 decision_packet.json artifact",
    )


def load_effective_allowed_buy_universe() -> list[str] | None:
    """Read the Step 2 decision packet's per-run effective allowed buy universe.

    This is the run-specific (typically stricter) buy universe. It is read
    defensively: if the decision packet is missing / malformed / lacks a
    non-empty string list, this returns None so the validator falls back to the
    static strategy-settings universe floor (never weaker than settings).
    """
    try:
        payload = read_json(step2_decision_packet_path())
    except (FileNotFoundError, json.JSONDecodeError, ValueError):
        return None
    if not isinstance(payload, dict):
        return None
    universe = payload.get("effective_allowed_buy_universe")
    if isinstance(universe, list):
        tickers = [item for item in universe if isinstance(item, str) and item.strip()]
        if tickers:
            return tickers
    return None


def load_audited_decision_packet() -> dict[str, Any]:
    """Read and validate the parsed Step 3 audited decision packet artifact."""
    payload = _require_json_object(
        step3_audited_decision_packet_path(),
        label="Step 3 audited_decision_packet.json artifact",
    )
    return validate_audited_decision_packet(payload)


def ensure_order_compiler_ready(audited_packet: dict[str, Any]) -> dict[str, Any]:
    """Require that Step 3 explicitly marked the packet as ready for Step 4 compilation."""
    if audited_packet.get("audit_passed") is not True:
        raise ValueError(
            "Order compiler blocked: Step 3 audited_decision_packet.json has audit_passed != true."
        )
    if audited_packet.get("order_compiler_ready") is not True:
        raise ValueError(
            "Order compiler blocked: Step 3 audited_decision_packet.json has order_compiler_ready != true."
        )
    return audited_packet


def build_step4_prompt_text() -> str:
    """Render the Step 4 prompt from formal artifacts plus current operator inputs."""
    prompt_template = read_text(require_prompt_path("strategy_c_order_compiler.txt"))
    decision_packet = load_decision_packet()
    audited_packet = ensure_order_compiler_ready(load_audited_decision_packet())

    market_data_snapshot = decision_packet.get("MARKET_DATA_SNAPSHOT")
    if not isinstance(market_data_snapshot, dict):
        raise ValueError(
            "Step 2 decision_packet.json must contain MARKET_DATA_SNAPSHOT as a JSON object."
        )

    rendered = render_prompt(
        prompt_template,
        {
            "research_json": load_research_output_text(),
            "portfolio_snapshot": load_portfolio_snapshot_text(),
            "strategy_settings": load_strategy_settings_text(),
            "market_data_snapshot": json.dumps(
                market_data_snapshot,
                ensure_ascii=False,
                indent=2,
            ),
            "audited_decision_packet": json.dumps(audited_packet, ensure_ascii=False, indent=2),
        },
    )
    return rendered.rstrip() + "\n"


def render_step4_prompt() -> dict[str, str]:
    """Write the rendered Step 4 prompt and prepare the manual output artifact."""
    enforce_step4_upstream_guard()
    enforce_step4_final_execution_safety_gate()

    artifact_dir = step4_artifact_dir()
    prompt_output_path = step4_prompt_path()
    raw_output_path = step4_raw_output_path()

    write_rendered_prompt(prompt_output_path, build_step4_prompt_text())
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


def parse_step4_output() -> dict[str, str]:
    """Parse and validate the manual Step 4 output artifacts."""
    enforce_step4_upstream_guard()
    enforce_step4_final_execution_safety_gate()

    audited_packet = ensure_order_compiler_ready(load_audited_decision_packet())
    strategy_settings = load_strategy_settings()
    template4_orders_text, order_state_export_text, exec_summary_text = extract_orders_and_summary(
        raw_output_path=step4_raw_output_path(),
        template4_orders_path=step4_template4_orders_path(),
        order_state_export_path=step4_order_state_export_path(),
        exec_summary_path=step4_exec_summary_path(),
        audited_decision_packet=audited_packet,
        strategy_settings=strategy_settings,
        effective_allowed_buy_universe=load_effective_allowed_buy_universe(),
        hard_cap_open_orders_budget=strategy_settings.get("hard_cap_open_orders_budget"),
        max_new_tickers_per_week=_max_new_tickers_per_week_total(strategy_settings),
    )
    return {
        "template4_orders_path": str(step4_template4_orders_path()),
        "order_state_export_path": str(step4_order_state_export_path()),
        "exec_summary_path": str(step4_exec_summary_path()),
        "template4_orders_chars": str(len(template4_orders_text)),
        "order_state_export_chars": str(len(order_state_export_text)),
        "exec_summary_chars": str(len(exec_summary_text)),
        "order_compiler_ready": str(audited_packet.get("order_compiler_ready", "")),
    }
