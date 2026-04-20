#!/usr/bin/env python3
"""Generate anchor_state_input.json from a simple order-state text/CSV file.

Purpose:
- Turn your maintained buy-open-order anchor state into the exact JSON expected
  by build_anchor_drift_snapshot.py
- Supports pipe-delimited, CSV, or TSV input
- Computes age_days_since_last_refresh from anchor_price_asof if not provided

Accepted column aliases (case-insensitive):
- ticker / TICKER
- side
- old_baseline / anchor_baseline_last_close
- anchor_price_asof / baseline_price_asof / price_asof
- age_days_since_last_refresh / age_days
- age_upgrade_applied

Example pipe-delimited input:
TICKER | side | anchor_baseline_last_close | anchor_price_asof | age_upgrade_applied
NVDA   | buy  | 118.00                     | 2026-04-04        | false
AVGO   | buy  | 187.40                     | 2026-04-06        | false
"""
from __future__ import annotations

import argparse
import csv
import json
from datetime import datetime, date
from pathlib import Path
from typing import Any, Dict, List, Tuple


ALIASES = {
    "ticker": {"ticker", "TICKER"},
    "side": {"side", "SIDE"},
    "old_baseline": {"old_baseline", "anchor_baseline_last_close", "baseline", "anchor_baseline", "OLD_BASELINE"},
    "anchor_price_asof": {"anchor_price_asof", "baseline_price_asof", "price_asof", "ANCHOR_PRICE_ASOF"},
    "age_days_since_last_refresh": {"age_days_since_last_refresh", "age_days", "AGE_DAYS_SINCE_LAST_REFRESH"},
    "age_upgrade_applied": {"age_upgrade_applied", "AGE_UPGRADE_APPLIED"},
}


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Generate anchor_state_input.json")
    p.add_argument("--input", required=True, help="Path to pipe/CSV/TSV order state file")
    p.add_argument("--output", required=True, help="Output anchor_state_input.json path")
    p.add_argument("--run-timestamp-et", required=True, help='Run timestamp ET, e.g. "2026-04-13 21:30 ET"')
    p.add_argument("--keep-floor-pct", type=float, default=2.0)
    p.add_argument("--keep-atr-mult", type=float, default=0.75)
    p.add_argument("--mini-floor-pct", type=float, default=5.0)
    p.add_argument("--mini-atr-mult", type=float, default=1.5)
    p.add_argument("--pretty", action="store_true")
    return p.parse_args()


def parse_run_date(text: str) -> date:
    text = text.strip().replace(" ET", "")
    for fmt in ("%Y-%m-%d %H:%M", "%Y-%m-%dT%H:%M"):
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    raise ValueError(f"Invalid --run-timestamp-et: {text!r}")


def detect_delimiter(header_line: str) -> str:
    if "|" in header_line:
        return "|"
    if "\t" in header_line:
        return "\t"
    return ","


def canonicalize_header(name: str) -> str:
    stripped = name.strip()
    for canon, aliases in ALIASES.items():
        if stripped in aliases:
            return canon
    return stripped.lower()


def parse_bool(value: Any) -> bool:
    return str(value).strip().lower() in {"1", "true", "yes", "y"}


def read_rows(path: Path) -> Tuple[List[str], List[Dict[str, str]]]:
    text = path.read_text(encoding="utf-8")
    lines = [ln for ln in text.splitlines() if ln.strip() and not ln.strip().startswith("#")]
    if not lines:
        raise ValueError("Input file is empty")
    delim = detect_delimiter(lines[0])
    reader = csv.DictReader(lines, delimiter=delim, skipinitialspace=True)
    if reader.fieldnames is None:
        raise ValueError("Could not parse header row")
    fieldnames = [canonicalize_header(fn) for fn in reader.fieldnames]
    rows: List[Dict[str, str]] = []
    for raw in reader:
        row: Dict[str, str] = {}
        for orig, canon in zip(reader.fieldnames, fieldnames):
            row[canon] = (raw.get(orig) or "").strip()
        rows.append(row)
    return fieldnames, rows


def compute_age(run_date: date, anchor_price_asof: str) -> int:
    d = date.fromisoformat(anchor_price_asof)
    return max(0, (run_date - d).days)


def build_output(args: argparse.Namespace) -> Dict[str, Any]:
    run_date = parse_run_date(args.run_timestamp_et)
    _, rows = read_rows(Path(args.input))
    out_rows: List[Dict[str, Any]] = []
    for row in rows:
        ticker = row.get("ticker", "").upper()
        if not ticker:
            continue
        side = (row.get("side") or "buy").lower()
        old_baseline_raw = row.get("old_baseline", "")
        if old_baseline_raw == "":
            raise ValueError(f"Missing old_baseline for {ticker}")
        old_baseline = float(old_baseline_raw)

        age_raw = row.get("age_days_since_last_refresh", "")
        anchor_price_asof = row.get("anchor_price_asof", "")
        if age_raw != "":
            age_days = int(float(age_raw))
        elif anchor_price_asof:
            age_days = compute_age(run_date, anchor_price_asof)
        else:
            age_days = 0

        out_rows.append({
            "ticker": ticker,
            "side": side,
            "old_baseline": old_baseline,
            "age_days_since_last_refresh": age_days,
            "age_upgrade_applied": parse_bool(row.get("age_upgrade_applied", "false")),
        })

    if not out_rows:
        raise ValueError("No usable rows found in input file")
    out_rows.sort(key=lambda x: x["ticker"])

    return {
        "run_timestamp_et": args.run_timestamp_et,
        "thresholds": {
            "keep_floor_pct": args.keep_floor_pct,
            "keep_atr_mult": args.keep_atr_mult,
            "mini_floor_pct": args.mini_floor_pct,
            "mini_atr_mult": args.mini_atr_mult,
        },
        "tickers": out_rows,
    }


def generate_anchor_state_input(
    *,
    input_path: str | Path,
    output_path: str | Path,
    run_timestamp_et: str,
    keep_floor_pct: float = 2.0,
    keep_atr_mult: float = 0.75,
    mini_floor_pct: float = 5.0,
    mini_atr_mult: float = 1.5,
    pretty: bool = True,
) -> Dict[str, Any]:
    """Generate and write anchor_state_input.json."""
    args = argparse.Namespace(
        input=str(input_path),
        output=str(output_path),
        run_timestamp_et=run_timestamp_et,
        keep_floor_pct=keep_floor_pct,
        keep_atr_mult=keep_atr_mult,
        mini_floor_pct=mini_floor_pct,
        mini_atr_mult=mini_atr_mult,
        pretty=pretty,
    )
    output = build_output(args)
    out_path = Path(output_path)
    with out_path.open("w", encoding="utf-8") as f:
        if pretty:
            json.dump(output, f, ensure_ascii=False, indent=2)
        else:
            json.dump(output, f, ensure_ascii=False, separators=(",", ":"))
    return output


def main() -> None:
    args = parse_args()
    output = build_output(args)
    out_path = Path(args.output)
    with out_path.open("w", encoding="utf-8") as f:
        if args.pretty:
            json.dump(output, f, ensure_ascii=False, indent=2)
        else:
            json.dump(output, f, ensure_ascii=False, separators=(",", ":"))
    print(f"[OK] wrote {out_path}")


if __name__ == "__main__":
    main()
