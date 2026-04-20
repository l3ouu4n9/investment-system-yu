"""Manual LLM runner helpers for the transitional workflow."""

from __future__ import annotations

from pathlib import Path
import re
from typing import Any, Callable, Literal, Mapping, Optional, Sequence, TypedDict

import yaml

from investment_orchestrator.common.io import file_exists, read_json, read_text, write_json, write_text
from investment_orchestrator.common.paths import prompt_path


PLACEHOLDER_RE = re.compile(r"{{\s*([A-Za-z0-9_]+)\s*}}")

LITERAL_PLACEHOLDER_ALIASES: dict[str, Sequence[str]] = {
    "<PASTE_TEMPLATE1_JSON_HERE>": ("research_json", "template1_json", "template_1_json"),
    "<PASTE_MARKET_DATA_SNAPSHOT_JSON_HERE>": ("market_data_snapshot_json",),
    "<PASTE_ANCHOR_DRIFT_SNAPSHOT_JSON_HERE>": ("anchor_drift_snapshot_json",),
    "<RUN_TIMESTAMP_ET>": ("run_timestamp_et",),
    "<HARD_CAP_OPEN_ORDERS_BUDGET>": ("hard_cap_open_orders_budget",),
    "paste_full_Template_1A_output_JSON_here": (
        "template_1a_output_json",
        "gather_1a_output",
    ),
}


class PromptRenderError(ValueError):
    """Raised when a prompt cannot be rendered safely."""


class ManualOutputValidationError(ValueError):
    """Raised when a manual LLM output is present but malformed."""


ManualOutputMetadataStatus = Literal["missing", "pending_manual_fill", "valid", "invalid"]


class ManualOutputMetadata(TypedDict):
    schema_version: str
    output_artifact: str
    prompt_artifact: str
    provider: str
    model: str
    generated_at: str
    edited_after_generation: bool
    notes: str


class ManualOutputMetadataInspection(TypedDict):
    status: ManualOutputMetadataStatus
    path: str
    issues: list[str]


def strip_wrapped_block(text: str, start_marker: str, end_marker: str) -> str:
    """Return the inner block when text is fully wrapped by the given markers."""
    stripped_text = text.strip()
    if not stripped_text:
        return text

    lines = stripped_text.splitlines()
    if len(lines) < 3:
        return text
    if lines[0].strip() != start_marker or lines[-1].strip() != end_marker:
        return text

    return "\n".join(lines[1:-1])


def _lookup_value(
    variables: Mapping[str, Any],
    candidate_keys: Sequence[str],
) -> tuple[bool, Optional[str], Optional[str]]:
    for key in candidate_keys:
        if key not in variables:
            continue
        value = variables[key]
        if value is None or value == "":
            return False, key, None
        return True, key, value if isinstance(value, str) else str(value)
    return False, None, None


def load_prompt(prompt_name: str) -> str:
    """Load a prompt template from the repo prompt directory."""
    prompt_file = Path(prompt_name)
    if not prompt_file.is_absolute():
        prompt_file = prompt_path(prompt_name)
    return read_text(prompt_file)


def render_prompt(template: str, variables: Optional[Mapping[str, Any]] = None) -> str:
    """Render a prompt with strict in-place placeholder replacement."""
    variables = variables or {}
    missing: list[str] = []

    def replace(match: re.Match[str]) -> str:
        key = match.group(1)
        found, _, rendered_value = _lookup_value(variables, (key,))
        if not found or rendered_value is None:
            missing.append(f"{{{{ {key} }}}} -> provide '{key}'")
            return match.group(0)
        return rendered_value

    rendered = PLACEHOLDER_RE.sub(replace, template)

    for literal_placeholder, candidate_keys in LITERAL_PLACEHOLDER_ALIASES.items():
        if literal_placeholder not in rendered:
            continue
        found, matched_key, rendered_value = _lookup_value(variables, candidate_keys)
        if not found or rendered_value is None:
            expected = ", ".join(candidate_keys)
            if matched_key is not None:
                missing.append(
                    f"{literal_placeholder} -> '{matched_key}' was provided but empty/null; expected one of: {expected}"
                )
            else:
                missing.append(f"{literal_placeholder} -> expected one of: {expected}")
            continue
        rendered = rendered.replace(literal_placeholder, rendered_value)

    if missing:
        raise PromptRenderError(
            "Prompt rendering failed because required placeholder values were missing:\n- "
            + "\n- ".join(missing)
        )

    return rendered


