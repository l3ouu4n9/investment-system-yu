#!/usr/bin/env python3
"""Build deterministic anchor_drift_snapshot.json.

This script consumes:
1. market_data_snapshot.json from build_market_data_snapshot.py
2. raw anchor state JSON that contains the previous baseline / order anchor state

Anchor input schema (minimal):
{
  "run_timestamp_et": "2026-04-13 21:30 ET",  # optional, fallback to market snapshot
  "thresholds": {
    "keep_floor_pct": 2.0,
    "keep_atr_mult": 0.75,
    "mini_floor_pct": 5.0,
    "mini_atr_mult": 1.50
  },
  "tickers": [
    {
      "ticker": "NVDA",
      "side": "buy",
      "old_baseline": 118.0,
      "age_days_since_last_refresh": 9,
      "age_upgrade_applied": false
    }
  ]
}
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Dict, Optional

from investment_orchestrator.common.schema_validation import validate_artifact_schema


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build deterministic anchor_drift_snapshot.json")
    parser.add_argument("--market-snapshot", required=True, help="Path to market_data_snapshot.json")
    parser.add_argument("--anchor-input", required=True, help="Path to raw anchor state JSON")
    parser.add_argument("--output", required=True, help="Path to write anchor drift snapshot JSON")
    parser.add_argument("--run-timestamp-et", help='Override run timestamp, e.g. "2026-04-15 20:30 ET"')
    parser.add_argument("--pretty", action="store_true", help="Pretty-print JSON output")
    return parser.parse_args()


def load_json(path: Path) -> Dict[str, Any]:
    with path.open("r", encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, dict):
        raise ValueError(f"{path} root must be a JSON object")
    return data


def build_market_index(market_snapshot: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    tickers = market_snapshot.get("tickers")
    if not isinstance(tickers, list):
        raise ValueError("market snapshot must contain tickers array")
    index: Dict[str, Dict[str, Any]] = {}
    for row in tickers:
        if not isinstance(row, dict) or "ticker" not in row:
            raise ValueError("invalid ticker row in market snapshot")
        index[str(row["ticker"]).upper()] = row
    return index


def _float_or_default(value: Any, default: float) -> float:
    if value in (None, ""):
        return default
    return float(value)


def _optional_float(value: Any) -> Optional[float]:
    if value in (None, ""):
        return None
    return float(value)


def compute_thresholds(atr_pct: Optional[float], thresholds: Dict[str, Any]) -> Dict[str, float]:
    keep_floor = _float_or_default(thresholds.get("keep_floor_pct"), 2.0)
    keep_mult = _float_or_default(thresholds.get("keep_atr_mult"), 0.75)
    mini_floor = _float_or_default(thresholds.get("mini_floor_pct"), 5.0)
    mini_mult = _float_or_default(thresholds.get("mini_atr_mult"), 1.5)

    if atr_pct is None:
        keep_threshold = keep_floor
        mini_threshold = mini_floor
    else:
        keep_threshold = max(keep_floor, keep_mult * atr_pct)
        mini_threshold = max(mini_floor, mini_mult * atr_pct)

    return {
        "keep_threshold_pct": round(keep_threshold, 4),
        "mini_threshold_pct": round(mini_threshold, 4),
    }


def mechanical_decision(drift_pct: float, keep_threshold_pct: float, mini_threshold_pct: float) -> str:
    if drift_pct <= keep_threshold_pct:
        return "KEEP_PRICE"
    if drift_pct <= mini_threshold_pct:
        return "MINI_REANCHOR"
    return "REANCHOR"


def normalize_anchor_row(raw: Dict[str, Any], market_index: Dict[str, Dict[str, Any]], thresholds: Dict[str, Any]) -> Dict[str, Any]:
    ticker = str(raw.get("ticker", "")).strip().upper()
    side = str(raw.get("side") or "buy").strip().lower()
    market = market_index.get(ticker)

    old_baseline = _optional_float(raw.get("old_baseline"))

    data_gap_reason = None
    if market is None:
        data_gap_reason = "ticker missing from market_data_snapshot"
    elif market.get("data_gap"):
        data_gap_reason = f"market data gap: {market.get('data_gap_reason')}"
    elif old_baseline in (None, 0):
        data_gap_reason = "missing old_baseline"

    new_baseline = None if market is None else _optional_float(market.get("last_close"))
    price_asof = None if market is None else market.get("price_asof")
    atr_pct = None if market is None else _optional_float(market.get("atr_20_30d_pct"))

    signed_drift_pct = None
    drift_pct = None
    up_drift_pct = None
    down_drift_pct = None
    decision = "DATA_GAP"

    threshold_vals = compute_thresholds(atr_pct, thresholds)

    if data_gap_reason is None and new_baseline not in (None, 0) and old_baseline not in (None, 0):
        current_baseline = new_baseline
        previous_baseline = old_baseline
        assert current_baseline is not None
        assert previous_baseline is not None
        signed_drift_pct = ((current_baseline / previous_baseline) - 1.0) * 100.0
        drift_pct = abs(signed_drift_pct)
        up_drift_pct = max(signed_drift_pct, 0.0)
        down_drift_pct = max(-signed_drift_pct, 0.0)
        decision = mechanical_decision(drift_pct, threshold_vals["keep_threshold_pct"], threshold_vals["mini_threshold_pct"])

    return {
        "ticker": ticker,
        "side": side,
        "old_baseline": old_baseline,
        "new_baseline": new_baseline,
        "price_asof": price_asof,
        "atr_pct": atr_pct,
        "signed_drift_pct": None if signed_drift_pct is None else round(signed_drift_pct, 4),
        "drift_pct": None if drift_pct is None else round(drift_pct, 4),
        "up_drift_pct": None if up_drift_pct is None else round(up_drift_pct, 4),
        "down_drift_pct": None if down_drift_pct is None else round(down_drift_pct, 4),
        "keep_threshold_pct": threshold_vals["keep_threshold_pct"],
        "mini_threshold_pct": threshold_vals["mini_threshold_pct"],
        "anchor_decision_mechanical": decision,
        "age_days_since_last_refresh": int(raw.get("age_days_since_last_refresh", 0)),
        "age_upgrade_applied": bool(raw.get("age_upgrade_applied", False)),
        "data_gap": data_gap_reason is not None,
        "data_gap_reason": data_gap_reason,
    }


def build_snapshot(
    market_snapshot: Dict[str, Any],
    anchor_input: Dict[str, Any],
    run_timestamp_override: Optional[str] = None,
) -> Dict[str, Any]:
    market_index = build_market_index(market_snapshot)
    anchor_rows = anchor_input.get("tickers")
    if not isinstance(anchor_rows, list) or not anchor_rows:
        raise ValueError("anchor_input.tickers must be a non-empty array")

    thresholds = dict(anchor_input["thresholds"]) if isinstance(anchor_input.get("thresholds"), dict) else {}
    normalized = [normalize_anchor_row(row, market_index, thresholds) for row in anchor_rows if isinstance(row, dict)]
    if not normalized:
        raise ValueError("anchor_input.tickers must contain at least one JSON object row")
    normalized.sort(key=lambda x: x["ticker"])

    return {
        "schema_version": "1.0",
        "snapshot_type": "ANCHOR_DRIFT_SNAPSHOT",
        "run_timestamp_et": str(
            run_timestamp_override
            or anchor_input.get("run_timestamp_et")
            or market_snapshot.get("run_timestamp_et")
        ),
        "tickers": normalized,
    }


def write_json(path: Path, data: Dict[str, Any], pretty: bool) -> None:
    with path.open("w", encoding="utf-8") as f:
        if pretty:
            json.dump(data, f, ensure_ascii=False, indent=2)
        else:
            json.dump(data, f, ensure_ascii=False, separators=(",", ":"))


def build_anchor_drift_snapshot(
    *,
    market_snapshot_path: str | Path,
    anchor_input_path: str | Path,
    output_path: str | Path,
    run_timestamp_et: Optional[str] = None,
    pretty: bool = True,
) -> Dict[str, Any]:
    """Build and write anchor_drift_snapshot.json."""
    market_snapshot = load_json(Path(market_snapshot_path))
    anchor_input = load_json(Path(anchor_input_path))
    snapshot = build_snapshot(market_snapshot, anchor_input, run_timestamp_override=run_timestamp_et)
    validate_artifact_schema(snapshot, schema_name="anchor_drift_snapshot.schema.json")
    write_json(Path(output_path), snapshot, pretty)
    return snapshot


def main() -> None:
    args = parse_args()
    market_snapshot = load_json(Path(args.market_snapshot))
    anchor_input = load_json(Path(args.anchor_input))
    snapshot = build_snapshot(market_snapshot, anchor_input, run_timestamp_override=args.run_timestamp_et)
    validate_artifact_schema(snapshot, schema_name="anchor_drift_snapshot.schema.json")
    write_json(Path(args.output), snapshot, args.pretty)


if __name__ == "__main__":
    main()
