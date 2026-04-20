"""Path helpers for the transitional investment system."""

from __future__ import annotations

from pathlib import Path


def repo_root() -> Path:
    """Return the repository root."""
    return Path(__file__).resolve().parents[3]


def legacy_dir() -> Path:
    """Return the preserved legacy directory."""
    return repo_root() / "legacy"


def artifacts_dir() -> Path:
    """Return the top-level artifacts directory."""
    return repo_root() / "artifacts"


def inputs_dir() -> Path:
    """Return the top-level operator-maintained inputs directory."""
    return repo_root() / "inputs"


def transitional_inputs_dir() -> Path:
    """Return the transitional operator-maintained inputs directory."""
    return inputs_dir() / "transitional"


def daily_artifact_dir(as_of_date: str) -> Path:
    """Return the daily artifact directory for a date."""
    return artifacts_dir() / "daily" / as_of_date


def weekly_artifact_dir(as_of_date: str) -> Path:
    """Return the weekly artifact directory for a date."""
    return artifacts_dir() / "weekly" / as_of_date


def prompt_path(relative_prompt_path: str) -> Path:
    """Resolve a prompt path relative to the repo-level prompts directory."""
    return repo_root() / "prompts" / relative_prompt_path


def require_prompt_path(relative_prompt_path: str) -> Path:
    """Resolve a prompt path and require that the file exists in prompts/."""
    path = prompt_path(relative_prompt_path)
    if not path.exists():
        raise FileNotFoundError(
            f"Missing required prompt template: {path}. "
            "Workflow prompt templates must exist under prompts/."
        )
    return path


def schemas_dir() -> Path:
    """Return the repo-level JSON schema directory."""
    return repo_root() / "schemas"


def schema_path(schema_name: str) -> Path:
    """Resolve a schema path relative to the repo-level schemas directory."""
    return schemas_dir() / schema_name


def transitional_portfolio_snapshot_path() -> Path:
    return transitional_inputs_dir() / "portfolio_snapshot.txt"


def transitional_order_state_input_path() -> Path:
    return transitional_inputs_dir() / "order_state_input.txt"


def transitional_official_source_registry_path() -> Path:
    return transitional_inputs_dir() / "official_source_registry.json"


def transitional_macro_policy_source_registry_path() -> Path:
    return transitional_inputs_dir() / "macro_policy_source_registry.json"


def legacy_portfolio_snapshot_path() -> Path:
    return legacy_dir() / "notes" / "portfolio_snapshot.txt"


def legacy_official_source_registry_path() -> Path:
    return legacy_dir() / "notes" / "official_source_registry.json"


def legacy_macro_policy_source_registry_path() -> Path:
    return legacy_dir() / "notes" / "macro_policy_source_registry.json"


def legacy_last_weekly_run_path() -> Path:
    return legacy_dir() / "notes" / "last_weekly_run.txt"