def write_rendered_prompt(output_path: str | Path, prompt_text: str) -> Path:
    """Persist a rendered prompt to disk."""
    return write_text(output_path, prompt_text)


def manual_output_metadata_path(path: str | Path) -> Path:
    """Return the companion metadata path for a manual output artifact."""
    output_path = Path(path)
    if output_path.suffix:
        return output_path.with_suffix(".meta.json")
    return output_path.with_name(f"{output_path.name}.meta.json")


def ensure_manual_output_metadata_template(
    output_path: str | Path,
    *,
    prompt_path: Optional[str | Path] = None,
) -> Path:
    """Write a sidecar metadata template if one does not already exist."""
    metadata_path = manual_output_metadata_path(output_path)
    if file_exists(metadata_path):
        return metadata_path

    payload: ManualOutputMetadata = {
        "schema_version": "1.0",
        "output_artifact": Path(output_path).name,
        "prompt_artifact": Path(prompt_path).name if prompt_path is not None else "",
        "provider": "chatgpt",
        "model": "",
        "generated_at": "",
        "edited_after_generation": False,
        "notes": "",
    }
    return write_json(metadata_path, payload)


def inspect_manual_output_metadata(
    output_path: str | Path,
    *,
    prompt_path: Optional[str | Path] = None,
) -> ManualOutputMetadataInspection:
    """Inspect the companion metadata file for a manual output artifact."""
    metadata_path = manual_output_metadata_path(output_path)
    if not file_exists(metadata_path):
        return {
            "status": "missing",
            "path": str(metadata_path),
            "issues": ["metadata file is missing"],
        }

    try:
        payload = read_json(metadata_path)
    except Exception as exc:  # noqa: BLE001
        return {
            "status": "invalid",
            "path": str(metadata_path),
            "issues": [f"metadata is not valid JSON: {exc}"],
        }

    if not isinstance(payload, dict):
        return {
            "status": "invalid",
            "path": str(metadata_path),
            "issues": ["metadata payload must be a JSON object"],
        }

    issues: list[str] = []
    pending_fields: list[str] = []

    schema_version = payload.get("schema_version")
    if schema_version != "1.0":
        issues.append(f"schema_version must be '1.0', got {schema_version!r}")

    output_artifact = payload.get("output_artifact")
    if not isinstance(output_artifact, str) or not output_artifact.strip():
        issues.append("output_artifact must be a non-empty string")
    elif output_artifact != Path(output_path).name:
        issues.append(
            f"output_artifact must match {Path(output_path).name!r}, got {output_artifact!r}"
        )

    prompt_artifact = payload.get("prompt_artifact")
    if not isinstance(prompt_artifact, str):
        issues.append("prompt_artifact must be a string")
    elif prompt_path is not None and prompt_artifact and prompt_artifact != Path(prompt_path).name:
        issues.append(
            f"prompt_artifact must match {Path(prompt_path).name!r} when provided, got {prompt_artifact!r}"
        )

    provider = payload.get("provider")
    if not isinstance(provider, str):
        issues.append("provider must be a string")
    elif not provider.strip():
        pending_fields.append("provider")

    model = payload.get("model")
    if not isinstance(model, str):
        issues.append("model must be a string")
    elif not model.strip():
        pending_fields.append("model")

    generated_at = payload.get("generated_at")
    if not isinstance(generated_at, str):
        issues.append("generated_at must be a string")
    elif not generated_at.strip():
        pending_fields.append("generated_at")

    edited_after_generation = payload.get("edited_after_generation")
    if not isinstance(edited_after_generation, bool):
        issues.append("edited_after_generation must be a boolean")

    notes = payload.get("notes")
    if not isinstance(notes, str):
        issues.append("notes must be a string")

    if issues:
        return {
            "status": "invalid",
            "path": str(metadata_path),
            "issues": issues,
        }

    if pending_fields:
        return {
            "status": "pending_manual_fill",
            "path": str(metadata_path),
            "issues": [f"fill required metadata fields: {', '.join(pending_fields)}"],
        }

    return {
        "status": "valid",
        "path": str(metadata_path),
        "issues": [],
    }


