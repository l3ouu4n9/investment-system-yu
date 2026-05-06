"""Manual Step 1 workflow: render prompt and ingest RESEARCH_JSON."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from investment_orchestrator.common.io import ensure_dir, file_exists, read_text, write_text
from investment_orchestrator.common.paths import repo_root, require_prompt_path
from investment_orchestrator.llm.manual_output import (
    ensure_manual_output_metadata_template,
    render_prompt,
    write_rendered_prompt,
)
from investment_orchestrator.parsers.extract_research_json import extract_research_json
from investment_orchestrator.validators.strategy_settings import parse_strategy_settings_text


STEP1_DIRNAME = "step1_research"
PROMPT_FILENAME = "prompt.txt"
RAW_OUTPUT_FILENAME = "raw_output.txt"
RESEARCH_OUTPUT_FILENAME = "research_output.json"
CURRENT_RUN_INPUT_NOTES_RE = re.compile(
    r"(?:\r?\n)*────────────────────────────────────────\r?\n"
    r"【Current Run Inputs（injected by workflow; rendered prompt must contain actual values, not placeholder notes）】"
    r"[\s\S]*?(?=CURRENT_RUN_INPUTS_START)",
)


def current_inputs_dir() -> Path:
    """Return the operator-maintained current input directory."""
    return repo_root() / "inputs" / "current"


def step1_artifact_dir() -> Path:
    """Return the Step 1 artifact directory."""
    return ensure_dir(repo_root() / "artifacts" / "current" / STEP1_DIRNAME)


def step1_prompt_path() -> Path:
    """Return the rendered Step 1 prompt path."""
    return step1_artifact_dir() / PROMPT_FILENAME


def step1_raw_output_path() -> Path:
    """Return the manual Step 1 raw output path."""
    return step1_artifact_dir() / RAW_OUTPUT_FILENAME


def step1_research_output_path() -> Path:
    """Return the parsed research output path."""
    return step1_artifact_dir() / RESEARCH_OUTPUT_FILENAME


def resolve_step1_prompt_template_path() -> Path:
    """Resolve the formal Step 1 prompt template from prompts/."""
    return require_prompt_path("research_dual_lane.txt")


def _require_non_empty_text(path: Path, *, label: str) -> str:
    """Read a required text input and fail clearly when it is missing or empty."""
    try:
        text = read_text(path)
    except FileNotFoundError as exc:
        raise FileNotFoundError(f"Missing required {label}: {path}") from exc

    if not text.strip():
        raise ValueError(f"Required {label} is empty: {path}")
    return text


def load_strategy_settings_yaml_text() -> str:
    """Read the operator-maintained strategy settings YAML exactly as stored on disk."""
    return _require_non_empty_text(
        current_inputs_dir() / "strategy_settings.yaml",
        label="strategy settings YAML input",
    )


def load_strategy_settings() -> dict[str, Any]:
    """Parse the operator-maintained strategy settings YAML."""
    return parse_strategy_settings_text(load_strategy_settings_yaml_text())


def load_portfolio_snapshot_text() -> str:
    """Read the operator-maintained portfolio snapshot exactly as stored on disk."""
    return _require_non_empty_text(
        current_inputs_dir() / "portfolio_snapshot.txt",
        label="portfolio snapshot input",
    )


def load_current_run_user_approved_extended_etf_static_list_json() -> str:
    """Load the current-run approved ETF static list and serialize it as a JSON array string."""
    strategy_settings = load_strategy_settings()
    approved_static_list = strategy_settings.get("user_approved_extended_etf_static_list")
    if approved_static_list is None:
        raise ValueError(
            "Missing required field 'user_approved_extended_etf_static_list' in "
            "inputs/current/strategy_settings.yaml"
        )
    if not isinstance(approved_static_list, list):
        raise ValueError(
            "inputs/current/strategy_settings.yaml field "
            "'user_approved_extended_etf_static_list' must be a list."
        )
    if not all(isinstance(item, str) for item in approved_static_list):
        raise ValueError(
            "inputs/current/strategy_settings.yaml field "
            "'user_approved_extended_etf_static_list' must contain only strings."
        )
    return json.dumps(approved_static_list, ensure_ascii=False, indent=2)


def sanitize_rendered_step1_prompt(text: str) -> str:
    """Remove workflow-only current-run explanatory notes from the rendered prompt."""
    return CURRENT_RUN_INPUT_NOTES_RE.sub("\n", text, count=1)


def build_step1_prompt_text() -> str:
    """Render the Step 1 prompt without mutating the source prompt file."""
    prompt_template = read_text(resolve_step1_prompt_template_path()).rstrip()
    strategy_settings_text = load_strategy_settings_yaml_text()
    portfolio_snapshot_text = load_portfolio_snapshot_text()
    approved_static_list_json = load_current_run_user_approved_extended_etf_static_list_json()

    rendered_prompt = render_prompt(
        prompt_template,
        {
            "current_run_user_approved_extended_etf_static_list_json": approved_static_list_json,
            "strategy_settings_yaml": strategy_settings_text,
            "portfolio_snapshot": portfolio_snapshot_text,
        },
    )
    return sanitize_rendered_step1_prompt(rendered_prompt).rstrip() + "\n"


def render_step1_prompt() -> dict[str, str]:
    """Write the rendered Step 1 prompt and prepare the manual output artifact."""
    artifact_dir = step1_artifact_dir()
    prompt_output_path = step1_prompt_path()
    raw_output_path = step1_raw_output_path()

    write_rendered_prompt(prompt_output_path, build_step1_prompt_text())
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
        "prompt_template_path": str(resolve_step1_prompt_template_path()),
    }


def parse_step1_output() -> dict[str, str]:
    """Parse and validate the manual Step 1 output into research_output.json."""
    payload = extract_research_json(
        raw_output_path=step1_raw_output_path(),
        output_path=step1_research_output_path(),
        pretty=True,
    )
    return {
        "research_output_path": str(step1_research_output_path()),
        "schema_version": str(payload.get("schema_version", "")),
    }
