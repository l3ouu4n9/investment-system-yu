#!/usr/bin/env python3
"""Generate market_data_raw.json for deterministic layer.

Fetches daily market data using yfinance and writes the raw JSON expected by
build_market_data_snapshot.py.

Key behaviors:
- Reads tickers from research_json.json, a plain-text file, or CLI list
- Computes last close, price_asof, ATR 30D / ATR 20D %, MA50, MA200,
  avg_volume_3m
- Approximates target close date using ET time and weekday (weekend aware,
  not holiday aware). Use --target-close-date-et to override on holidays.
- Produces market_data_raw.json with deterministic, template-friendly fields

Install dependency on Windows if needed:
    pip install yfinance pandas
"""
from __future__ import annotations

import argparse
import json
import math
from dataclasses import dataclass
from datetime import datetime, date, time, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence
from zoneinfo import ZoneInfo

try:
    import pandas as pd
    import yfinance as yf
except Exception as exc:  # pragma: no cover
    pd = None  # type: ignore
    yf = None  # type: ignore
    IMPORT_ERROR: Exception | None = exc
else:
    IMPORT_ERROR = None

ET = ZoneInfo("America/New_York")
UTC = ZoneInfo("UTC")


@dataclass
class FetchResult:
    ticker: str
    last_close: Optional[float]
    price_asof: Optional[str]
    atr_30d_pct: Optional[float]
    atr_20d_pct: Optional[float]
    ma50: Optional[float]
    ma200: Optional[float]
    avg_volume_3m: Optional[int]
    notes: List[str]
    data_ok: bool


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Generate market_data_raw.json from yfinance")
    source = p.add_mutually_exclusive_group(required=True)
    source.add_argument("--research-json", help="Path to research_json.json; uses seed_tickers by default")
    source.add_argument("--tickers-file", help="Plain text file with one ticker per line")
    source.add_argument("--tickers", nargs="+", help="Ticker list, e.g. NVDA AVGO VRT")
    p.add_argument("--output", required=True, help="Output market_data_raw.json path")
    p.add_argument("--run-timestamp-et", help='Override run timestamp ET, e.g. "2026-04-13 21:30 ET"')
    p.add_argument("--target-close-date-et", help="Override target close date (YYYY-MM-DD)")
    p.add_argument("--period", default="1y", help="History period for indicators, default 1y")
    p.add_argument("--primary-source", default="YahooFinance", help="Value to stamp into *_source fields")
    p.add_argument("--fallback-source", default="Stooq", help="Fallback source label only")
    p.add_argument("--same-day-close-all", action="store_true", help="Force same_day_close_required=true for all tickers")
    p.add_argument("--pretty", action="store_true", help="Pretty print output JSON")
    return p.parse_args()


def fail(msg: str) -> None:
    raise SystemExit(msg)


def now_et_string() -> str:
    return datetime.now(ET).strftime("%Y-%m-%d %H:%M ET")


def parse_run_timestamp_et(text: str) -> datetime:
    text = text.strip().replace(" ET", "")
    for fmt in ("%Y-%m-%d %H:%M", "%Y-%m-%dT%H:%M"):
        try:
            dt = datetime.strptime(text, fmt)
            return dt.replace(tzinfo=ET)
        except ValueError:
            continue
    raise ValueError(f"Invalid --run-timestamp-et: {text!r}")


def previous_weekday(d: date) -> date:
    d = d - timedelta(days=1)
    while d.weekday() >= 5:
        d -= timedelta(days=1)
    return d


def infer_target_close_date(run_dt: datetime) -> date:
    # Weekend runs -> previous Friday. Weekdays before/at 16:10 -> previous weekday.
    cutoff = time(16, 10)
    local_date = run_dt.date()
    if local_date.weekday() >= 5:
        while local_date.weekday() >= 5:
            local_date -= timedelta(days=1)
        return local_date
    if run_dt.timetz().replace(tzinfo=None) <= cutoff:
        return previous_weekday(local_date)
    return local_date


