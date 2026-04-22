"""Manual Step 4 workflow: render prompt and ingest order compiler artifacts."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from investment_orchestrator.common.io import ensure_dir, file_exists, read_json, read_text, write_text
from investment_orchestrator.common.paths import repo_root, require_prompt_path
from investment_orchestrator.llm.manual_output import (
    ensure_manual_output_metadata_template,
    render_prompt,
    write_rendered_prompt,
)
from investment_orchestrator.parsers.extract_orders_and_summary import extract_orders_and_summary
from investment_orchestrator.validators.validate_audited_decision_packet import (
    validate_audited_decision_packet,
)
from investment_orchestrator.workflow.step1_research import step1_research_output_path
from investment_orchestrator.workflow.step2_decision_builder import step2_decision_packet_path
from investment_orchestrator.workflow.step3_audit_engine import step3_audited_decision_packet_path


STEP4_DIRNAME = "step4_order_compiler"
PROMPT_FILENAME = "prompt.txt"
RAW_OUTPUT_FILENAME = "raw_output.txt"
TEMPLATE4_ORDERS_FILENAME = "template4_orders.txt"
ORDER_STATE_EXPORT_FILENAME = "order_state_export.txt"
EXEC_SUMMARY_FILENAME = "exec_summary.txt"


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
    audited_packet = ensure_order_compiler_ready(load_audited_decision_packet())
    template4_orders_text, order_state_export_text, exec_summary_text = extract_orders_and_summary(
        raw_output_path=step4_raw_output_path(),
        template4_orders_path=step4_template4_orders_path(),
        order_state_export_path=step4_order_state_export_path(),
        exec_summary_path=step4_exec_summary_path(),
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
