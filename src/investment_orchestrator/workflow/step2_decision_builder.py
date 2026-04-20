"""Manual Step 2 workflow: render prompt and ingest Template 2 + DECISION_PACKET."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import yaml

from investment_orchestrator.common.io import ensure_dir, file_exists, read_json, read_text, write_text
from investment_orchestrator.common.paths import repo_root, require_prompt_path
from investment_orchestrator.llm.manual_output import (
    ensure_manual_output_metadata_template,
    render_prompt,
    write_rendered_prompt,
)
from investment_orchestrator.parsers.extract_template2_and_decision_packet import (
    extract_template2_and_decision_packet,
)
from investment_orchestrator.workflow.step1_research import step1_research_output_path


STEP2_DIRNAME = "step2_decision_builder"
PROMPT_FILENAME = "prompt.txt"
RAW_OUTPUT_FILENAME = "raw_output.txt"
TEMPLATE2_OUTPUT_FILENAME = "template2_output.txt"
DECISION_PACKET_FILENAME = "decision_packet.json"


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
    raw = yaml.safe_load(load_strategy_settings_text())
    if not isinstance(raw, dict):
        raise ValueError("inputs/current/strategy_settings.yaml must parse to a mapping/object.")
    return raw


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
    """Write the rendered Step 2 prompt and prepare the manual output artifact."""
    artifact_dir = step2_artifact_dir()
    prompt_output_path = step2_prompt_path()
    raw_output_path = step2_raw_output_path()

    write_rendered_prompt(prompt_output_path, build_step2_prompt_text())
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


def parse_step2_output() -> dict[str, str]:
    """Parse and validate the manual Step 2 output artifacts."""
    template2_text, decision_packet = extract_template2_and_decision_packet(
        raw_output_path=step2_raw_output_path(),
        template2_output_path=step2_template2_output_path(),
        decision_packet_path=step2_decision_packet_path(),
    )
    return {
        "template2_output_path": str(step2_template2_output_path()),
        "decision_packet_path": str(step2_decision_packet_path()),
        "template2_output_chars": str(len(template2_text)),
        "market_snapshot_type": str(
            decision_packet.get("MARKET_DATA_SNAPSHOT", {}).get("snapshot_type", "")
        ),
    }
