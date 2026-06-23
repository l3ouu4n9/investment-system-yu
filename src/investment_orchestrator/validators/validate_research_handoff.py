"""Strict validation for Step 1 research-to-execution handoff fields."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any


BASE_ROLE_KEYS = (
    "benchmark_carrier_core",
    "diversified_core_buffer",
    "sector_alpha_tilt",
)
REQUIRED_TOP_LEVEL_FIELDS = (
    "schema_version",
    "trade_universe",
    "buy_universe_scorecard",
    "scheduled_events",
    "structural_themes_6_18m",
    "regime_inputs",
    "policy_items",
    "top5_next_week",
    "user_approved_extended_etf_static_list",
    "proposed_extended_etf_candidates",
    "extended_etf_candidate_universe",
    "extended_etf_predecision_scorecard",
    "approved_static_list_screening_log",
    "optional_extended_etf_sleeve",
    "strategy_a_research_handoff",
)
REQUIRED_BUY_SCORECARD_FIELDS = (
    "ticker",
    "role_layer",
    "execution_priority_this_run",
    "actionability_status",
    "entry_driver",
    "primary_anchor_type",
    "primary_anchor_event_id",
    "primary_anchor_date_et",
    "preferred_scheduled_theme_event_id",
    "thesis_12m_plus_supported",
    "thesis_12m_plus_summary",
    "thesis_linkage_quality",
    "compile_blocker_if_any",
    "event_id_refs",
    "structural_theme_refs",
)
REQUIRED_HANDOFF_FIELDS = (
    "handoff_version",
    "handoff_scope",
    "not_order_instruction",
    "strategy_a_must_still_apply",
    "base_shortlist_eligible_by_role",
    "base_watch_only_by_role",
    "positive_delta_research_supported",
    "positive_delta_not_implied_for",
    "replacement_ranking_by_role",
    "rotation_handoff",
    "buy_side_no_action_hints",
    "extended_lane_downstream_gate",
    "sell_side_research_boundary",
)
REQUIRED_EXTENDED_GATE_FIELDS = (
    "effective_allowed_extended_etf_tickers_this_run",
    "predecision_only_tickers",
    "proposed_only_tickers",
    "approved_but_excluded_tickers",
    "must_not_enter_strategy_a_effective_universe",
    "disable_reason",
    "why_not_enabled",
)
DATA_GAP_MARKERS = ("DATA_GAP", "missing", "unspecified", "unknown")


@dataclass(frozen=True)
class ResearchHandoffValidationResult:
    """Structured result for strict research handoff validation."""

    valid: bool
    fail_reasons: list[str] = field(default_factory=list)
    missing_fields: list[str] = field(default_factory=list)
    blocker_reasons: list[str] = field(default_factory=list)
    non_blocker_reasons: list[str] = field(default_factory=list)


def research_handoff_validation_result_to_dict(
    result: ResearchHandoffValidationResult,
) -> dict[str, Any]:
    """Serialize handoff validation results for stable JSON artifacts."""
    return {
        "valid": result.valid,
        "fail_reasons": list(result.fail_reasons),
        "missing_fields": list(result.missing_fields),
        "blocker_reasons": list(result.blocker_reasons),
        "non_blocker_reasons": list(result.non_blocker_reasons),
    }


class _Collector:
    def __init__(self) -> None:
        self.fail_reasons: list[str] = []
        self.missing_fields: list[str] = []
        self.blocker_reasons: list[str] = []
        self.non_blocker_reasons: list[str] = []

    def missing(self, path: str) -> None:
        self.missing_fields.append(path)
        self.blocker(f"Missing execution handoff field: {path}")

    def fail(self, reason: str) -> None:
        self.fail_reasons.append(reason)

    def blocker(self, reason: str) -> None:
        self.blocker_reasons.append(reason)
        self.fail(reason)

    def non_blocker(self, reason: str) -> None:
        self.non_blocker_reasons.append(reason)

    def result(self) -> ResearchHandoffValidationResult:
        return ResearchHandoffValidationResult(
            valid=not self.fail_reasons,
            fail_reasons=self.fail_reasons,
            missing_fields=self.missing_fields,
            blocker_reasons=self.blocker_reasons,
            non_blocker_reasons=self.non_blocker_reasons,
        )


def validate_research_handoff(
    payload: Any,
    *,
    strategy_settings: Mapping[str, Any] | None = None,
) -> ResearchHandoffValidationResult:
    """Validate Step 1's strict downstream execution handoff contract.

    This intentionally does not trust research_output.validation_summary.passed.
    The permissive research artifact schema may pass while the execution handoff
    remains invalid.
    """
    collector = _Collector()
    if not isinstance(payload, Mapping):
        collector.blocker("research handoff payload must be a JSON object.")
        return collector.result()

    for field_name in REQUIRED_TOP_LEVEL_FIELDS:
        if field_name not in payload:
            collector.missing(field_name)

    _validate_schema_version(payload, collector)
    required_buy_tickers = _derive_required_buy_universe(strategy_settings, collector)
    allowed_buy_tickers = _validate_trade_universe(
        payload.get("trade_universe"),
        required_buy_tickers,
        collector,
    )
    scorecard_coverage_tickers = required_buy_tickers if required_buy_tickers is not None else allowed_buy_tickers
    scorecard_items = _validate_buy_universe_scorecard(
        payload.get("buy_universe_scorecard"),
        scorecard_coverage_tickers,
        collector,
    )
    _validate_top_level_container_types(payload, collector)
    allowed_extended_tickers = _validate_optional_extended_etf_sleeve(
        payload.get("optional_extended_etf_sleeve"),
        collector,
    )
    _validate_lane_b_contract(
        payload,
        allowed_extended_tickers,
        collector,
    )
    _validate_strategy_a_handoff(
        payload.get("strategy_a_research_handoff"),
        allowed_buy_tickers,
        allowed_extended_tickers,
        scorecard_items,
        payload.get("optional_extended_etf_sleeve"),
        collector,
    )

    return collector.result()


def _validate_schema_version(payload: Mapping[str, Any], collector: _Collector) -> None:
    value = payload.get("schema_version")
    if not isinstance(value, str) or not value.strip():
        collector.blocker("schema_version must be a non-empty string.")


def _derive_required_buy_universe(
    strategy_settings: Mapping[str, Any] | None,
    collector: _Collector,
) -> list[str] | None:
    if strategy_settings is None:
        collector.non_blocker(
            "strategy_settings not provided; using RESEARCH_JSON.trade_universe.allowed_buy_tickers "
            "as the required buy universe for backward-compatible report-only validation."
        )
        return None

    core_universe = strategy_settings.get("core_universe")
    satellite_universe = strategy_settings.get("satellite_universe")
    if not _is_list(core_universe) or not _is_list(satellite_universe):
        collector.blocker("strategy_settings core_universe and satellite_universe must both be lists.")
        return []

    required = _string_list([*core_universe, *satellite_universe])
    if len(required) != len(core_universe) + len(satellite_universe):
        collector.blocker("strategy_settings core_universe and satellite_universe must contain only non-empty strings.")
    if len(set(required)) != len(required):
        collector.blocker("strategy_settings derived buy universe must not contain duplicates.")
    return required


def _validate_trade_universe(
    value: Any,
    required_buy_tickers: list[str] | None,
    collector: _Collector,
) -> list[str]:
    if not isinstance(value, Mapping):
        collector.blocker("trade_universe must be an object.")
        return required_buy_tickers or []

    allowed = value.get("allowed_buy_tickers")
    if "allowed_buy_tickers" not in value:
        collector.missing("trade_universe.allowed_buy_tickers")
        return []
    if not _is_list(allowed):
        collector.blocker("trade_universe.allowed_buy_tickers must be a list.")
        return []
    if not allowed:
        collector.blocker("trade_universe.allowed_buy_tickers must be non-empty.")
        return []
    tickers = _string_list(allowed)
    if len(tickers) != len(allowed):
        collector.blocker("trade_universe.allowed_buy_tickers must contain only non-empty strings.")
    if len(set(tickers)) != len(tickers):
        collector.blocker("trade_universe.allowed_buy_tickers must not contain duplicates.")
    if required_buy_tickers is not None:
        missing_required = sorted(set(required_buy_tickers) - set(tickers))
        for ticker in missing_required:
            collector.blocker(
                "trade_universe.allowed_buy_tickers must cover strategy_settings derived buy universe "
                f"ticker {ticker}."
            )
        extra = sorted(set(tickers) - set(required_buy_tickers))
        if extra:
            collector.non_blocker(
                "trade_universe.allowed_buy_tickers includes tickers outside strategy_settings derived "
                f"buy universe: {extra}."
            )
    return tickers


def _validate_buy_universe_scorecard(
    value: Any,
    allowed_buy_tickers: list[str],
    collector: _Collector,
) -> dict[str, Mapping[str, Any]]:
    rows = _scorecard_rows(value, collector)
    by_ticker: dict[str, Mapping[str, Any]] = {}

    for index, row in enumerate(rows):
        path = f"buy_universe_scorecard[{index}]"
        ticker = _ticker(row.get("ticker"))
        if not ticker:
            collector.blocker(f"{path}.ticker must be a non-empty string.")
            continue
        if ticker in by_ticker:
            collector.blocker(f"buy_universe_scorecard contains duplicate ticker {ticker}.")
        by_ticker[ticker] = row

        missing = [field_name for field_name in REQUIRED_BUY_SCORECARD_FIELDS if field_name not in row]
        for field_name in missing:
            collector.missing(f"{path}.{field_name}")

        if row.get("role_layer") not in BASE_ROLE_KEYS:
            collector.blocker(f"{path}.role_layer must be one of {list(BASE_ROLE_KEYS)}.")
        if not isinstance(row.get("execution_priority_this_run"), int):
            collector.blocker(f"{path}.execution_priority_this_run must be an integer.")
        if not _is_list(row.get("event_id_refs")):
            collector.blocker(f"{path}.event_id_refs must be a list.")
        if not _is_list(row.get("structural_theme_refs")):
            collector.blocker(f"{path}.structural_theme_refs must be a list.")

        actionability_status = row.get("actionability_status")
        is_actionable = actionability_status == "actionable_this_run"
        _classify_data_gap_markers(row, path, blocker=is_actionable, collector=collector)
        if is_actionable:
            _validate_actionable_scorecard_item(row, path, collector)

    missing_tickers = sorted(set(allowed_buy_tickers) - set(by_ticker))
    for ticker in missing_tickers:
        collector.blocker(f"buy_universe_scorecard must cover allowed buy ticker {ticker}.")

    return by_ticker


def _scorecard_rows(value: Any, collector: _Collector) -> list[Mapping[str, Any]]:
    if isinstance(value, Mapping):
        rows: list[Mapping[str, Any]] = []
        for key, item in value.items():
            if not isinstance(item, Mapping):
                collector.blocker(f"buy_universe_scorecard.{key} must be an object.")
                continue
            if "ticker" in item:
                rows.append(item)
            else:
                rows.append({"ticker": key, **dict(item)})
        return rows

    if _is_list(value):
        rows = []
        for index, item in enumerate(value):
            if not isinstance(item, Mapping):
                collector.blocker(f"buy_universe_scorecard[{index}] must be an object.")
                continue
            rows.append(item)
        return rows

    collector.blocker("buy_universe_scorecard must be an object or list.")
    return []


def _validate_actionable_scorecard_item(
    row: Mapping[str, Any],
    path: str,
    collector: _Collector,
) -> None:
    if row.get("thesis_12m_plus_supported") is not True:
        collector.blocker(f"{path}.thesis_12m_plus_supported must be true for actionable_this_run.")
    if row.get("thesis_linkage_quality") not in {"strong", "adequate"}:
        collector.blocker(f"{path}.thesis_linkage_quality must be strong or adequate for actionable_this_run.")
    if not row.get("event_id_refs") and not row.get("structural_theme_refs"):
        collector.blocker(f"{path} must have event_id_refs or structural_theme_refs for actionable_this_run.")
    if not row.get("primary_anchor_event_id"):
        collector.blocker(f"{path}.primary_anchor_event_id is required for actionable_this_run.")
    if not row.get("primary_anchor_date_et"):
        collector.blocker(f"{path}.primary_anchor_date_et is required for actionable_this_run.")
    if row.get("compile_blocker_if_any") is not None:
        collector.blocker(f"{path}.compile_blocker_if_any must be null for actionable_this_run.")


def _validate_top_level_container_types(payload: Mapping[str, Any], collector: _Collector) -> None:
    expected = {
        "scheduled_events": list,
        "structural_themes_6_18m": list,
        "regime_inputs": Mapping,
        "policy_items": list,
        "top5_next_week": list,
        "user_approved_extended_etf_static_list": list,
        "proposed_extended_etf_candidates": list,
        "extended_etf_candidate_universe": list,
        "extended_etf_predecision_scorecard": list,
        "approved_static_list_screening_log": list,
    }
    for field_name, expected_type in expected.items():
        if field_name not in payload:
            continue
        value = payload[field_name]
        if expected_type is Mapping:
            if not isinstance(value, Mapping):
                collector.blocker(f"{field_name} must be an object.")
        elif not _is_list(value):
            collector.blocker(f"{field_name} must be a list.")

    scheduled_events = payload.get("scheduled_events")
    if _is_list(scheduled_events):
        for index, event in enumerate(scheduled_events):
            if not isinstance(event, Mapping):
                collector.blocker(f"scheduled_events[{index}] must be an object.")


def _validate_optional_extended_etf_sleeve(value: Any, collector: _Collector) -> list[str]:
    if not isinstance(value, Mapping):
        collector.blocker("optional_extended_etf_sleeve must be an object.")
        return []

    enabled = value.get("enabled")
    if not isinstance(enabled, bool):
        collector.blocker("optional_extended_etf_sleeve.enabled must be a boolean.")

    allowed = value.get("allowed_extended_etf_tickers")
    if "allowed_extended_etf_tickers" not in value:
        collector.missing("optional_extended_etf_sleeve.allowed_extended_etf_tickers")
        allowed_tickers: list[str] = []
    elif not _is_list(allowed):
        collector.blocker("optional_extended_etf_sleeve.allowed_extended_etf_tickers must be a list.")
        allowed_tickers = []
    else:
        allowed_tickers = _string_list(allowed)
        if len(allowed_tickers) != len(allowed):
            collector.blocker(
                "optional_extended_etf_sleeve.allowed_extended_etf_tickers must contain only non-empty strings."
            )

    for field_name in ("disable_reason", "why_not_enabled"):
        if field_name not in value:
            collector.missing(f"optional_extended_etf_sleeve.{field_name}")
            continue
        if not isinstance(value[field_name], str):
            collector.blocker(f"optional_extended_etf_sleeve.{field_name} must be a string.")

    if enabled is False:
        if allowed_tickers:
            collector.blocker(
                "optional_extended_etf_sleeve.allowed_extended_etf_tickers must be empty when enabled is false."
            )
        for field_name in ("disable_reason", "why_not_enabled"):
            if isinstance(value.get(field_name), str) and not value[field_name].strip():
                collector.blocker(f"optional_extended_etf_sleeve.{field_name} must explain the disabled gate.")
    elif enabled is True and not allowed_tickers:
        collector.blocker(
            "optional_extended_etf_sleeve.allowed_extended_etf_tickers must be non-empty when enabled is true."
        )

    return allowed_tickers


def _validate_lane_b_contract(
    payload: Mapping[str, Any],
    allowed_extended_tickers: list[str],
    collector: _Collector,
) -> None:
    _validate_approved_static_list_screening(payload, collector)
    _validate_proposed_candidate_coverage(payload, collector)
    _validate_extended_scorecard(
        payload.get("extended_etf_scorecard"),
        allowed_extended_tickers,
        payload.get("optional_extended_etf_sleeve"),
        collector,
    )


def _validate_approved_static_list_screening(
    payload: Mapping[str, Any],
    collector: _Collector,
) -> None:
    approved = payload.get("user_approved_extended_etf_static_list")
    screening = payload.get("approved_static_list_screening_log")
    if not _is_list(approved) or not _is_list(screening):
        return
    approved_tickers = set(_string_list(approved))
    if len(approved_tickers) != len(approved):
        collector.blocker("user_approved_extended_etf_static_list must contain only non-empty strings.")
    screening_tickers = _ticker_set_from_rows(screening, "approved_static_list_screening_log", collector)
    for ticker in sorted(approved_tickers - screening_tickers):
        collector.blocker(f"approved_static_list_screening_log must cover approved static-list ticker {ticker}.")


def _validate_proposed_candidate_coverage(
    payload: Mapping[str, Any],
    collector: _Collector,
) -> None:
    proposed = payload.get("proposed_extended_etf_candidates")
    candidate_universe = payload.get("extended_etf_candidate_universe")
    predecision = payload.get("extended_etf_predecision_scorecard")
    if not _is_list(proposed) or not proposed:
        return
    if not _is_list(candidate_universe) or not _is_list(predecision):
        return

    proposed_tickers = _admitted_proposed_ticker_set(proposed, collector)
    if not proposed_tickers:
        return
    candidate_tickers = _ticker_set_from_rows(
        candidate_universe,
        "extended_etf_candidate_universe",
        collector,
    )
    predecision_tickers = _ticker_set_from_rows(
        predecision,
        "extended_etf_predecision_scorecard",
        collector,
    )
    for ticker in sorted(proposed_tickers - candidate_tickers):
        collector.blocker(f"extended_etf_candidate_universe must cover proposed candidate ticker {ticker}.")
    for ticker in sorted(proposed_tickers - predecision_tickers):
        collector.blocker(f"extended_etf_predecision_scorecard must cover proposed candidate ticker {ticker}.")


def _validate_extended_scorecard(
    value: Any,
    allowed_extended_tickers: list[str],
    optional_sleeve: Any,
    collector: _Collector,
) -> None:
    enabled = optional_sleeve.get("enabled") if isinstance(optional_sleeve, Mapping) else None
    if value is None:
        if enabled is True:
            collector.missing("extended_etf_scorecard")
        elif enabled is False:
            collector.non_blocker("extended_etf_scorecard missing while extended ETF sleeve is disabled.")
        return
    if not _is_list(value):
        collector.blocker("extended_etf_scorecard must be a list.")
        return
    if enabled is not True:
        return
    scorecard_tickers = {
        ticker
        for row in value
        if isinstance(row, Mapping)
        for ticker in [_ticker(row.get("ticker"))]
        if ticker
    }
    for ticker in sorted(set(allowed_extended_tickers) - scorecard_tickers):
        collector.blocker(f"extended_etf_scorecard must cover enabled extended ETF ticker {ticker}.")
    for index, row in enumerate(value):
        if not isinstance(row, Mapping):
            collector.blocker(f"extended_etf_scorecard[{index}] must be an object.")
            continue
        if _ticker(row.get("ticker")) in set(allowed_extended_tickers):
            _validate_enabled_extended_scorecard_row(row, index, collector)


def _validate_enabled_extended_scorecard_row(
    row: Mapping[str, Any],
    index: int,
    collector: _Collector,
) -> None:
    path = f"extended_etf_scorecard[{index}]"
    required_fields = (
        "ticker",
        "event_id_refs",
        "structural_theme_refs",
    )
    for field_name in required_fields:
        if field_name not in row:
            collector.missing(f"{path}.{field_name}")
    if not row.get("event_id_refs") and not row.get("structural_theme_refs"):
        collector.blocker(f"{path} must have event_id_refs or structural_theme_refs when sleeve is enabled.")
    for field_name in ("event_id_refs", "structural_theme_refs"):
        if field_name in row and not _is_list(row.get(field_name)):
            collector.blocker(f"{path}.{field_name} must be a list.")


def _validate_strategy_a_handoff(
    value: Any,
    allowed_buy_tickers: list[str],
    allowed_extended_tickers: list[str],
    scorecard_items: dict[str, Mapping[str, Any]],
    optional_sleeve: Any,
    collector: _Collector,
) -> None:
    if not isinstance(value, Mapping):
        collector.blocker("strategy_a_research_handoff must be an object.")
        return

    for field_name in REQUIRED_HANDOFF_FIELDS:
        if field_name not in value:
            collector.missing(f"strategy_a_research_handoff.{field_name}")

    if value.get("handoff_version") != "strategy_a_research_handoff_v1":
        collector.blocker("strategy_a_research_handoff.handoff_version must be strategy_a_research_handoff_v1.")
    if value.get("handoff_scope") != "research_to_decision_builder_only":
        collector.blocker("strategy_a_research_handoff.handoff_scope must be research_to_decision_builder_only.")
    if value.get("not_order_instruction") is not True:
        collector.blocker("strategy_a_research_handoff.not_order_instruction must be true.")
    if not _is_list(value.get("strategy_a_must_still_apply")):
        collector.blocker("strategy_a_research_handoff.strategy_a_must_still_apply must be a list.")

    for field_name in (
        "base_shortlist_eligible_by_role",
        "base_watch_only_by_role",
        "replacement_ranking_by_role",
    ):
        _validate_role_mapping(value.get(field_name), f"strategy_a_research_handoff.{field_name}", collector)

    for field_name in (
        "positive_delta_research_supported",
        "positive_delta_not_implied_for",
        "rotation_handoff",
        "buy_side_no_action_hints",
    ):
        if not _is_list(value.get(field_name)):
            collector.blocker(f"strategy_a_research_handoff.{field_name} must be a list.")

    allowed_set = set(allowed_buy_tickers)
    _validate_handoff_base_ticker_lists(value, allowed_set, scorecard_items, collector)
    _validate_extended_lane_downstream_gate(
        value.get("extended_lane_downstream_gate"),
        allowed_extended_tickers,
        optional_sleeve,
        collector,
    )
    if not isinstance(value.get("sell_side_research_boundary"), Mapping):
        collector.blocker("strategy_a_research_handoff.sell_side_research_boundary must be an object.")


def _validate_role_mapping(value: Any, path: str, collector: _Collector) -> None:
    if not isinstance(value, Mapping):
        collector.blocker(f"{path} must be an object.")
        return
    missing_roles = [role for role in BASE_ROLE_KEYS if role not in value]
    for role in missing_roles:
        collector.missing(f"{path}.{role}")
    for role, tickers in value.items():
        if role not in BASE_ROLE_KEYS:
            collector.blocker(f"{path} contains unsupported role {role}.")
        if not _is_list(tickers):
            collector.blocker(f"{path}.{role} must be a list.")


def _validate_handoff_base_ticker_lists(
    handoff: Mapping[str, Any],
    allowed_tickers: set[str],
    scorecard_items: dict[str, Mapping[str, Any]],
    collector: _Collector,
) -> None:
    for field_name in ("positive_delta_research_supported", "positive_delta_not_implied_for"):
        tickers = handoff.get(field_name)
        if _is_list(tickers):
            _validate_tickers_allowed(
                tickers,
                allowed_tickers,
                f"strategy_a_research_handoff.{field_name}",
                collector,
            )

    for field_name in ("base_shortlist_eligible_by_role", "base_watch_only_by_role"):
        role_mapping = handoff.get(field_name)
        if not isinstance(role_mapping, Mapping):
            continue
        for role, tickers in role_mapping.items():
            if _is_list(tickers):
                _validate_tickers_allowed(
                    tickers,
                    allowed_tickers,
                    f"strategy_a_research_handoff.{field_name}.{role}",
                    collector,
                )

    positive_delta = handoff.get("positive_delta_research_supported")
    if _is_list(positive_delta):
        for ticker in _string_list(positive_delta):
            if scorecard_items.get(ticker, {}).get("actionability_status") != "actionable_this_run":
                collector.blocker(
                    "strategy_a_research_handoff.positive_delta_research_supported "
                    f"contains non-actionable ticker {ticker}."
                )


def _validate_tickers_allowed(
    values: Sequence[Any],
    allowed_tickers: set[str],
    path: str,
    collector: _Collector,
) -> None:
    for item in values:
        ticker = _ticker(item)
        if not ticker:
            collector.blocker(f"{path} must contain only non-empty ticker strings.")
        elif ticker not in allowed_tickers:
            collector.blocker(f"{path} contains ticker outside trade_universe.allowed_buy_tickers: {ticker}.")


def _ticker_set_from_rows(
    rows: Sequence[Any],
    path: str,
    collector: _Collector,
) -> set[str]:
    tickers: set[str] = set()
    for index, row in enumerate(rows):
        if not isinstance(row, Mapping):
            collector.blocker(f"{path}[{index}] must be an object.")
            continue
        ticker = _ticker(row.get("ticker"))
        if not ticker:
            collector.blocker(f"{path}[{index}].ticker must be a non-empty string.")
            continue
        tickers.add(ticker)
    return tickers


def _admitted_proposed_ticker_set(
    rows: Sequence[Any],
    collector: _Collector,
) -> set[str]:
    tickers: set[str] = set()
    for index, row in enumerate(rows):
        path = f"proposed_extended_etf_candidates[{index}]"
        if not isinstance(row, Mapping):
            collector.blocker(f"{path} must be an object.")
            continue
        ticker = _ticker(row.get("ticker"))
        if not ticker:
            collector.blocker(f"{path}.ticker must be a non-empty string.")
            continue
        lane_b_status = row.get("lane_b_status")
        admitted = row.get("admitted_to_candidate_universe") is True
        can_enter = row.get("can_enter_effective_allowed_buy_universe_this_run") is True
        if admitted or can_enter or lane_b_status in {"predecision_only", "activated_tradeable"}:
            tickers.add(ticker)
    return tickers


def _validate_extended_lane_downstream_gate(
    value: Any,
    allowed_extended_tickers: list[str],
    optional_sleeve: Any,
    collector: _Collector,
) -> None:
    if not isinstance(value, Mapping):
        collector.blocker("strategy_a_research_handoff.extended_lane_downstream_gate must be an object.")
        return

    for field_name in REQUIRED_EXTENDED_GATE_FIELDS:
        if field_name not in value:
            collector.missing(f"strategy_a_research_handoff.extended_lane_downstream_gate.{field_name}")

    effective = value.get("effective_allowed_extended_etf_tickers_this_run")
    if not _is_list(effective):
        collector.blocker(
            "strategy_a_research_handoff.extended_lane_downstream_gate."
            "effective_allowed_extended_etf_tickers_this_run must be a list."
        )
    elif _string_list(effective) != allowed_extended_tickers:
        collector.blocker(
            "strategy_a_research_handoff.extended_lane_downstream_gate."
            "effective_allowed_extended_etf_tickers_this_run must match "
            "optional_extended_etf_sleeve.allowed_extended_etf_tickers."
        )

    for field_name in (
        "predecision_only_tickers",
        "proposed_only_tickers",
        "approved_but_excluded_tickers",
        "must_not_enter_strategy_a_effective_universe",
    ):
        if not _is_list(value.get(field_name)):
            collector.blocker(f"strategy_a_research_handoff.extended_lane_downstream_gate.{field_name} must be a list.")

    if isinstance(optional_sleeve, Mapping):
        for field_name in ("disable_reason", "why_not_enabled"):
            if value.get(field_name) != optional_sleeve.get(field_name):
                collector.blocker(
                    f"strategy_a_research_handoff.extended_lane_downstream_gate.{field_name} "
                    f"must match optional_extended_etf_sleeve.{field_name}."
                )


def _classify_data_gap_markers(
    row: Mapping[str, Any],
    path: str,
    *,
    blocker: bool,
    collector: _Collector,
) -> None:
    for field_name in REQUIRED_BUY_SCORECARD_FIELDS:
        value = row.get(field_name)
        if isinstance(value, str) and any(marker.lower() in value.lower() for marker in DATA_GAP_MARKERS):
            message = f"{path}.{field_name} contains handoff uncertainty marker: {value}"
            if blocker:
                collector.blocker(message)
            else:
                collector.non_blocker(message)


def _is_list(value: Any) -> bool:
    return isinstance(value, list)


def _string_list(values: Sequence[Any]) -> list[str]:
    return [ticker for value in values for ticker in [_ticker(value)] if ticker]


def _ticker(value: Any) -> str:
    if not isinstance(value, str):
        return ""
    return value.strip().upper()
