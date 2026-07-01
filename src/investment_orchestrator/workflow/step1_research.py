"""Manual Step 1 workflow: render prompt and ingest RESEARCH_JSON."""

from __future__ import annotations

from collections.abc import Mapping
import json
import re
from pathlib import Path
from typing import Any

from investment_orchestrator.common.io import (
    ensure_dir,
    file_exists,
    read_json,
    read_text,
    write_json,
    write_text,
)
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
from investment_orchestrator.research.analyst_memo import (
    analyst_memo_parse_result_to_dict,
    evidence_universe_from_packet,
    parse_analyst_memo_text,
    render_analyst_memo_prompt,
)
from investment_orchestrator.research.evidence_packet import write_evidence_packet
from investment_orchestrator.research.handoff_compiler import write_compiled_research_handoff
from investment_orchestrator.state.last_good_research_handoff import (
    LastGoodResearchHandoffWriteResult,
    last_good_research_handoff_metadata_path,
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
EVIDENCE_PACKET_FILENAME = "evidence_packet.json"
ANALYST_MEMO_PROMPT_FILENAME = "analyst_memo_prompt.txt"
ANALYST_MEMO_RAW_OUTPUT_FILENAME = "analyst_memo_raw_output.txt"
ANALYST_MEMO_FILENAME = "analyst_memo.json"
ANALYST_MEMO_VALIDATION_FILENAME = "analyst_memo_validation.json"
COMPILED_HANDOFF_CANDIDATE_FILENAME = "compiled_research_handoff_candidate.json"
COMPILED_HANDOFF_VALIDATION_FILENAME = "compiled_research_handoff_validation.json"
COMPILED_HANDOFF_METADATA_FILENAME = "compiled_research_handoff_metadata.json"
COMPILED_SUPPORT_SIGNALS_FILENAME = "compiled_support_signals.json"
RESEARCH_ANCHORS_INPUT_FILENAME = "research_anchors.yaml"
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


def step1_evidence_packet_path() -> Path:
    """Return the report-only deterministic evidence packet artifact path (R2B)."""
    return step1_artifact_dir() / EVIDENCE_PACKET_FILENAME


def step1_analyst_memo_prompt_path() -> Path:
    """Return the rendered Step 1B analyst-memo prompt path (R2C, report-only)."""
    return step1_artifact_dir() / ANALYST_MEMO_PROMPT_FILENAME


def step1_analyst_memo_raw_output_path() -> Path:
    """Return the manual Step 1B analyst-memo raw output path (R2C, report-only)."""
    return step1_artifact_dir() / ANALYST_MEMO_RAW_OUTPUT_FILENAME


def step1_analyst_memo_path() -> Path:
    """Return the parsed Step 1B analyst-memo artifact path (R2C, report-only)."""
    return step1_artifact_dir() / ANALYST_MEMO_FILENAME


def step1_analyst_memo_validation_path() -> Path:
    """Return the report-only Step 1B analyst-memo validation artifact path (R2C)."""
    return step1_artifact_dir() / ANALYST_MEMO_VALIDATION_FILENAME


def step1_compiled_handoff_candidate_path() -> Path:
    """Return the report-only Step 1C compiled handoff candidate artifact path (R2D)."""
    return step1_artifact_dir() / COMPILED_HANDOFF_CANDIDATE_FILENAME


def step1_compiled_handoff_validation_path() -> Path:
    """Return the report-only Step 1C compiled handoff validation artifact path (R2D)."""
    return step1_artifact_dir() / COMPILED_HANDOFF_VALIDATION_FILENAME


def step1_compiled_handoff_metadata_path() -> Path:
    """Return the report-only Step 1C compiled handoff metadata artifact path (R2D)."""
    return step1_artifact_dir() / COMPILED_HANDOFF_METADATA_FILENAME


def step1_compiled_support_signals_path() -> Path:
    """Return the report-only Step 1C support-signals artifact path (R2E.3)."""
    return step1_artifact_dir() / COMPILED_SUPPORT_SIGNALS_FILENAME


def resolve_step1_prompt_template_path() -> Path:
    """Resolve the formal Step 1 prompt template from prompts/."""
    return require_prompt_path("research_dual_lane.txt")


def resolve_analyst_memo_prompt_template_path() -> Path:
    """Resolve the small Step 1B analyst-memo prompt template from prompts/."""
    return require_prompt_path("analyst_memo.txt")


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


def render_step1_analyst_memo_prompt(
    *,
    strategy_settings: Mapping[str, Any] | None = None,
) -> dict[str, str]:
    """Render the small Step 1B analyst-memo prompt from the evidence packet (R2C).

    Report-only: builds (or reuses) the deterministic ``evidence_packet.json``,
    injects it into the small memo prompt template, and writes
    ``analyst_memo_prompt.txt`` plus a blank ``analyst_memo_raw_output.txt`` for
    the operator to paste the LLM memo into. This neither runs the model nor
    changes any gate, permission, or degraded-mode decision.
    """
    settings = (
        strategy_settings
        if strategy_settings is not None
        else load_strategy_settings_for_handoff_validation()
    )

    packet = _load_or_build_evidence_packet(strategy_settings=settings)
    template = read_text(resolve_analyst_memo_prompt_template_path())
    rendered = render_analyst_memo_prompt(prompt_template=template, evidence_packet=packet)

    prompt_output_path = step1_analyst_memo_prompt_path()
    write_text(prompt_output_path, rendered.rstrip() + "\n")
    raw_output_path = step1_analyst_memo_raw_output_path()
    if not file_exists(raw_output_path):
        write_text(raw_output_path, "")

    return {
        "analyst_memo_prompt_path": str(prompt_output_path),
        "analyst_memo_raw_output_path": str(raw_output_path),
        "evidence_packet_path": str(step1_evidence_packet_path()),
        "analyst_memo_prompt_template_path": str(resolve_analyst_memo_prompt_template_path()),
    }


def parse_step1_analyst_memo_output(
    *,
    strategy_settings: Mapping[str, Any] | None = None,
) -> dict[str, str]:
    """Standalone parse of a pasted analyst-memo output (R2C, report-only).

    Requires ``analyst_memo_raw_output.txt`` to be present; writes
    ``analyst_memo.json`` + ``analyst_memo_validation.json``. Unlike the layer
    embedded in ``parse_step1_output`` (which silently skips when no raw memo
    exists), this CLI-facing entrypoint raises if the raw memo is absent.
    """
    settings = (
        strategy_settings
        if strategy_settings is not None
        else load_strategy_settings_for_handoff_validation()
    )
    raw_path = step1_analyst_memo_raw_output_path()
    if not file_exists(raw_path) or not read_text(raw_path).strip():
        raise FileNotFoundError(
            f"Missing analyst memo raw output: {raw_path}. "
            "Run `run_step1 analyst-memo-render` and paste the memo first."
        )
    result = _run_analyst_memo_parse(strategy_settings=settings)
    assert result is not None  # raw is present per the guard above
    return result


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

    # Report-only layer 0 (R2B): deterministic evidence packet. Built from
    # operator inputs + last-good metadata only (no LLM, no parsed payload), so
    # it is written regardless of whether the Deep Research output parses, and is
    # fully independent of the degraded-mode decision below.
    _write_evidence_packet_report_only(strategy_settings=handoff_strategy_settings)

    # Report-only layer 0b (R2C): small analyst-memo parse/validation. Only runs
    # when a raw memo output exists; it writes its own two artifacts and never
    # gates the pipeline, never feeds the degraded-mode decision, and can never
    # permit NEW_BUY.
    analyst_memo_summary = _parse_analyst_memo_report_only(
        strategy_settings=handoff_strategy_settings
    )

    # Report-only layer 0c (R2D): deterministic strict-handoff compiler. Compiles
    # the evidence packet (+ optional valid analyst memo) into a structurally
    # complete candidate, validates it with the existing validator, and writes
    # compiled_* artifacts. It is NOT fed into research_degraded_mode_decision and
    # never changes allowed_actions; evidence-only / invalid-memo never support
    # NEW_BUY. The raw Deep Research candidate below remains the active source.
    compiled_handoff_summary = _compile_research_handoff_report_only(
        strategy_settings=handoff_strategy_settings
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
        "evidence_packet_path": str(step1_evidence_packet_path()),
        "analyst_memo_present": str(analyst_memo_summary.get("present", False)),
        "analyst_memo_valid": str(analyst_memo_summary.get("valid", False)),
        "analyst_memo_validation_path": analyst_memo_summary.get("validation_path", ""),
        "analyst_memo_path": analyst_memo_summary.get("memo_path", ""),
        "compiled_research_handoff_candidate_path": compiled_handoff_summary.get("candidate_path", ""),
        "compiled_research_handoff_validation_path": compiled_handoff_summary.get("validation_path", ""),
        "compiled_research_handoff_metadata_path": compiled_handoff_summary.get("metadata_path", ""),
        "compiled_support_signals_path": compiled_handoff_summary.get("support_signals_path", ""),
        "compiled_research_handoff_mode": compiled_handoff_summary.get("compilation_mode", ""),
        "compiled_research_handoff_valid": compiled_handoff_summary.get("compiled_candidate_valid", ""),
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
        # R2E.1 (report-only recognition): feed the deterministic compiled
        # evidence-first handoff (Step 1C) so a valid+fresh compiled candidate is
        # recognized as STRICT_FRESH_EVIDENCE_ONLY (HOLD / NO_TRADE only) instead
        # of a misleading INVALID_CONTRACT / DEGRADED_*. This never adds NEW_BUY /
        # ORDER_COMPILATION. Only the normal parse path (parsed output present) is
        # fed compiled inputs; a hard parse failure stays NO_OUTPUT (see the
        # no-output writer, which is intentionally left unchanged).
        compiled_inputs = _compiled_handoff_availability_inputs()
        availability = evaluate_research_availability(
            candidate_validation=candidate_validation,
            candidate=candidate,
            strategy_settings=strategy_settings,
            source_as_of_date=payload_as_of,
            now_date=settings_as_of or payload_as_of,
            last_good_handoff=last_good.handoff,
            last_good_metadata=last_good.metadata,
            compiled_candidate_validation=compiled_inputs["compiled_candidate_validation"],
            compiled_metadata=compiled_inputs["compiled_metadata"],
            compiled_source_as_of_date=settings_as_of,
            compiled_source_artifacts=compiled_inputs["compiled_source_artifacts"],
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


def _write_evidence_packet_report_only(
    *,
    strategy_settings: Mapping[str, Any] | None,
) -> None:
    """Build and write the deterministic evidence packet defensively (R2B).

    Report-only: never raises into the Step 1 parse flow, never gates the
    pipeline, and never feeds the degraded-mode decision. Uses only operator
    inputs + last-good metadata (no LLM, no parsed payload). A missing portfolio
    snapshot becomes an explicit DATA_GAP inside the packet rather than a crash.
    """
    try:
        snapshot_path = current_inputs_dir() / "portfolio_snapshot.txt"
        research_anchors_path = current_inputs_dir() / RESEARCH_ANCHORS_INPUT_FILENAME
        try:
            snapshot_text: str | None = load_portfolio_snapshot_text()
        except Exception:  # noqa: BLE001 - missing snapshot -> DATA_GAP, not crash
            snapshot_text = None
        last_good = read_last_good_research_handoff(step1_state_dir())
        settings_as_of = (
            strategy_settings.get("as_of") if isinstance(strategy_settings, Mapping) else None
        )
        write_evidence_packet(
            output_path=step1_evidence_packet_path(),
            strategy_settings=strategy_settings,
            portfolio_snapshot_text=snapshot_text,
            portfolio_snapshot_path=snapshot_path,
            last_good_available=last_good.available,
            last_good_metadata=last_good.metadata,
            now_date=settings_as_of,
            source_artifacts={
                "strategy_settings": str(current_inputs_dir() / "strategy_settings.yaml"),
                "portfolio_snapshot": str(snapshot_path),
                "last_good_metadata": str(last_good_research_handoff_metadata_path(step1_state_dir())),
                "research_anchors": str(research_anchors_path),
            },
            research_anchors_path=research_anchors_path,
        )
    except Exception:  # noqa: BLE001 - report-only: never break Step 1 parse
        # Best-effort only: do not mask or alter existing Step 1 behavior.
        pass


def _load_or_build_evidence_packet(
    *,
    strategy_settings: Mapping[str, Any] | None,
) -> dict[str, Any]:
    """Ensure the deterministic evidence packet is fresh on disk, then return it.

    Used by the analyst-memo render/parse (R2C). Falls back to an in-memory build
    so rendering/parsing still works even if the disk write/read fails.
    """
    _write_evidence_packet_report_only(strategy_settings=strategy_settings)
    try:
        return read_json(step1_evidence_packet_path())
    except Exception:  # noqa: BLE001 - report-only fallback to in-memory build
        from investment_orchestrator.research.evidence_packet import build_evidence_packet

        try:
            snapshot_text: str | None = load_portfolio_snapshot_text()
        except Exception:  # noqa: BLE001 - missing snapshot -> DATA_GAP, not crash
            snapshot_text = None
        return build_evidence_packet(
            strategy_settings=strategy_settings,
            portfolio_snapshot_text=snapshot_text,
            generated_at=None,
        )


def _run_analyst_memo_parse(
    *,
    strategy_settings: Mapping[str, Any] | None,
) -> dict[str, str] | None:
    """Parse + validate a pasted analyst memo and write its two report-only artifacts.

    Returns ``None`` when no raw memo output exists (treated as absent, not an
    error). The evidence universe comes from the deterministic evidence packet;
    the memo can only express a relative view inside that universe.
    """
    raw_path = step1_analyst_memo_raw_output_path()
    if not file_exists(raw_path):
        return None
    raw_text = read_text(raw_path)
    if not raw_text.strip():
        return None

    try:
        packet = read_json(step1_evidence_packet_path())
    except Exception:  # noqa: BLE001 - build the packet if it is not on disk yet
        packet = _load_or_build_evidence_packet(strategy_settings=strategy_settings)
    universe = evidence_universe_from_packet(packet)

    result = parse_analyst_memo_text(raw_text, evidence_universe=universe)
    if isinstance(result.memo, Mapping):
        write_json(step1_analyst_memo_path(), dict(result.memo))
    else:
        write_json(
            step1_analyst_memo_path(),
            {
                "schema_version": "analyst_memo_v1",
                "present": result.present,
                "valid": result.valid,
                "note": "no parseable analyst_memo object (see analyst_memo_validation.json).",
                "parse_error": result.parse_error,
            },
        )
    write_json(
        step1_analyst_memo_validation_path(),
        analyst_memo_parse_result_to_dict(result),
    )
    return {
        "present": str(result.present),
        "valid": str(result.valid),
        "memo_path": str(step1_analyst_memo_path()),
        "validation_path": str(step1_analyst_memo_validation_path()),
    }


def _parse_analyst_memo_report_only(
    *,
    strategy_settings: Mapping[str, Any] | None,
) -> dict[str, Any]:
    """Run the analyst-memo parse defensively as a report-only layer (R2C).

    Step 1 parse must never fail because of this observer, and the memo must
    never change the degraded-mode decision or any allowed action. A missing raw
    memo is simply skipped; any error is swallowed.
    """
    absent = {"present": False, "valid": False, "validation_path": "", "memo_path": ""}
    try:
        result = _run_analyst_memo_parse(strategy_settings=strategy_settings)
        return result if result is not None else absent
    except Exception:  # noqa: BLE001 - report-only: never break Step 1 parse
        return absent


def _read_json_if_exists(path: Path) -> Any | None:
    """Read a JSON artifact if present; return None when absent or unreadable."""
    if not file_exists(path):
        return None
    try:
        return read_json(path)
    except Exception:  # noqa: BLE001 - report-only: a malformed artifact is treated as absent
        return None


def _compiled_handoff_availability_inputs() -> dict[str, Any]:
    """Load the R2D compiled-handoff validation + metadata for the availability evaluator.

    Report-only: a missing / malformed compiled artifact is treated as absent, so
    the evaluator falls back to its pre-R2E.1 behavior (no relabel).
    """
    return {
        "compiled_candidate_validation": _read_json_if_exists(step1_compiled_handoff_validation_path()),
        "compiled_metadata": _read_json_if_exists(step1_compiled_handoff_metadata_path()),
        "compiled_source_artifacts": {
            "compiled_research_handoff_candidate": str(step1_compiled_handoff_candidate_path()),
            "compiled_research_handoff_validation": str(step1_compiled_handoff_validation_path()),
            "compiled_research_handoff_metadata": str(step1_compiled_handoff_metadata_path()),
        },
    }


def _load_analyst_memo_for_compiler() -> Mapping[str, Any] | None:
    """Read the parsed analyst memo artifact for the compiler, if present.

    The compiler re-validates whatever it is given, so a stub / invalid memo is
    safely classified as ``invalid_memo_ignored``. A missing memo file means the
    compiler runs in ``evidence_only`` mode.
    """
    memo_path = step1_analyst_memo_path()
    if not file_exists(memo_path):
        return None
    try:
        memo = read_json(memo_path)
    except Exception:  # noqa: BLE001 - unreadable memo -> treated as absent
        return None
    return memo if isinstance(memo, Mapping) else None


def _run_compile_research_handoff(
    *,
    strategy_settings: Mapping[str, Any] | None,
) -> dict[str, str]:
    """Compile + validate + write the three report-only R2D artifacts."""
    packet = _load_or_build_evidence_packet(strategy_settings=strategy_settings)
    analyst_memo = _load_analyst_memo_for_compiler()
    result = write_compiled_research_handoff(
        candidate_path=step1_compiled_handoff_candidate_path(),
        validation_path=step1_compiled_handoff_validation_path(),
        metadata_path=step1_compiled_handoff_metadata_path(),
        evidence_packet=packet,
        analyst_memo=analyst_memo,
        strategy_settings=strategy_settings,
        evidence_packet_path=str(step1_evidence_packet_path()),
        analyst_memo_path=str(step1_analyst_memo_path()) if analyst_memo is not None else None,
        support_signals_path=step1_compiled_support_signals_path(),
    )
    return {
        "candidate_path": result["compiled_research_handoff_candidate_path"],
        "validation_path": result["compiled_research_handoff_validation_path"],
        "metadata_path": result["compiled_research_handoff_metadata_path"],
        "support_signals_path": result.get("compiled_support_signals_path", ""),
        "compilation_mode": result["compilation_mode"],
        "compiled_candidate_valid": result["compiled_candidate_valid"],
    }


def _compile_research_handoff_report_only(
    *,
    strategy_settings: Mapping[str, Any] | None,
) -> dict[str, Any]:
    """Run the deterministic handoff compiler defensively as a report-only layer (R2D).

    Step 1 parse must never fail because of this observer, and the compiled
    candidate must never change the degraded-mode decision or any allowed action
    (it is not fed into the availability evaluator). Any error is swallowed.
    """
    empty = {
        "candidate_path": "",
        "validation_path": "",
        "metadata_path": "",
        "support_signals_path": "",
        "compilation_mode": "",
        "compiled_candidate_valid": "",
    }
    try:
        return _run_compile_research_handoff(strategy_settings=strategy_settings)
    except Exception:  # noqa: BLE001 - report-only: never break Step 1 parse
        return empty


def compile_step1_research_handoff(
    *,
    strategy_settings: Mapping[str, Any] | None = None,
) -> dict[str, str]:
    """Standalone deterministic handoff compile (R2D, report-only) for the CLI.

    Builds/reuses the evidence packet, reads the parsed analyst memo if present,
    compiles the strict candidate, validates it, and writes the compiled_*
    artifacts. Report-only: not fed into the degraded-mode decision.
    """
    settings = (
        strategy_settings
        if strategy_settings is not None
        else load_strategy_settings_for_handoff_validation()
    )
    return _run_compile_research_handoff(strategy_settings=settings)


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
