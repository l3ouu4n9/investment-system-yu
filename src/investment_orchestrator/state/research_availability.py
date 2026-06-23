"""Report-only research availability / freshness / degraded-mode evaluator.

Roadmap PR C of the Deep Research degraded-mode design
(``docs/deep_research_degraded_mode_design.md``).

This module is a deterministic *observer*. Given the current run's strict
handoff candidate validation, the strategy settings, and any persisted
last-known-good (LKG) handoff, it classifies a research-availability state and
emits a deterministic action permission (allowed / blocked actions). It does
**not** gate the pipeline, does not let any downstream step read its output, and
never raises on degraded input.

Key safety property: missing / stale / invalid research can never yield
``NEW_BUY``; ``HOLD`` / ``NO_TRADE`` are always allowed.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import date
from typing import Any

from investment_orchestrator.state.last_good_research_handoff import (
    decision_relevant_settings,
    strategy_settings_hash,
)


# --- states ------------------------------------------------------------------
STRICT_FRESH = "STRICT_FRESH"
STRICT_STALE = "STRICT_STALE"
DEGRADED_WITH_LAST_GOOD = "DEGRADED_WITH_LAST_GOOD"
DEGRADED_NO_RESEARCH = "DEGRADED_NO_RESEARCH"
INVALID_CONTRACT = "INVALID_CONTRACT"
NO_OUTPUT = "NO_OUTPUT"
MANUAL_REVIEW_REQUIRED = "MANUAL_REVIEW_REQUIRED"

# --- actions -----------------------------------------------------------------
ACTIONS = (
    "HOLD",
    "NO_TRADE",
    "SELL",
    "NEW_BUY",
    "ROTATION",
    "REBALANCE",
    "EXTENDED_ETF_ADMISSION",
    "ORDER_COMPILATION",
)

# Allowed action set per state. Default-deny for order-generating actions;
# HOLD / NO_TRADE are always allowed. Blocked actions are derived as the
# complement, preserving ACTIONS order.
_ALLOWED_ACTIONS_BY_STATE: dict[str, tuple[str, ...]] = {
    STRICT_FRESH: ACTIONS,
    STRICT_STALE: ("HOLD", "NO_TRADE", "SELL"),
    DEGRADED_WITH_LAST_GOOD: ("HOLD", "NO_TRADE"),
    DEGRADED_NO_RESEARCH: ("HOLD", "NO_TRADE"),
    INVALID_CONTRACT: ("HOLD", "NO_TRADE"),
    NO_OUTPUT: ("HOLD", "NO_TRADE"),
    MANUAL_REVIEW_REQUIRED: ("HOLD", "NO_TRADE"),
}

# --- stale policy ------------------------------------------------------------
# age <= fresh_days       -> fresh
# fresh_days < age <= stale_days -> stale
# age > stale_days        -> too_old (manual review)
DEFAULT_STALE_POLICY: dict[str, int] = {"fresh_days": 8, "stale_days": 16}


@dataclass(frozen=True)
class ResearchAvailabilityResult:
    """Deterministic, report-only research availability / permission decision."""

    state: str
    research_availability: str
    fresh_research_available: bool
    handoff_valid: bool
    handoff_stale: bool
    handoff_age_days: int | None
    stale_label: str
    last_good_available: bool
    last_good_usable: bool
    last_good_age_days: int | None
    settings_hash_match: bool | None
    universe_match: bool | None
    allowed_actions: list[str]
    blocked_actions: list[str]
    manual_review_required: bool
    blocker_reasons: list[str] = field(default_factory=list)
    non_blocker_reasons: list[str] = field(default_factory=list)
    fresh_days: int = DEFAULT_STALE_POLICY["fresh_days"]
    stale_days: int = DEFAULT_STALE_POLICY["stale_days"]
    source_as_of_date: str | None = None
    now_date: str | None = None
    last_good_as_of_date: str | None = None


def evaluate_research_availability(
    *,
    candidate_validation: Any | None,
    candidate: Mapping[str, Any] | None,
    strategy_settings: Mapping[str, Any] | None,
    source_as_of_date: str | None,
    now_date: str | None = None,
    last_good_handoff: Mapping[str, Any] | None = None,
    last_good_metadata: Mapping[str, Any] | None = None,
    stale_policy: Mapping[str, Any] | None = None,
    parsed_output_available: bool | None = None,
) -> ResearchAvailabilityResult:
    """Classify research availability and derive a deterministic permission.

    ``parsed_output_available`` disambiguates ``INVALID_CONTRACT`` /
    ``DEGRADED_NO_RESEARCH`` / ``NO_OUTPUT`` when there is no usable candidate;
    when omitted it is inferred from whether a candidate object was supplied.
    """
    fresh_days, stale_days = _resolve_stale_policy(stale_policy)

    handoff_valid = _validation_is_valid(candidate_validation)
    candidate_present = isinstance(candidate, Mapping)
    output_present = parsed_output_available if parsed_output_available is not None else candidate_present

    handoff_age_days = _age_days(now_date, source_as_of_date)

    last_good_available = isinstance(last_good_handoff, Mapping) and isinstance(last_good_metadata, Mapping)
    last_good_as_of_date = (
        _str_or_none(last_good_metadata.get("source_as_of_date")) if last_good_available else None
    )
    last_good_age_days = _age_days(now_date, last_good_as_of_date) if last_good_available else None

    current_hash = strategy_settings_hash(decision_relevant_settings(strategy_settings))
    current_universe = _current_universe_set(strategy_settings)

    blocker_reasons: list[str] = []
    non_blocker_reasons: list[str] = []

    if handoff_valid:
        settings_hash_match: bool | None = True
        universe_match: bool | None = True
        state = _classify_current_valid(
            handoff_age_days, fresh_days, stale_days, blocker_reasons, non_blocker_reasons
        )
    else:
        last_good_hash = (
            _str_or_none(last_good_metadata.get("strategy_settings_hash"))
            if last_good_available
            else None
        )
        last_good_universe = _last_good_universe_set(last_good_metadata) if last_good_available else None
        settings_hash_match = (
            (current_hash == last_good_hash)
            if (last_good_available and current_hash is not None and last_good_hash is not None)
            else (None if not last_good_available else False)
        )
        universe_match = (
            (current_universe == last_good_universe)
            if (last_good_available and current_universe is not None and last_good_universe is not None)
            else (None if not last_good_available else False)
        )
        state = _classify_fallback(
            candidate_present=candidate_present,
            output_present=output_present,
            last_good_available=last_good_available,
            last_good_age_days=last_good_age_days,
            stale_days=stale_days,
            universe_match=universe_match,
            settings_hash_match=settings_hash_match,
            blocker_reasons=blocker_reasons,
            non_blocker_reasons=non_blocker_reasons,
        )

    last_good_usable = state == DEGRADED_WITH_LAST_GOOD
    age_for_label = handoff_age_days if handoff_valid else (last_good_age_days if last_good_available else None)
    stale_label = _stale_label(age_for_label, fresh_days, stale_days)

    allowed_actions = list(_ALLOWED_ACTIONS_BY_STATE[state])
    blocked_actions = [action for action in ACTIONS if action not in allowed_actions]
    manual_review_required = state == MANUAL_REVIEW_REQUIRED

    return ResearchAvailabilityResult(
        state=state,
        research_availability=state.lower(),
        fresh_research_available=state == STRICT_FRESH,
        handoff_valid=handoff_valid,
        handoff_stale=state == STRICT_STALE,
        handoff_age_days=handoff_age_days,
        stale_label=stale_label,
        last_good_available=last_good_available,
        last_good_usable=last_good_usable,
        last_good_age_days=last_good_age_days,
        settings_hash_match=settings_hash_match,
        universe_match=universe_match,
        allowed_actions=allowed_actions,
        blocked_actions=blocked_actions,
        manual_review_required=manual_review_required,
        blocker_reasons=blocker_reasons,
        non_blocker_reasons=non_blocker_reasons,
        fresh_days=fresh_days,
        stale_days=stale_days,
        source_as_of_date=source_as_of_date,
        now_date=now_date,
        last_good_as_of_date=last_good_as_of_date,
    )


def _classify_current_valid(
    age: int | None,
    fresh_days: int,
    stale_days: int,
    blocker_reasons: list[str],
    non_blocker_reasons: list[str],
) -> str:
    if age is not None and age > stale_days:
        blocker_reasons.append(
            f"current strict handoff is valid but too old ({age}d > {stale_days}d); manual review required."
        )
        return MANUAL_REVIEW_REQUIRED
    if age is not None and age <= fresh_days:
        return STRICT_FRESH
    if age is None:
        blocker_reasons.append(
            "current strict handoff is valid but its age is unknown (missing as_of / now_date); "
            "treated as stale (no new buy)."
        )
    else:
        non_blocker_reasons.append(
            f"current strict handoff is valid but stale ({age}d > {fresh_days}d); new buy not permitted."
        )
    return STRICT_STALE


def _classify_fallback(
    *,
    candidate_present: bool,
    output_present: bool,
    last_good_available: bool,
    last_good_age_days: int | None,
    stale_days: int,
    universe_match: bool | None,
    settings_hash_match: bool | None,
    blocker_reasons: list[str],
    non_blocker_reasons: list[str],
) -> str:
    if last_good_available:
        if last_good_age_days is not None and last_good_age_days > stale_days:
            blocker_reasons.append(
                f"no fresh valid handoff and last-known-good is too old "
                f"({last_good_age_days}d > {stale_days}d); manual review required."
            )
            return MANUAL_REVIEW_REQUIRED
        if universe_match is False:
            blocker_reasons.append(
                "no fresh valid handoff and the universe changed since last-known-good; "
                "manual review required."
            )
            return MANUAL_REVIEW_REQUIRED
        if last_good_age_days is None:
            blocker_reasons.append(
                "no fresh valid handoff and last-known-good age is unknown; manual review required."
            )
            return MANUAL_REVIEW_REQUIRED
        # Usable last-good: degraded, hold / no-trade only.
        if settings_hash_match is False:
            non_blocker_reasons.append(
                "last-known-good is usable but non-universe strategy settings changed; "
                "degraded mode permits only HOLD / NO_TRADE."
            )
        non_blocker_reasons.append(
            f"using last-known-good handoff (age {last_good_age_days}d); no fresh valid handoff this run."
        )
        return DEGRADED_WITH_LAST_GOOD

    # No usable last-good available.
    if candidate_present:
        blocker_reasons.append(
            "parsed research output exists but strict handoff candidate is invalid, "
            "and no last-known-good is available."
        )
        return INVALID_CONTRACT
    if output_present:
        blocker_reasons.append(
            "parsed research output exists but produced no valid handoff, and no last-known-good is available."
        )
        return DEGRADED_NO_RESEARCH
    blocker_reasons.append("no research output and no last-known-good available.")
    return NO_OUTPUT


def _resolve_stale_policy(stale_policy: Mapping[str, Any] | None) -> tuple[int, int]:
    policy = dict(DEFAULT_STALE_POLICY)
    if isinstance(stale_policy, Mapping):
        for key in ("fresh_days", "stale_days"):
            value = stale_policy.get(key)
            if isinstance(value, int) and not isinstance(value, bool):
                policy[key] = value
    return policy["fresh_days"], policy["stale_days"]


def _validation_is_valid(candidate_validation: Any | None) -> bool:
    if candidate_validation is None:
        return False
    if isinstance(candidate_validation, Mapping):
        return candidate_validation.get("valid") is True
    return getattr(candidate_validation, "valid", False) is True


def _parse_date(value: Any) -> date | None:
    if not isinstance(value, str):
        return None
    try:
        return date.fromisoformat(value.strip())
    except ValueError:
        return None


def _age_days(now_date: Any, as_of_date: Any) -> int | None:
    now = _parse_date(now_date)
    as_of = _parse_date(as_of_date)
    if now is None or as_of is None:
        return None
    return (now - as_of).days


def _stale_label(age: int | None, fresh_days: int, stale_days: int) -> str:
    if age is None:
        return "unknown"
    if age <= fresh_days:
        return "fresh"
    if age <= stale_days:
        return "stale"
    return "too_old"


def _string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, str)]


def _current_universe_set(strategy_settings: Mapping[str, Any] | None) -> set[str] | None:
    if not isinstance(strategy_settings, Mapping):
        return None
    core = _string_list(strategy_settings.get("core_universe"))
    satellite = _string_list(strategy_settings.get("satellite_universe"))
    universe = set(core) | set(satellite)
    return universe or None


def _last_good_universe_set(metadata: Mapping[str, Any] | None) -> set[str] | None:
    if not isinstance(metadata, Mapping):
        return None
    universe = metadata.get("universe")
    if not isinstance(universe, Mapping):
        return None
    core = _string_list(universe.get("core_universe"))
    satellite = _string_list(universe.get("satellite_universe"))
    derived = set(core) | set(satellite)
    if derived:
        return derived
    allowed = set(_string_list(universe.get("allowed_buy_tickers")))
    return allowed or None


def _str_or_none(value: Any) -> str | None:
    return value if isinstance(value, str) else None


# --- serialization -----------------------------------------------------------


def research_availability_result_to_dict(result: ResearchAvailabilityResult) -> dict[str, Any]:
    """Full availability result (the ``research_availability.json`` artifact)."""
    return {
        "state": result.state,
        "research_availability": result.research_availability,
        "fresh_research_available": result.fresh_research_available,
        "handoff_valid": result.handoff_valid,
        "handoff_stale": result.handoff_stale,
        "handoff_age_days": result.handoff_age_days,
        "stale_label": result.stale_label,
        "last_good_available": result.last_good_available,
        "last_good_usable": result.last_good_usable,
        "last_good_age_days": result.last_good_age_days,
        "settings_hash_match": result.settings_hash_match,
        "universe_match": result.universe_match,
        "allowed_actions": list(result.allowed_actions),
        "blocked_actions": list(result.blocked_actions),
        "manual_review_required": result.manual_review_required,
        "blocker_reasons": list(result.blocker_reasons),
        "non_blocker_reasons": list(result.non_blocker_reasons),
        "fresh_days": result.fresh_days,
        "stale_days": result.stale_days,
        "source_as_of_date": result.source_as_of_date,
        "now_date": result.now_date,
        "last_good_as_of_date": result.last_good_as_of_date,
        "report_only": True,
    }


def research_freshness_report_to_dict(result: ResearchAvailabilityResult) -> dict[str, Any]:
    """Freshness-focused view (the ``research_freshness_report.json`` artifact)."""
    return {
        "state": result.state,
        "handoff_valid": result.handoff_valid,
        "handoff_age_days": result.handoff_age_days,
        "stale_label": result.stale_label,
        "fresh_days": result.fresh_days,
        "stale_days": result.stale_days,
        "source_as_of_date": result.source_as_of_date,
        "now_date": result.now_date,
        "last_good_available": result.last_good_available,
        "last_good_age_days": result.last_good_age_days,
        "last_good_as_of_date": result.last_good_as_of_date,
        "report_only": True,
    }


def research_degraded_mode_decision_to_dict(result: ResearchAvailabilityResult) -> dict[str, Any]:
    """Permission decision view (the ``research_degraded_mode_decision.json`` artifact)."""
    return {
        "state": result.state,
        "research_availability": result.research_availability,
        "fresh_research_available": result.fresh_research_available,
        "handoff_valid": result.handoff_valid,
        "handoff_stale": result.handoff_stale,
        "settings_hash_match": result.settings_hash_match,
        "universe_match": result.universe_match,
        "allowed_actions": list(result.allowed_actions),
        "blocked_actions": list(result.blocked_actions),
        "manual_review_required": result.manual_review_required,
        "blocker_reasons": list(result.blocker_reasons),
        "non_blocker_reasons": list(result.non_blocker_reasons),
        "report_only": True,
    }