def read_manual_output_if_exists(path: str | Path) -> Optional[str]:
    """Read a manually supplied prompt output if present."""
    if not file_exists(path):
        return None
    return read_text(path)


def require_manual_output(
    path: str | Path,
    *,
    validator: Optional[Callable[[str], Any]] = None,
) -> str:
    """Require a manually supplied prompt output."""
    if not file_exists(path):
        raise FileNotFoundError(
            f"Manual LLM output is missing: {path}. Generate the matching *.prompt.txt file, "
            "run it manually, and save the result at the expected *.output.txt location."
        )
    text = read_text(path)
    if validator is not None:
        validator(text)
    return text


def _extract_required_block(text: str, start_marker: str, end_marker: str, *, label: str) -> str:
    stripped = strip_wrapped_block(text, start_marker, end_marker)
    if stripped == text:
        raise ManualOutputValidationError(
            f"{label} must be fully wrapped by {start_marker} and {end_marker} with no extra text."
        )
    return stripped


def _load_yaml_block(block_text: str, *, label: str) -> Any:
    try:
        return yaml.safe_load(block_text)
    except yaml.YAMLError as exc:
        raise ManualOutputValidationError(f"{label} is not valid YAML: {exc}") from exc


def _ensure_mapping(value: Any, *, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ManualOutputValidationError(f"{label} must parse to a mapping/object.")
    return value


def _ensure_list(value: Any, *, label: str) -> list[Any]:
    if not isinstance(value, list):
        raise ManualOutputValidationError(f"{label} must parse to a list.")
    return value


def _require_keys(mapping: Mapping[str, Any], required_keys: Sequence[str], *, label: str) -> None:
    missing = [key for key in required_keys if key not in mapping]
    if missing:
        raise ManualOutputValidationError(f"{label} is missing required keys: {', '.join(missing)}")


def _normalize_daily_quick_check_block(block_text: str) -> str:
    normalized_lines = []
    for line in block_text.splitlines():
        stripped = line.lstrip()
        indent = line[: len(line) - len(stripped)]
        if stripped.startswith("* "):
            if not indent:
                normalized_lines.append(stripped[2:])
            else:
                normalized_lines.append(f"{indent}- {stripped[2:]}")
        else:
            normalized_lines.append(line)
    return "\n".join(normalized_lines)


def _coerce_keyed_sequence_to_mapping(value: Any) -> Any:
    if isinstance(value, list):
        if value and all(isinstance(item, dict) and len(item) == 1 for item in value):
            collapsed: dict[str, Any] = {}
            for item in value:
                key, item_value = next(iter(item.items()))
                collapsed[str(key)] = _coerce_keyed_sequence_to_mapping(item_value)
            return collapsed
        return [_coerce_keyed_sequence_to_mapping(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _coerce_keyed_sequence_to_mapping(item) for key, item in value.items()}
    return value


def validate_override_event_notes_output(text: str) -> list[dict[str, Any]]:
    """Validate override_event_notes.output.txt structure."""
    block = _extract_required_block(
        text,
        "OVERRIDE_EVENT_NOTES_START",
        "OVERRIDE_EVENT_NOTES_END",
        label="override_event_notes.output.txt",
    )
    payload = _load_yaml_block(block, label="OVERRIDE_EVENT_NOTES block")
    notes = _ensure_list(payload, label="OVERRIDE_EVENT_NOTES block")
    for index, note in enumerate(notes):
        item_label = f"OVERRIDE_EVENT_NOTES[{index}]"
        item = _ensure_mapping(note, label=item_label)
        _require_keys(
            item,
            (
                "ticker",
                "source_type",
                "source_date_et",
                "event_type",
                "source_title",
                "source_url",
                "what_changed",
                "why_it_matters_for_daily_check",
                "affected_fields",
                "thesis_impact",
                "execution_impact",
            ),
            label=item_label,
        )
        affected_fields = _ensure_list(item.get("affected_fields"), label=f"{item_label}.affected_fields")
        if not all(isinstance(field, str) and field.strip() for field in affected_fields):
            raise ManualOutputValidationError(f"{item_label}.affected_fields must contain only non-empty strings.")
        if item["thesis_impact"] not in {"none", "low", "medium", "high"}:
            raise ManualOutputValidationError(
                f"{item_label}.thesis_impact must be one of: none, low, medium, high."
            )
        if item["execution_impact"] not in {"low", "medium", "high"}:
            raise ManualOutputValidationError(
                f"{item_label}.execution_impact must be one of: low, medium, high."
            )
    return notes


def validate_daily_quick_check_output(text: str) -> dict[str, Any]:
    """Validate daily_quick_check.output.txt structure."""
    block = _extract_required_block(
        text,
        "DAILY_QUICK_CHECK_START",
        "DAILY_QUICK_CHECK_END",
        label="daily_quick_check.output.txt",
    )
    payload = _load_yaml_block(
        _normalize_daily_quick_check_block(block),
        label="DAILY_QUICK_CHECK block",
    )
    payload = _coerce_keyed_sequence_to_mapping(payload)
    quick_check = _ensure_mapping(payload, label="DAILY_QUICK_CHECK block")
    _require_keys(
        quick_check,
        (
            "as_of",
            "primary_status",
            "evaluation_mode",
            "break_flags",
            "break_flags_count",
            "buy_open_order_maintenance",
            "buy_open_orders_paste_ready_rows",
            "sell_open_order_maintenance",
            "sell_review_queue",
            "full_rerun_decision",
            "do_today_only",
            "do_not_do_today",
            "next_check_trigger",
        ),
        label="DAILY_QUICK_CHECK block",
    )
    as_of = _ensure_mapping(quick_check["as_of"], label="DAILY_QUICK_CHECK.as_of")
    _require_keys(as_of, ("date_et", "date_pt"), label="DAILY_QUICK_CHECK.as_of")
    break_flags = _ensure_mapping(quick_check["break_flags"], label="DAILY_QUICK_CHECK.break_flags")
    _require_keys(
        break_flags,
        (
            "thesis_break",
            "ranking_break",
            "event_shock",
            "concentration_break",
            "execution_break",
            "opportunity_activation",
        ),
        label="DAILY_QUICK_CHECK.break_flags",
    )
    full_rerun_decision = _ensure_mapping(
        quick_check["full_rerun_decision"],
        label="DAILY_QUICK_CHECK.full_rerun_decision",
    )
    _require_keys(
        full_rerun_decision,
        ("run_full_strategy_early", "threshold_met", "minimum_reason"),
        label="DAILY_QUICK_CHECK.full_rerun_decision",
    )
    if quick_check["primary_status"] not in {
        "NO_ACTION",
        "MODIFY_OPEN_ORDERS_ONLY",
        "SELL_REVIEW_REQUIRED",
        "RUN_FULL_STRATEGY_EARLY",
    }:
        raise ManualOutputValidationError(
            "DAILY_QUICK_CHECK.primary_status must be one of: "
            "NO_ACTION, MODIFY_OPEN_ORDERS_ONLY, SELL_REVIEW_REQUIRED, RUN_FULL_STRATEGY_EARLY."
        )
    for field_name in (
        "buy_open_order_maintenance",
        "buy_open_orders_paste_ready_rows",
        "sell_open_order_maintenance",
        "sell_review_queue",
        "do_today_only",
        "do_not_do_today",
        "next_check_trigger",
    ):
        _ensure_list(quick_check[field_name], label=f"DAILY_QUICK_CHECK.{field_name}")
    return quick_check


# TODO: Add real model-calling support later.
# TODO: Save raw prompt / raw model output metadata separately from curated artifacts.
