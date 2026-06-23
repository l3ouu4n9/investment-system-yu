"""Manual Step 1 workflow: render prompt and ingest RESEARCH_JSON."""

from __future__ import annotations

from collections.abc import Mapping
import json
import re
from pathlib import Path
from typing import Any

from investment_orchestrator.common.io import ensure_dir, file_exists, read_text, write_json, write_text
from investment_orchestrator.common.paths import repo_root, require_prompt_path
from investment_orchestrator.llm.manual_output import (
    ensure_manual_output_metadata_template,
    render_prompt,
    write_rendered_prompt,
)
from investment_orchestrator.normalizers.research_handoff_candidate import (
    normalize_research_handoff_candidate,
    research_handoff_normalization_result_to_dict,
)
from investment_orchestrator.parsers.extract_research_json import extract_research_json
from investment_orchestrator.state.last_good_research_handoff import (
    LastGoodResearchHandoffWriteResult,
    last_good_research_handoff_write_result_to_dict,
    read_last_good_research_handoff,
    write_last_good_research_handoff_if_valid,
)
from investment_orchestrator.state.research_availability import (
    evaluate_research_availability,
    research_availability_result_to_dict,
    research_degraded_mode_decision_to_dict,
    research_freshness_report_to_dict,
)
from investment_orchestrator.validators.strategy_settings import parse_strategy_settings_text
from investment_orchestrator.validators.validate_research_handoff import (
    research_handoff_validation_result_to_dict,
    validate_research_handoff,
)


STEP1_DIRNAME = "step1_research"
PROMPT_FILENAME = "prompt.txt"
RAW_OUTPUT_FILENAME = "raw_output.txt"
RESEARCH_OUTPUT_FILENAME = "research_output.json"
RESEARCH_HANDOFF_VALIDATION_FILENAME = "research_handoff_validation.json"
RESEARCH_HANDOFF_CANDIDATE_FILENAME = "research_handoff_candidate.json"
RESEARCH_HANDOFF_CANDIDATE_NORMALIZATION_FILENAME = "research_handoff_candidate_normalization.json"
RESEARCH_HANDOFF_CANDIDATE_VALIDATION_FILENAME = "research_handoff_candidate_validation.json"
LAST_GOOD_WRITE_RESULT_FILENAME = "last_good_research_handoff_write_result.json"
RESEARCH_AVAILABILITY_FILENAME = "research_availability.json"
RESEARCH_FRESHNESS_REPORT_FILENAME = "research_freshness_report.json"
RESEARCH_DEGRADED_MODE_DECISION_FILENAME = "research_degraded_mode_decision.json"
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


def step1_research_handoff_validation_path() -> Path:
    """Return the report-only raw research handoff validation artifact path."""
    return step1_artifact_dir() / RESEARCH_HANDOFF_VALIDATION_FILENAME


def step1_research_handoff_candidate_path() -> Path:
    """Return the normalized research handoff candidate artifact path."""
    return step1_artifact_dir() / RESEARCH_HANDOFF_CANDIDATE_FILENAME


def step1_research_handoff_candidate_normalization_path() -> Path:
    """Return the normalization-diagnostics artifact path for the candidate."""
    return step1_artifact_dir() / RESEARCH_HANDOFF_CANDIDATE_NORMALIZATION_FILENAME


def step1_research_handoff_candidate_validation_path() -> Path:
    """Return the report-only candidate handoff validation artifact path."""
    return step1_artifact_dir() / RESEARCH_HANDOFF_CANDIDATE_VALIDATION_FILENAME


def step1_state_dir() -> Path:
    """Return the persistent state directory (outside current/; survives prepare_next_run)."""
    return ensure_dir(repo_root() / "artifacts" / "state")


def step1_last_good_write_result_path() -> Path:
    """Return the report-only per-run last-good write-result artifact path."""
    return step1_artifact_dir() / LAST_GOOD_WRITE_RESULT_FILENAME


def step1_research_availability_path() -> Path:
    """Return the report-only research availability artifact path."""
    return step1_artifact_dir() / RESEARCH_AVAILABILITY_FILENAME


def step1_research_freshness_report_path() -> Path:
    """Return the report-only research freshness report artifact path."""
    return step1_artifact_dir() / RESEARCH_FRESHNESS_REPORT_FILENAME


def step1_research_degraded_mode_decision_path() -> Path:
    """Return the report-only degraded-mode decision artifact path."""
    return step1_artifact_dir() / RESEARCH_DEGRADED_MODE_DECISION_FILENAME


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


def load_strategy_settings_for_handoff_validation() -> dict[str, Any] | None:
    """Load strategy settings for report-only handoff validation without blocking parse."""
    try:
        return load_strategy_settings()
    except Exception:
        return None


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


