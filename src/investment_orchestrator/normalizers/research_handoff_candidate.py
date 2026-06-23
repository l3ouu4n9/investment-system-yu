"""Deterministic, report-only normalization of Step 1 research output.

This layer produces a *candidate* strict research handoff from a parsed
``research_output.json`` payload so the existing strict validator
(:func:`validate_research_handoff`) can be run against a normalized shape as
well as the raw shape. It exists to measure how far deterministic
normalization can recover real runs; it is **not** a gate and never blocks the
pipeline.

Hard rule: the normalizer never hallucinates investment content. It only

* copies existing fields through,
* unwraps known wrapper shapes (``RESEARCH_JSON`` envelopes),
* renames known legacy containers (verbatim moves, target-absent only),
* preserves explicit empty arrays / disabled gate states.

It must never infer a ``trade_universe`` from prose, synthesize a
``buy_universe_scorecard`` / ``scheduled_events``, or fill missing rationale /
actionability / gate-reason fields to make the validator pass. When the
required strict data is genuinely absent the normalizer emits an invalid
candidate with clear diagnostics instead.
"""

from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
from dataclasses import dataclass, field
from typing import Any

from investment_orchestrator.validators.validate_research_handoff import (
    REQUIRED_TOP_LEVEL_FIELDS,
)


# Recognized source shapes for the parsed research output.
SOURCE_SHAPE_STRICT = "strict"
SOURCE_SHAPE_WRAPPED_RESEARCH_JSON = "wrapped_RESEARCH_JSON"
SOURCE_SHAPE_LEGACY_STRUCTURED = "legacy_structured"
SOURCE_SHAPE_NARRATIVE_LANES = "narrative_lanes"
SOURCE_SHAPE_UNKNOWN = "unknown"

# Normalization modes describe what the normalizer was able to do.
NORMALIZATION_MODE_COPY_THROUGH = "copy_through"
NORMALIZATION_MODE_UNWRAP = "unwrap"
NORMALIZATION_MODE_LEGACY = "legacy_normalization"
NORMALIZATION_MODE_UNRECOVERABLE = "unrecoverable"

# Defining marker of a v1 strict handoff payload.
_STRICT_HANDOFF_KEY = "strategy_a_research_handoff"
# Known wrapper envelope key.
_WRAPPER_KEY = "RESEARCH_JSON"
# Narrative / markdown lane keys (prose, not structured handoff data).
_LANE_KEYS = ("lane_a", "lane_A", "lane_b", "lane_B")
# Structured (non-prose) handoff signals used to recognize legacy outputs.
_STRUCTURED_HANDOFF_SIGNAL_KEYS = ("trade_universe", "buy_universe_scorecard")

# Legacy container renames: ``legacy name -> strict name``. Applied only as a
# verbatim move of an existing field and only when the strict target is absent.
# Renaming never reshapes the value, so a shape-mismatched legacy container is
# still rejected by the strict validator rather than silently "passing".
_LEGACY_CONTAINER_ALIASES = {
    "strategy_a_handoff": "strategy_a_research_handoff",
}


@dataclass(frozen=True)
class ResearchHandoffNormalizationResult:
    """Structured result of report-only research handoff normalization."""

    candidate: dict[str, Any]
    source_shape: str
    normalization_mode: str
    applied_transforms: list[str] = field(default_factory=list)
    missing_or_unrecoverable_fields: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def research_handoff_normalization_result_to_dict(
    result: ResearchHandoffNormalizationResult,
) -> dict[str, Any]:
    """Serialize normalization *metadata* (without the candidate body).

    The candidate object is written to its own artifact; this dict is the
    normalization-diagnostics layer kept separate from both the raw research
    output and the candidate validation result.
    """
    return {
        "source_shape": result.source_shape,
        "normalization_mode": result.normalization_mode,
        "applied_transforms": list(result.applied_transforms),
        "missing_or_unrecoverable_fields": list(result.missing_or_unrecoverable_fields),
        "warnings": list(result.warnings),
    }


def normalize_research_handoff_candidate(
    payload: Mapping[str, Any],
    *,
    strategy_settings: Mapping[str, Any] | None = None,
) -> ResearchHandoffNormalizationResult:
    """Produce a strict-handoff candidate from a parsed research output.

    ``strategy_settings`` is accepted for API symmetry with the validator and
    future use. It is intentionally **not** used to inject tickers or other
    settings-derived values into the candidate: doing so would fabricate
    investment content the research output did not produce.
    """
    if not isinstance(payload, Mapping):
        return ResearchHandoffNormalizationResult(
            candidate={},
            source_shape=SOURCE_SHAPE_UNKNOWN,
            normalization_mode=NORMALIZATION_MODE_UNRECOVERABLE,
            applied_transforms=[],
            missing_or_unrecoverable_fields=list(REQUIRED_TOP_LEVEL_FIELDS),
            warnings=["research output payload is not a JSON object; nothing to normalize."],
        )

    shape = _detect_source_shape(payload)
    if shape == SOURCE_SHAPE_WRAPPED_RESEARCH_JSON:
        return _normalize_wrapped(payload)
    if shape == SOURCE_SHAPE_STRICT:
        return _normalize_strict(payload)
    if shape == SOURCE_SHAPE_LEGACY_STRUCTURED:
        return _normalize_legacy(payload)
    if shape == SOURCE_SHAPE_NARRATIVE_LANES:
        return _normalize_narrative(payload)
    return _normalize_unknown(payload)