def load_research_tickers(path: Path) -> List[str]:
    data = json.loads(path.read_text(encoding="utf-8"))
    tickers: List[str] = []
    if isinstance(data, dict):
        if isinstance(data.get("seed_tickers"), list):
            tickers = [str(x).strip().upper() for x in data["seed_tickers"] if str(x).strip()]
        elif isinstance(data.get("alpha_ranking_2_4y"), list):
            tickers = [str(row.get("ticker", "")).strip().upper() for row in data["alpha_ranking_2_4y"] if isinstance(row, dict)]
    return sorted(dict.fromkeys([t for t in tickers if t]))


def load_tickers_file(path: Path) -> List[str]:
    lines = [ln.strip().upper() for ln in path.read_text(encoding="utf-8").splitlines()]
    tickers = [ln for ln in lines if ln and not ln.startswith("#")]
    return sorted(dict.fromkeys(tickers))


def get_tickers(args: argparse.Namespace) -> List[str]:
    if args.research_json:
        tickers = load_research_tickers(Path(args.research_json))
    elif args.tickers_file:
        tickers = load_tickers_file(Path(args.tickers_file))
    else:
        tickers = sorted(dict.fromkeys([str(t).strip().upper() for t in args.tickers if str(t).strip()]))
    if not tickers:
        fail("No tickers found.")
    return tickers


def safe_float(value: Any) -> Optional[float]:
    try:
        if value is None or (isinstance(value, float) and math.isnan(value)):
            return None
        return float(value)
    except Exception:
        return None


def safe_int(value: Any) -> Optional[int]:
    try:
        if value is None or (isinstance(value, float) and math.isnan(value)):
            return None
        return int(round(float(value)))
    except Exception:
        return None


def compute_atr_pct(df: "pd.DataFrame", window: int) -> Optional[float]:
    if df is None or len(df) < window + 1:
        return None
    high = df["High"].astype(float)
    low = df["Low"].astype(float)
    close = df["Close"].astype(float)
    prev_close = close.shift(1)
    tr = pd.concat([(high - low), (high - prev_close).abs(), (low - prev_close).abs()], axis=1).max(axis=1)
    atr = tr.rolling(window=window).mean().iloc[-1]
    last_close = close.iloc[-1]
    if pd.isna(atr) or pd.isna(last_close) or float(last_close) == 0.0:
        return None
    return round(float(atr) / float(last_close) * 100.0, 4)


def fetch_one(ticker: str, period: str) -> FetchResult:
    notes: List[str] = []
    try:
        hist = yf.download(ticker, period=period, interval="1d", auto_adjust=False, progress=False, threads=False)
    except Exception as exc:
        return FetchResult(ticker, None, None, None, None, None, None, None, [f"download_error: {exc}"], False)

    if hist is None or hist.empty:
        return FetchResult(ticker, None, None, None, None, None, None, None, ["empty_history"], False)

    # Flatten possible multi-index columns from yfinance
    if hasattr(hist.columns, "levels"):
        hist.columns = [c[0] if isinstance(c, tuple) else c for c in hist.columns]

    needed = {"Open", "High", "Low", "Close", "Volume"}
    missing = [c for c in needed if c not in hist.columns]
    if missing:
        return FetchResult(ticker, None, None, None, None, None, None, None, [f"missing_columns: {missing}"], False)

    hist = hist.dropna(subset=["Close"]).copy()
    if hist.empty:
        return FetchResult(ticker, None, None, None, None, None, None, None, ["no_valid_close_rows"], False)

    last_close = safe_float(hist["Close"].iloc[-1])
    idx = hist.index[-1]
    if hasattr(idx, "date"):
        price_asof = idx.date().isoformat()
    else:
        price_asof = str(idx)[:10]

    atr_30d_pct = compute_atr_pct(hist, 30)
    atr_20d_pct = compute_atr_pct(hist, 20)
    ma50 = safe_float(hist["Close"].rolling(50).mean().iloc[-1]) if len(hist) >= 50 else None
    ma200 = safe_float(hist["Close"].rolling(200).mean().iloc[-1]) if len(hist) >= 200 else None
    avg_volume_3m = safe_int(hist["Volume"].tail(63).mean()) if len(hist) >= 5 else None

    if atr_30d_pct is None and atr_20d_pct is None:
        notes.append("atr_missing")
    if ma50 is None:
        notes.append("ma50_missing")
    if ma200 is None:
        notes.append("ma200_missing")

    return FetchResult(
        ticker=ticker,
        last_close=last_close,
        price_asof=price_asof,
        atr_30d_pct=atr_30d_pct,
        atr_20d_pct=atr_20d_pct,
        ma50=ma50,
        ma200=ma200,
        avg_volume_3m=avg_volume_3m,
        notes=notes,
        data_ok=last_close is not None and price_asof is not None,
    )