def parse_step1_output(
    *,
    strategy_settings: Mapping[str, Any] | None = None,
) -> dict[str, str]:
    """Parse and validate the manual Step 1 output into research_output.json."""
    handoff_strategy_settings = (
        strategy_settings
        if strategy_settings is not None
        else load_strategy_settings_for_handoff_validation()
    )
    try:
        payload = extract_research_json(
            raw_output_path=step1_raw_output_path(),
            output_path=step1_research_output_path(),
            pretty=True,
        )
    except Exception as exc:
        _write_no_output_research_availability_artifacts_report_only(
            strategy_settings=handoff_strategy_settings,
            diagnostic_reason="step1 parse failed before research_output.json was produced.",
            parse_error=str(exc),
        )
        raise

    # Report-only layer 1: validate the raw parsed output as-is.
    handoff_validation = validate_research_handoff(
        payload,
        strategy_settings=handoff_strategy_settings,
    )
    write_json(
        step1_research_handoff_validation_path(),
        research_handoff_validation_result_to_dict(handoff_validation),
    )

    # Report-only layer 2: deterministically normalize a strict-handoff
    # candidate, then validate the candidate. This never mutates
    # research_output.json or the raw handoff validation artifact, and never
    # blocks the pipeline regardless of candidate validity.
    normalization = normalize_research_handoff_candidate(
        payload,
        strategy_settings=handoff_strategy_settings,
    )
    write_json(step1_research_handoff_candidate_path(), normalization.candidate)
    write_json(
        step1_research_handoff_candidate_normalization_path(),
        research_handoff_normalization_result_to_dict(normalization),
    )
    candidate_validation = validate_research_handoff(
        normalization.candidate,
        strategy_settings=handoff_strategy_settings,
    )
    write_json(
        step1_research_handoff_candidate_validation_path(),
        research_handoff_validation_result_to_dict(candidate_validation),
    )

    # Report-only layer 3 (PR B): persist the last-known-good strict handoff to
    # artifacts/state/ only when the candidate is strict-valid. This is a writer
    # only — no downstream step reads it, it never blocks the pipeline, and a
    # writer failure is recorded rather than raised.
    last_good_result = _write_last_good_research_handoff_report_only(
        candidate=normalization.candidate,
        candidate_validation=candidate_validation,
        strategy_settings=handoff_strategy_settings,
        source_as_of_date=payload.get("as_of") if isinstance(payload, Mapping) else None,
    )
    write_json(
        step1_last_good_write_result_path(),
        last_good_research_handoff_write_result_to_dict(last_good_result),
    )

    # Report-only layer 4 (PR C): deterministic research availability /
    # freshness / degraded-mode decision artifacts. This only observes and
    # classifies; it does not gate the pipeline and no downstream step reads it.
    availability = _evaluate_research_availability_report_only(
        candidate=normalization.candidate,
        candidate_validation=candidate_validation,
        strategy_settings=handoff_strategy_settings,
        payload=payload,
    )

    return {
        "research_output_path": str(step1_research_output_path()),
        "research_handoff_validation_path": str(step1_research_handoff_validation_path()),
        "research_handoff_valid": str(handoff_validation.valid),
        "research_handoff_candidate_path": str(step1_research_handoff_candidate_path()),
        "research_handoff_candidate_normalization_path": str(
            step1_research_handoff_candidate_normalization_path()
        ),
        "research_handoff_candidate_validation_path": str(
            step1_research_handoff_candidate_validation_path()
        ),
        "research_handoff_candidate_valid": str(candidate_validation.valid),
        "research_handoff_candidate_source_shape": normalization.source_shape,
        "research_handoff_candidate_normalization_mode": normalization.normalization_mode,
        "last_good_research_handoff_write_result_path": str(step1_last_good_write_result_path()),
        "last_good_research_handoff_written": str(last_good_result.wrote),
        "last_good_research_handoff_path": (
            str(last_good_result.handoff_path) if last_good_result.handoff_path is not None else ""
        ),
        "research_availability_path": str(step1_research_availability_path()),
        "research_freshness_report_path": str(step1_research_freshness_report_path()),
        "research_degraded_mode_decision_path": str(step1_research_degraded_mode_decision_path()),
        "research_availability_state": availability.state,
        "research_availability_fresh": str(availability.fresh_research_available),
        "schema_version": str(payload.get("schema_version", "")),
    }