def _detect_source_shape(payload: Mapping[str, Any]) -> str:
    if isinstance(payload.get(_WRAPPER_KEY), Mapping):
        return SOURCE_SHAPE_WRAPPED_RESEARCH_JSON
    if isinstance(payload.get(_STRICT_HANDOFF_KEY), Mapping):
        return SOURCE_SHAPE_STRICT
    if _has_structured_handoff_signal(payload):
        return SOURCE_SHAPE_LEGACY_STRUCTURED
    if any(key in payload for key in _LANE_KEYS):
        return SOURCE_SHAPE_NARRATIVE_LANES
    return SOURCE_SHAPE_UNKNOWN


def _has_structured_handoff_signal(payload: Mapping[str, Any]) -> bool:
    if isinstance(payload.get("trade_universe"), Mapping):
        return True
    if any(key in payload for key in _LEGACY_CONTAINER_ALIASES):
        return True
    return any(key in payload for key in _STRUCTURED_HANDOFF_SIGNAL_KEYS)


def _normalize_strict(payload: Mapping[str, Any]) -> ResearchHandoffNormalizationResult:
    candidate = deepcopy(dict(payload))
    return ResearchHandoffNormalizationResult(
        candidate=candidate,
        source_shape=SOURCE_SHAPE_STRICT,
        normalization_mode=NORMALIZATION_MODE_COPY_THROUGH,
        applied_transforms=["copy_through"],
        missing_or_unrecoverable_fields=_missing_top_level_fields(candidate),
        warnings=[],
    )


def _normalize_wrapped(payload: Mapping[str, Any]) -> ResearchHandoffNormalizationResult:
    candidate = deepcopy(dict(payload[_WRAPPER_KEY]))
    warnings: list[str] = []
    missing = _missing_top_level_fields(candidate)
    if missing:
        warnings.append(
            "unwrapped RESEARCH_JSON envelope but the inner object still lacks required "
            f"strict handoff fields; candidate remains invalid: {missing}."
        )
    return ResearchHandoffNormalizationResult(
        candidate=candidate,
        source_shape=SOURCE_SHAPE_WRAPPED_RESEARCH_JSON,
        normalization_mode=NORMALIZATION_MODE_UNWRAP,
        applied_transforms=["unwrap_RESEARCH_JSON"],
        missing_or_unrecoverable_fields=missing,
        warnings=warnings,
    )


def _normalize_legacy(payload: Mapping[str, Any]) -> ResearchHandoffNormalizationResult:
    candidate = deepcopy(dict(payload))
    applied_transforms = ["copy_existing_fields"]
    warnings: list[str] = []

    for legacy_name, strict_name in _LEGACY_CONTAINER_ALIASES.items():
        if legacy_name in candidate and strict_name not in candidate:
            candidate[strict_name] = candidate.pop(legacy_name)
            applied_transforms.append(f"rename_legacy_field:{legacy_name}->{strict_name}")
            warnings.append(
                f"renamed legacy container '{legacy_name}' to '{strict_name}' (verbatim move); "
                "the legacy shape is validated as-is and may not satisfy strict handoff v1."
            )

    missing = _missing_top_level_fields(candidate)
    if missing:
        warnings.append(
            "legacy normalization moved/renamed existing fields only; strict handoff fields "
            f"remain absent and were not synthesized: {missing}."
        )
    return ResearchHandoffNormalizationResult(
        candidate=candidate,
        source_shape=SOURCE_SHAPE_LEGACY_STRUCTURED,
        normalization_mode=NORMALIZATION_MODE_LEGACY,
        applied_transforms=applied_transforms,
        missing_or_unrecoverable_fields=missing,
        warnings=warnings,
    )


def _normalize_narrative(payload: Mapping[str, Any]) -> ResearchHandoffNormalizationResult:
    candidate = deepcopy(dict(payload))
    warnings = [
        "payload is dominated by narrative / markdown lane content; the deterministic "
        "normalizer does not infer trade_universe, buy_universe_scorecard, scheduled_events, "
        "or strategy_a_research_handoff from prose.",
    ]
    if "base_universe" in candidate:
        warnings.append(
            "base_universe is present as a bare ticker list but was not promoted to "
            "trade_universe.allowed_buy_tickers; deterministic normalization does not construct "
            "trade_universe from a bare list that lacks scorecard / handoff structure."
        )
    return ResearchHandoffNormalizationResult(
        candidate=candidate,
        source_shape=SOURCE_SHAPE_NARRATIVE_LANES,
        normalization_mode=NORMALIZATION_MODE_UNRECOVERABLE,
        applied_transforms=["copy_existing_fields"],
        missing_or_unrecoverable_fields=_missing_top_level_fields(candidate),
        warnings=warnings,
    )


def _normalize_unknown(payload: Mapping[str, Any]) -> ResearchHandoffNormalizationResult:
    candidate = deepcopy(dict(payload))
    return ResearchHandoffNormalizationResult(
        candidate=candidate,
        source_shape=SOURCE_SHAPE_UNKNOWN,
        normalization_mode=NORMALIZATION_MODE_UNRECOVERABLE,
        applied_transforms=["copy_existing_fields"],
        missing_or_unrecoverable_fields=_missing_top_level_fields(candidate),
        warnings=[
            "payload does not match any known strict / wrapped / legacy / narrative shape; "
            "no deterministic normalization is available.",
        ],
    )


def _missing_top_level_fields(candidate: Mapping[str, Any]) -> list[str]:
    return [field_name for field_name in REQUIRED_TOP_LEVEL_FIELDS if field_name not in candidate]
