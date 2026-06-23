"""Report-only last-known-good (LKG) research handoff state writer.

Roadmap PR B of the Deep Research degraded-mode design
(``docs/deep_research_degraded_mode_design.md``).

This module persists the most recent **strict-validated** research handoff
candidate into ``artifacts/state/`` so a future degraded-mode layer can fall
back to it. It is strictly a *writer*:

* It writes only when the candidate passed strict handoff validation.
* An invalid / narrative / unrecoverable candidate is skipped and the existing
  last-good state is left untouched (never cleared or overwritten).
* It never fabricates missing provenance; unknown values are recorded as
  ``"unknown"``.
* No downstream step reads this state in PR B; nothing here gates the pipeline.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
from typing import Any

from investment_orchestrator.common.io import read_json, write_json
from investment_orchestrator.common.paths import repo_root
from investment_orchestrator.validators.validate_research_handoff import (
    ResearchHandoffValidationResult,
    research_handoff_validation_result_to_dict,
)


LAST_GOOD_HANDOFF_FILENAME = "last_good_research_handoff.json"
LAST_GOOD_HANDOFF_METADATA_FILENAME = "last_good_research_handoff_metadata.json"

HANDOFF_SOURCE = "research_handoff_candidate"
UNKNOWN = "unknown"

# Only decision-relevant strategy settings contribute to the hash. Timestamps,
# run ids, as_of dates, prompt text, and other non-decision metadata are
# intentionally excluded so that re-running with a fresh date does not change
# the hash, while a real universe / cap change does.
DECISION_RELEVANT_SETTINGS_KEYS = (
    "core_universe",
    "satellite_universe",
    "user_approved_extended_etf_static_list",
    "extended_etf_constraints",
    "active_shortlist_size_rule",
    "hard_cap_open_orders_budget",
    "max_new_tickers_per_week",
)
# Subset whose absence is worth flagging explicitly in metadata.
REQUIRED_DECISION_RELEVANT_KEYS = ("core_universe", "satellite_universe")


@dataclass(frozen=True)
class LastGoodResearchHandoffReadResult:
    """Outcome of reading the persisted last-known-good handoff (never raises)."""

    available: bool
    handoff: dict[str, Any] | None = None
    metadata: dict[str, Any] | None = None
    read_errors: list[str] = field(default_factory=list)


def read_last_good_research_handoff(
    state_dir: Path | None = None,
) -> LastGoodResearchHandoffReadResult:
    """Read the persisted last-known-good handoff + metadata.

    Report-only and defensive: missing or malformed files never raise; they
    produce an ``available=False`` result with recorded read errors. The
    handoff is considered available only when both files parse to JSON objects.
    """
    handoff_path = last_good_research_handoff_path(state_dir)
    metadata_path = last_good_research_handoff_metadata_path(state_dir)
    read_errors: list[str] = []

    handoff = _read_json_object(handoff_path, "last_good_research_handoff.json", read_errors)
    metadata = _read_json_object(
        metadata_path, "last_good_research_handoff_metadata.json", read_errors
    )

    available = handoff is not None and metadata is not None
    return LastGoodResearchHandoffReadResult(
        available=available,
        handoff=handoff,
        metadata=metadata,
        read_errors=read_errors,
    )


def _read_json_object(path: Path, label: str, read_errors: list[str]) -> dict[str, Any] | None:
    if not path.exists():
        read_errors.append(f"{label} not found.")
        return None
    try:
        payload = read_json(path)
    except Exception as exc:  # noqa: BLE001 - report-only: malformed files never raise
        read_errors.append(f"{label} could not be parsed: {exc}")
        return None
    if not isinstance(payload, dict):
        read_errors.append(f"{label} is not a JSON object.")
        return None
    return payload


@dataclass(frozen=True)
class LastGoodResearchHandoffWriteResult:
    """Outcome of an attempt to persist the last-known-good handoff."""

    wrote: bool
    handoff_path: Path | None
    metadata_path: Path | None
    skip_reasons: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)


def last_good_research_handoff_write_result_to_dict(
    result: LastGoodResearchHandoffWriteResult,
) -> dict[str, Any]:
    """Serialize a write result for a stable, report-only per-run artifact."""
    return {
        "wrote": result.wrote,
        "handoff_path": str(result.handoff_path) if result.handoff_path is not None else None,
        "metadata_path": str(result.metadata_path) if result.metadata_path is not None else None,
        "skip_reasons": list(result.skip_reasons),
        "metadata": result.metadata,
    }


def default_state_dir() -> Path:
    """Return the persistent state directory (survives ``prepare_next_run``)."""
    return repo_root() / "artifacts" / "state"


def last_good_research_handoff_path(state_dir: Path | None = None) -> Path:
    """Return the last-known-good handoff artifact path."""
    return (state_dir or default_state_dir()) / LAST_GOOD_HANDOFF_FILENAME


def last_good_research_handoff_metadata_path(state_dir: Path | None = None) -> Path:
    """Return the last-known-good handoff metadata artifact path."""
    return (state_dir or default_state_dir()) / LAST_GOOD_HANDOFF_METADATA_FILENAME


def decision_relevant_settings(
    strategy_settings: Mapping[str, Any] | None,
) -> dict[str, Any] | None:
    """Extract only the decision-relevant settings subset used for hashing."""
    if not isinstance(strategy_settings, Mapping):
        return None
    return {
        key: strategy_settings[key]
        for key in DECISION_RELEVANT_SETTINGS_KEYS
        if key in strategy_settings
    }


def strategy_settings_hash(subset: Mapping[str, Any] | None) -> str | None:
    """Hash the decision-relevant subset with stable, key-sorted serialization."""
    if subset is None:
        return None
    serialized = json.dumps(
        subset,
        sort_keys=True,
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def write_last_good_research_handoff_if_valid(
    *,
    candidate: Mapping[str, Any],
    candidate_validation: ResearchHandoffValidationResult,
    strategy_settings: Mapping[str, Any] | None,
    source_run_id: str | None,
    source_as_of_date: str | None,
    output_dir: Path | None = None,
    now: datetime | None = None,
) -> LastGoodResearchHandoffWriteResult:
    """Persist the candidate as last-known-good only if it is strict-valid.

    Report-only: an invalid candidate is skipped without raising and without
    touching any existing last-good state.
    """
    skip_reasons: list[str] = []
    if not isinstance(candidate, Mapping):
        skip_reasons.append("candidate is not a JSON object; last-good not written.")
    if not getattr(candidate_validation, "valid", False):
        skip_reasons.append(
            "candidate strict handoff validation is invalid (valid=false); "
            "last-good not written and any existing last-good is preserved."
        )

    safe_candidate: Mapping[str, Any] = candidate if isinstance(candidate, Mapping) else {}
    metadata = _build_metadata(
        candidate=safe_candidate,
        candidate_validation=candidate_validation,
        strategy_settings=strategy_settings,
        source_run_id=source_run_id,
        source_as_of_date=source_as_of_date,
        now=now,
    )

    if skip_reasons:
        return LastGoodResearchHandoffWriteResult(
            wrote=False,
            handoff_path=None,
            metadata_path=None,
            skip_reasons=skip_reasons,
            metadata=metadata,
        )

    state_dir = output_dir or default_state_dir()
    handoff_path = last_good_research_handoff_path(state_dir)
    metadata_path = last_good_research_handoff_metadata_path(state_dir)
    write_json(handoff_path, dict(candidate))
    write_json(metadata_path, metadata)
    return LastGoodResearchHandoffWriteResult(
        wrote=True,
        handoff_path=handoff_path,
        metadata_path=metadata_path,
        skip_reasons=[],
        metadata=metadata,
    )


def _build_metadata(
    *,
    candidate: Mapping[str, Any],
    candidate_validation: ResearchHandoffValidationResult,
    strategy_settings: Mapping[str, Any] | None,
    source_run_id: str | None,
    source_as_of_date: str | None,
    now: datetime | None,
) -> dict[str, Any]:
    written_at = (now or datetime.now(timezone.utc)).isoformat()
    subset = decision_relevant_settings(strategy_settings)
    settings_available = subset is not None
    missing_required = (
        [key for key in REQUIRED_DECISION_RELEVANT_KEYS if key not in (subset or {})]
        if settings_available
        else list(REQUIRED_DECISION_RELEVANT_KEYS)
    )

    return {
        "source_run_id": source_run_id if source_run_id else UNKNOWN,
        "source_as_of_date": source_as_of_date if source_as_of_date else UNKNOWN,
        "written_at": written_at,
        "strategy_settings_available": settings_available,
        "strategy_settings_hash": strategy_settings_hash(subset),
        "strategy_settings_hash_inputs": subset if subset is not None else {},
        "missing_decision_relevant_settings_keys": missing_required,
        "universe": {
            "core_universe": _string_list((strategy_settings or {}).get("core_universe")),
            "satellite_universe": _string_list((strategy_settings or {}).get("satellite_universe")),
            "allowed_buy_tickers": _allowed_buy_tickers(candidate),
        },
        "validation_result": research_handoff_validation_result_to_dict(candidate_validation),
        "handoff_source": HANDOFF_SOURCE,
        "schema_version": _schema_version(candidate),
        "report_only": True,
    }


def _allowed_buy_tickers(candidate: Mapping[str, Any]) -> list[str]:
    trade_universe = candidate.get("trade_universe")
    if isinstance(trade_universe, Mapping):
        return _string_list(trade_universe.get("allowed_buy_tickers"))
    return []


def _schema_version(candidate: Mapping[str, Any]) -> str:
    value = candidate.get("schema_version")
    return value if isinstance(value, str) else ""


def _string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, str)]
