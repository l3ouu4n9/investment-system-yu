#!/usr/bin/env python3
"""Build deterministic market_data_snapshot.json from raw market data input.

This script is intentionally deterministic. It does NOT fetch market data.
It normalizes and validates pre-fetched raw inputs into the JSON shape that
Strategy Template / Daily Quick Check can consume.

Raw input schema (minimal):
{
  "run_timestamp_et": "2026-04-13 21:30 ET",   # optional if passed by CLI
  "market_data_target_close_date_et": "2026-04-14",  # optional
  "primary_source": "Barchart",                # optional
  "fallback_source_for_last_close_and_price_asof_only": "Stooq",  # optional
  "tickers": [
    {
      "ticker": "NVDA",
      "last_close": 123.45,
      "price_asof": "2026-04-14",
      "atr_30d_pct": 4.8,                       # optional
      "atr_20d_pct": 4.6,                       # optional
      "atr_pct": 4.8,                           # optional fallback alias
      "ma50": 118.2,                            # optional
      "ma200": 97.4,                            # optional
      "avg_volume_3m": 45200000,                # optional
      "last_close_source": "Barchart",        # optional
      "price_asof_source": "Barchart",        # optional
      "technicals_source": "Barchart",        # optional
      "retrieved_at_utc": "2026-04-15T02:41:19Z",  # recommended
      "same_day_close_required": true,          # optional
      "notes": []                               # optional
    }
  ]
}
"""

from __future__ import annotations

import argparse
import json
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional

from zoneinfo import ZoneInfo

from investment_orchestrator.common.schema_validation import validate_artifact_schema

ET_TZ = ZoneInfo("America/New_York")

RUN_TS_RE = re.compile(r"^(\d{4}-\d{2}-\d{2})[ T](\d{2}:\d{2})(?::\d{2})? ?(?:ET)?$")
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
UTC_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")


@dataclass
class ValidationIssue:
    ticker: Optional[str]
    message: str


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build deterministic market_data_snapshot.json")
    parser.add_argument("--raw-input", required=True, help="Path to raw market data JSON")
    parser.add_argument("--output", required=True, help="Path to write normalized snapshot JSON")
    parser.add_argument("--run-timestamp-et", help='Override run timestamp, e.g. "2026-04-13 21:30 ET"')
    parser.add_argument("--target-close-date-et", help="Override market_data_target_close_date_et (YYYY-MM-DD)")
    parser.add_argument("--pretty", action="store_true", help="Pretty-print JSON output")
    return parser.parse_args()


def load_json(path: Path) -> Dict[str, Any]:
    with path.open("r", encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, dict):
        raise ValueError("Raw input root must be a JSON object.")
    return data


def parse_run_timestamp_et(text: str) -> datetime:
    match = RUN_TS_RE.match(text.strip())
    if not match:
        raise ValueError(f"Invalid run_timestamp_et: {text!r}")
    dt = datetime.strptime(f"{match.group(1)} {match.group(2)}", "%Y-%m-%d %H:%M")
    return dt.replace(tzinfo=ET_TZ)


def ensure_date(value: str, field_name: str) -> str:
    if not isinstance(value, str) or not DATE_RE.match(value):
        raise ValueError(f"{field_name} must be YYYY-MM-DD, got {value!r}")
    return value


def choose_atr_pct(raw: Dict[str, Any]) -> Optional[float]:
    for key in ("atr_30d_pct", "atr_20d_pct", "atr_pct"):
        val = raw.get(key)
        if val is None:
            continue
        return float(val)
    if raw.get("atr") is not None and raw.get("last_close") not in (None, 0):
        return round(float(raw["atr"]) / float(raw["last_close"]) * 100.0, 4)
    return None


def normalize_source_name(value: Optional[str], fallback: str) -> str:
    if not value:
        return fallback
    return str(value).strip()


def validate_retrieved_at_utc(value: Optional[str]) -> Optional[str]:
    if value is None:
        return None
    value = str(value).strip()
    if not UTC_RE.match(value):
        raise ValueError(f"retrieved_at_utc must be YYYY-MM-DDTHH:MM:SSZ, got {value!r}")
    return value


def compute_freshness_ok(price_asof: Optional[str], target_close_date: str, same_day_close_required: bool) -> bool:
    if price_asof is None:
        return False
    if same_day_close_required:
        return price_asof == target_close_date
    return price_asof <= target_close_date


def determine_data_gap(raw: Dict[str, Any]) -> Optional[str]:
    required = ["ticker", "last_close", "price_asof"]
    missing = [field for field in required if raw.get(field) in (None, "")]
    if missing:
        return f"missing required fields: {', '.join(missing)}"
    return None