def build_output(args: argparse.Namespace, tickers: Sequence[str]) -> Dict[str, Any]:
    if IMPORT_ERROR is not None:
        fail(f"Missing dependency. Install with: pip install yfinance pandas\nOriginal import error: {IMPORT_ERROR}")

    run_dt = parse_run_timestamp_et(args.run_timestamp_et) if args.run_timestamp_et else parse_run_timestamp_et(now_et_string())
    target_close = date.fromisoformat(args.target_close_date_et) if args.target_close_date_et else infer_target_close_date(run_dt)
    retrieved_at_utc = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")

    out_rows: List[Dict[str, Any]] = []
    for ticker in tickers:
        res = fetch_one(ticker, args.period)
        same_day_close_required = bool(args.same_day_close_all or (run_dt.date() == target_close and run_dt.timetz().replace(tzinfo=None) > time(16, 10)))
        row = {
            "ticker": ticker,
            "last_close": res.last_close,
            "price_asof": res.price_asof,
            "atr_30d_pct": res.atr_30d_pct,
            "atr_20d_pct": res.atr_20d_pct,
            "ma50": res.ma50,
            "ma200": res.ma200,
            "avg_volume_3m": res.avg_volume_3m,
            "last_close_source": args.primary_source,
            "price_asof_source": args.primary_source,
            "technicals_source": args.primary_source,
            "retrieved_at_utc": retrieved_at_utc,
            "same_day_close_required": same_day_close_required,
            "notes": res.notes,
        }
        out_rows.append(row)

    return {
        "run_timestamp_et": run_dt.strftime("%Y-%m-%d %H:%M ET"),
        "market_data_target_close_date_et": target_close.isoformat(),
        "primary_source": args.primary_source,
        "fallback_source_for_last_close_and_price_asof_only": args.fallback_source,
        "tickers": out_rows,
    }


def generate_market_data_raw(
    *,
    output_path: str | Path,
    research_json_path: str | Path | None = None,
    tickers_file_path: str | Path | None = None,
    tickers: Sequence[str] | None = None,
    run_timestamp_et: str | None = None,
    target_close_date_et: str | None = None,
    period: str = "1y",
    primary_source: str = "YahooFinance",
    fallback_source: str = "Stooq",
    same_day_close_all: bool = False,
    pretty: bool = True,
) -> Dict[str, Any]:
    """Generate and write market_data_raw.json."""
    args = argparse.Namespace(
        research_json=str(research_json_path) if research_json_path else None,
        tickers_file=str(tickers_file_path) if tickers_file_path else None,
        tickers=list(tickers or []),
        output=str(output_path),
        run_timestamp_et=run_timestamp_et,
        target_close_date_et=target_close_date_et,
        period=period,
        primary_source=primary_source,
        fallback_source=fallback_source,
        same_day_close_all=same_day_close_all,
        pretty=pretty,
    )
    selected_tickers = get_tickers(args)
    output = build_output(args, selected_tickers)
    out_path = Path(output_path)
    with out_path.open("w", encoding="utf-8") as f:
        if pretty:
            json.dump(output, f, ensure_ascii=False, indent=2)
        else:
            json.dump(output, f, ensure_ascii=False, separators=(",", ":"))
    return output


def main() -> None:
    args = parse_args()
    tickers = get_tickers(args)
    output = build_output(args, tickers)
    out_path = Path(args.output)
    with out_path.open("w", encoding="utf-8") as f:
        if args.pretty:
            json.dump(output, f, ensure_ascii=False, indent=2)
        else:
            json.dump(output, f, ensure_ascii=False, separators=(",", ":"))
    print(f"[OK] wrote {out_path}")


if __name__ == "__main__":
    main()
