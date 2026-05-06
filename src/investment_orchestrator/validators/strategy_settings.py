"""Validation helpers for operator-maintained strategy settings."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from investment_orchestrator.common.io import read_text


ETF_ROLE_NAMES = (
    "benchmark_carrier_core",
    "diversified_core_buffer",
    "sector_alpha_tilt",
    "extended_etf_minority_sleeve",
)


class StrategySettingsValidationError(ValueError):
    """Raised when strategy settings are malformed."""


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise StrategySettingsValidationError(message)


def parse_strategy_settings_text(text: str) -> dict[str, Any]:
    """Parse YAML strategy settings, accepting an empty/missing policy surface."""
    payload = yaml.safe_load(text) if text.strip() else {}
    _require(isinstance(payload, dict), "strategy_settings.yaml must parse to a mapping/object.")
    validate_strategy_settings(payload)
    return payload


def load_strategy_settings(path: str | Path) -> dict[str, Any]:
    """Read and validate a strategy settings YAML file."""
    return parse_strategy_settings_text(read_text(path))


def validate_strategy_settings(payload: Any) -> dict[str, Any]:
    """Validate only the repo-wired policy shape, preserving backward compatibility."""
    _require(isinstance(payload, dict), "strategy_settings.yaml must parse to a mapping/object.")

    etf_policy = payload.get("etf_layer_execution_policy")
    if etf_policy is not None:
        _require(isinstance(etf_policy, dict), "etf_layer_execution_policy must be a mapping when present.")

    drift_policy = payload.get("daily_execution_drift_policy")
    if drift_policy is None:
        return payload
    _require(isinstance(drift_policy, dict), "daily_execution_drift_policy must be a mapping when present.")

    for optional_key in (
        "near_edge_monitor_band",
        "lowest_live_limit_policy",
        "repeated_near_edge_policy",
    ):
        value = drift_policy.get(optional_key)
        if value is not None:
            _require(
                isinstance(value, dict),
                f"daily_execution_drift_policy.{optional_key} must be a mapping when present.",
            )

    keep_tolerance = drift_policy.get("keep_tolerance")
    if isinstance(keep_tolerance, dict):
        for tolerance_key in (
            "anchor_drift_abs_pct_static_cap",
            "anchor_drift_atr_multiple_cap",
            "max_negative_distance_to_highest_live_limit_pct",
        ):
            values = keep_tolerance.get(tolerance_key)
            if values is None:
                continue
            _require(isinstance(values, dict), f"keep_tolerance.{tolerance_key} must be a role mapping.")
            missing_roles = [role for role in ETF_ROLE_NAMES if role not in values]
            _require(
                not missing_roles,
                f"keep_tolerance.{tolerance_key} is missing roles: {', '.join(missing_roles)}",
            )

    near_edge = drift_policy.get("near_edge_monitor_band")
    if isinstance(near_edge, dict):
        for band_key in ("anchor_drift_pct_band", "highest_live_limit_distance_pct_band"):
            values = near_edge.get(band_key)
            if values is None:
                continue
            _require(isinstance(values, dict), f"near_edge_monitor_band.{band_key} must be a role mapping.")
            missing_roles = [role for role in ETF_ROLE_NAMES if role not in values]
            _require(
                not missing_roles,
                f"near_edge_monitor_band.{band_key} is missing roles: {', '.join(missing_roles)}",
            )

    return payload


def is_near_edge_monitor_enabled(settings: Any) -> bool:
    """Return true only when the new daily near-edge monitor is explicitly enabled."""
    if not isinstance(settings, dict):
        return False
    drift_policy = settings.get("daily_execution_drift_policy")
    if not isinstance(drift_policy, dict):
        return False
    near_edge = drift_policy.get("near_edge_monitor_band")
    return isinstance(near_edge, dict) and near_edge.get("enabled") is True


def lowest_live_limit_is_evidence_only(settings: Any) -> bool:
    """Return true when distance_to_lowest_live_limit_pct is explicitly evidence-only."""
    if not isinstance(settings, dict):
        return False
    drift_policy = settings.get("daily_execution_drift_policy")
    if not isinstance(drift_policy, dict):
        return False
    lowest_policy = drift_policy.get("lowest_live_limit_policy")
    if not isinstance(lowest_policy, dict):
        return False
    distance_policy = lowest_policy.get("distance_to_lowest_live_limit_pct")
    return isinstance(distance_policy, dict) and distance_policy.get("action_threshold_role") == "none"