def _evaluate_research_availability_report_only(
    *,
    candidate: Mapping[str, Any],
    candidate_validation: Any,
    strategy_settings: Mapping[str, Any] | None,
    payload: Mapping[str, Any],
):
    """Evaluate and write report-only availability artifacts defensively.

    Step 1 parse must never fail because of this observability layer, so any
    error is swallowed and recorded into the artifacts as a conservative
    NO_OUTPUT-style decision rather than raised.
    """
    try:
        last_good = read_last_good_research_handoff(step1_state_dir())
        # now_date is the current run's SSOT date (strategy settings as_of),
        # falling back to the parsed research as_of. source_as_of_date is the
        # research as_of for the current handoff candidate.
        settings_as_of = (
            strategy_settings.get("as_of") if isinstance(strategy_settings, Mapping) else None
        )
        payload_as_of = payload.get("as_of") if isinstance(payload, Mapping) else None
        availability = evaluate_research_availability(
            candidate_validation=candidate_validation,
            candidate=candidate,
            strategy_settings=strategy_settings,
            source_as_of_date=payload_as_of,
            now_date=settings_as_of or payload_as_of,
            last_good_handoff=last_good.handoff,
            last_good_metadata=last_good.metadata,
        )
        write_json(
            step1_research_availability_path(),
            research_availability_result_to_dict(availability),
        )
        write_json(
            step1_research_freshness_report_path(),
            research_freshness_report_to_dict(availability),
        )
        write_json(
            step1_research_degraded_mode_decision_path(),
            research_degraded_mode_decision_to_dict(availability),
        )
        return availability
    except Exception as exc:  # noqa: BLE001 - report-only: never break Step 1 parse
        fallback = evaluate_research_availability(
            candidate_validation=None,
            candidate=None,
            strategy_settings=None,
            source_as_of_date=None,
            now_date=None,
        )
        try:
            error_payload = {
                **research_availability_result_to_dict(fallback),
                "evaluator_error": f"availability evaluation failed (report-only, not raised): {exc}",
            }
            write_json(step1_research_availability_path(), error_payload)
            write_json(
                step1_research_freshness_report_path(),
                research_freshness_report_to_dict(fallback),
            )
            write_json(
                step1_research_degraded_mode_decision_path(),
                research_degraded_mode_decision_to_dict(fallback),
            )
        except Exception:  # noqa: BLE001 - best-effort artifact emission
            pass
        return fallback


def _write_no_output_research_availability_artifacts_report_only(
    *,
    strategy_settings: Mapping[str, Any] | None,
    diagnostic_reason: str,
    parse_error: str | None = None,
):
    """Best-effort PR C artifacts for no-output / parse-failure Step 1 runs.

    This preserves the original parser error behavior: callers still raise
    after this report-only observer writes conservative degraded-mode artifacts.
    """
    try:
        last_good = read_last_good_research_handoff(step1_state_dir())
        settings_as_of = (
            strategy_settings.get("as_of") if isinstance(strategy_settings, Mapping) else None
        )
        availability = evaluate_research_availability(
            candidate_validation=None,
            candidate=None,
            strategy_settings=strategy_settings,
            source_as_of_date=None,
            now_date=settings_as_of,
            last_good_handoff=last_good.handoff,
            last_good_metadata=last_good.metadata,
            parsed_output_available=False,
        )
        diagnostic = {
            "diagnostic_reason": diagnostic_reason,
            "parse_error": parse_error,
        }
        write_json(
            step1_research_availability_path(),
            {**research_availability_result_to_dict(availability), **diagnostic},
        )
        write_json(
            step1_research_freshness_report_path(),
            {**research_freshness_report_to_dict(availability), **diagnostic},
        )
        write_json(
            step1_research_degraded_mode_decision_path(),
            {**research_degraded_mode_decision_to_dict(availability), **diagnostic},
        )
        return availability
    except Exception:
        # Best-effort only: never mask the original parse failure.
        return None


def _write_last_good_research_handoff_report_only(
    *,
    candidate: Mapping[str, Any],
    candidate_validation: Any,
    strategy_settings: Mapping[str, Any] | None,
    source_as_of_date: str | None,
) -> LastGoodResearchHandoffWriteResult:
    """Call the last-good writer defensively so Step 1 parse stays report-only.

    source_run_id is genuinely unknown at parse time (the archive label is only
    assigned later by prepare_next_run), so it is passed as None and recorded as
    "unknown" rather than fabricated.
    """
    try:
        return write_last_good_research_handoff_if_valid(
            candidate=candidate,
            candidate_validation=candidate_validation,
            strategy_settings=strategy_settings,
            source_run_id=None,
            source_as_of_date=source_as_of_date,
            output_dir=step1_state_dir(),
        )
    except Exception as exc:  # noqa: BLE001 - report-only: never break Step 1 parse
        return LastGoodResearchHandoffWriteResult(
            wrote=False,
            handoff_path=None,
            metadata_path=None,
            skip_reasons=[f"last-good writer error (report-only, not raised): {exc}"],
            metadata={},
        )