def normalize_ticker_entry(raw: Dict[str, Any], target_close_date: str, default_primary_source: str) -> Dict[str, Any]:
    if not isinstance(raw, dict):
        raise ValueError("Each ticker entry must be a JSON object")

    ticker = str(raw.get("ticker", "")).strip().upper()
    data_gap_reason = determine_data_gap(raw)

    price_asof = raw.get("price_asof")
    if price_asof not in (None, ""):
        price_asof = ensure_date(str(price_asof), "price_asof")
    else:
        price_asof = None

    same_day_close_required = bool(raw.get("same_day_close_required", False))
    freshness_ok = compute_freshness_ok(price_asof, target_close_date, same_day_close_required)

    atr_pct = choose_atr_pct(raw)
    notes = raw.get("notes") if isinstance(raw.get("notes"), list) else []

    if data_gap_reason is None and not freshness_ok:
        data_gap_reason = (
            f"freshness check failed: price_asof={price_asof} target_close={target_close_date} "
            f"same_day_close_required={same_day_close_required}"
        )

    last_close_source = normalize_source_name(raw.get("last_close_source"), default_primary_source)
    price_asof_source = normalize_source_name(raw.get("price_asof_source"), last_close_source)
    technicals_source = normalize_source_name(raw.get("technicals_source"), last_close_source)

    return {
        "ticker": ticker,
        "last_close": None if raw.get("last_close") in (None, "") else float(raw["last_close"]),
        "price_asof": price_asof,
        "atr_20_30d_pct": atr_pct,
        "ma50": None if raw.get("ma50") in (None, "") else float(raw["ma50"]),
        "ma200": None if raw.get("ma200") in (None, "") else float(raw["ma200"]),
        "avg_volume_3m": None if raw.get("avg_volume_3m") in (None, "") else int(raw["avg_volume_3m"]),
        "last_close_source": last_close_source,
        "price_asof_source": price_asof_source,
        "technicals_source": technicals_source,
        "retrieved_at_utc": validate_retrieved_at_utc(raw.get("retrieved_at_utc")),
        "same_day_close_required": same_day_close_required,
        "freshness_ok": data_gap_reason is None,
        "data_gap": data_gap_reason is not None,
        "data_gap_reason": data_gap_reason,
        "notes": notes,
    }


def build_snapshot(raw: Dict[str, Any], run_timestamp_override: Optional[str], target_close_override: Optional[str]) -> Dict[str, Any]:
    run_timestamp_text = run_timestamp_override or raw.get("run_timestamp_et")
    if not run_timestamp_text:
        raise ValueError("run_timestamp_et is required either in raw input or via --run-timestamp-et")

    run_dt = parse_run_timestamp_et(str(run_timestamp_text))
    execution_date_et = run_dt.date().isoformat()
    target_close_date = target_close_override or raw.get("market_data_target_close_date_et") or execution_date_et
    target_close_date = ensure_date(str(target_close_date), "market_data_target_close_date_et")

    tickers_raw = raw.get("tickers")
    if not isinstance(tickers_raw, list) or not tickers_raw:
        raise ValueError("raw_input.tickers must be a non-empty array")

    primary_source = str(raw.get("primary_source") or "Barchart")
    fallback_source = str(raw.get("fallback_source_for_last_close_and_price_asof_only") or "Stooq")

    tickers = [normalize_ticker_entry(t, target_close_date, primary_source) for t in tickers_raw]
    tickers.sort(key=lambda x: x["ticker"])

    return {
        "schema_version": "1.0",
        "snapshot_type": "MARKET_DATA_SNAPSHOT",
        "run_timestamp_et": run_dt.strftime("%Y-%m-%d %H:%M ET"),
        "execution_date_et": execution_date_et,
        "market_data_target_close_date_et": target_close_date,
        "close_time_zone": "America/New_York",
        "display_time_zone": "America/Los_Angeles",
        "primary_source": primary_source,
        "fallback_source_for_last_close_and_price_asof_only": fallback_source,
        "holiday_aware_close_resolution": bool(raw.get("holiday_aware_close_resolution", True)),
        "tickers": tickers,
    }


def write_json(path: Path, data: Dict[str, Any], pretty: bool) -> None:
    with path.open("w", encoding="utf-8") as f:
        if pretty:
            json.dump(data, f, ensure_ascii=False, indent=2)
        else:
            json.dump(data, f, ensure_ascii=False, separators=(",", ":"))


def build_market_data_snapshot(
    *,
    raw_input_path: str | Path,
    output_path: str | Path,
    run_timestamp_et: Optional[str] = None,
    target_close_date_et: Optional[str] = None,
    pretty: bool = True,
) -> Dict[str, Any]:
    """Build and write market_data_snapshot.json."""
    raw = load_json(Path(raw_input_path))
    snapshot = build_snapshot(raw, run_timestamp_et, target_close_date_et)
    validate_artifact_schema(snapshot, schema_name="market_data_snapshot.schema.json")
    write_json(Path(output_path), snapshot, pretty)
    return snapshot


def main() -> None:
    args = parse_args()
    raw = load_json(Path(args.raw_input))
    snapshot = build_snapshot(raw, args.run_timestamp_et, args.target_close_date_et)
    validate_artifact_schema(snapshot, schema_name="market_data_snapshot.schema.json")
    write_json(Path(args.output), snapshot, args.pretty)


if __name__ == "__main__":
    main()
