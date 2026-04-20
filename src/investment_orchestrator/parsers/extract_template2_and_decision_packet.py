"""Extract Template 2 text and DECISION_PACKET from a manual Step 2 output."""

from __future__ import annotations

import json
from pathlib import Path
import re
from typing import Any

import yaml

from investment_orchestrator.common.io import read_text, write_json, write_text
from investment_orchestrator.common.paths import repo_root
from investment_orchestrator.validators.validate_decision_packet import validate_decision_packet


class Step2ExtractionError(ValueError):
    """Raised when a Step 2 raw output cannot be parsed safely."""


TOP_LEVEL_PACKET_KEY_RE = re.compile(r"^(?P<key>[A-Za-z0-9_]+):(?:\s*(?P<inline>.*))?$")
CITATION_TAIL_RE = re.compile(r"\s*\(\[[^\]]+\]\[\d+\]\)\.?\s*$")
KV_SEGMENT_RE = re.compile(r"(?P<key>[A-Za-z0-9_]+)=(?P<value>.*)")


def strip_code_fence(text: str) -> str:
    """Remove one surrounding Markdown fence when present."""
    stripped = text.strip()
    if not stripped.startswith("```"):
        return stripped

    lines = stripped.splitlines()
    if len(lines) < 3 or not lines[-1].strip().startswith("```"):
        return stripped
    return "\n".join(lines[1:-1]).strip()


def extract_required_block(text: str, start_marker: str, end_marker: str) -> str:
    """Return the text between two required markers."""
    start = text.find(start_marker)
    if start == -1:
        raise Step2ExtractionError(f"Missing required marker {start_marker!r}.")
    end = text.rfind(end_marker)
    if end == -1 or end <= start:
        raise Step2ExtractionError(f"Missing or malformed closing marker {end_marker!r}.")
    return text[start + len(start_marker) : end].strip()


def _clean_value_text(value: str) -> str:
    """Trim surrounding whitespace and common citation tails."""
    return CITATION_TAIL_RE.sub("", value.strip()).strip()


def _clean_terminal_punctuation(value: Any) -> Any:
    """Trim harmless trailing punctuation from simple string labels."""
    if not isinstance(value, str):
        return value
    cleaned = value.strip()
    if re.fullmatch(r"[A-Za-z0-9_.%-]+[.]", cleaned):
        return cleaned[:-1]
    return cleaned


def _maybe_number(value: str) -> Any:
    """Convert a numeric-looking string to int/float when safe."""
    cleaned = value.replace(",", "").strip()
    if not cleaned:
        return ""
    if re.fullmatch(r"-?\d+", cleaned):
        return int(cleaned)
    if re.fullmatch(r"-?\d+\.\d+", cleaned):
        return float(cleaned)
    return value.strip()


def _parse_semicolon_kv_line(text: str) -> dict[str, Any]:
    """Parse `a=1; b=2` style segments into a mapping."""
    parsed: dict[str, Any] = {}
    for segment in [part.strip() for part in text.split(";") if part.strip()]:
        match = KV_SEGMENT_RE.fullmatch(segment)
        if not match:
            parsed.setdefault("_raw", []).append(_clean_value_text(segment))
            continue
        key = match.group("key")
        value = _clean_value_text(match.group("value"))
        parsed[key] = _maybe_number(value)
    return parsed


def _packet_lines_to_sections(text: str) -> dict[str, Any]:
    """Split the outline-style DECISION_PACKET into top-level sections."""
    sections: dict[str, Any] = {}
    current_key: str | None = None
    current_lines: list[str] = []

    def flush() -> None:
        nonlocal current_key, current_lines
        if current_key is None:
            return
        while current_lines and not current_lines[-1].strip():
            current_lines.pop()
        sections[current_key] = current_lines[:]
        current_key = None
        current_lines = []

    for raw_line in text.splitlines():
        line = raw_line.rstrip()
        stripped = line.strip()
        if not stripped:
            if current_key is not None:
                current_lines.append("")
            continue

        match = TOP_LEVEL_PACKET_KEY_RE.fullmatch(line)
        if match and not line.startswith((" ", "\t", "*")):
            flush()
            current_key = match.group("key")
            inline_value = match.group("inline") or ""
            current_lines = [inline_value] if inline_value else []
            continue

        if current_key is None:
            continue
        current_lines.append(line)

    flush()
    return sections


def _section_to_bullets(lines: list[str]) -> list[str]:
    """Extract top-level bullet lines from a section."""
    bullets: list[str] = []
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("* "):
            bullets.append(stripped[2:].strip())
    return bullets


def _load_current_strategy_settings() -> dict[str, Any]:
    """Load current strategy settings for fallback packet normalization."""
    path = repo_root() / "inputs" / "current" / "strategy_settings.yaml"
    if not path.exists():
        return {}
    payload = yaml.safe_load(read_text(path))
    return payload if isinstance(payload, dict) else {}


def _infer_execution_date_et(context_text: str) -> str:
    """Infer execution date from current settings or context text."""
    settings = _load_current_strategy_settings()
    as_of = settings.get("as_of")
    if isinstance(as_of, str) and re.fullmatch(r"\d{4}-\d{2}-\d{2}", as_of):
        return as_of

    match = re.search(r"\bas_of:\s*(\d{4}-\d{2}-\d{2})\b", context_text)
    if match:
        return match.group(1)
    return "1970-01-01"


def _infer_market_target_close_date(context_text: str, execution_date_et: str) -> str:
    """Infer market target close date from textual context when present."""
    match = re.search(r"(\d{4}-\d{2}-\d{2}) regular-session close", context_text)
    if match:
        return match.group(1)
    match = re.search(r'"as_of_et"\s*:\s*"(\d{4}-\d{2}-\d{2})"', context_text)
    if match:
        return match.group(1)
    return execution_date_et


def _parse_effective_allowed_buy_universe(lines: list[str]) -> list[str]:
    """Parse the buy-universe bullet list."""
    return [item for item in _section_to_bullets(lines) if item]


def _normalize_snapshot_ticker_row(
    ticker: str,
    raw_row: dict[str, Any],
    *,
    target_close_date_et: str,
    primary_source: str,
) -> dict[str, Any]:
    """Normalize one ticker row into market_data_snapshot.schema.json shape."""
    atr_pct = raw_row.get("atr_20_30d_pct")
    if atr_pct is None:
        atr_pct = raw_row.get("atr_20_pct")

    ma50 = raw_row.get("ma50")
    if ma50 is None:
        ma50 = raw_row.get("ma_50")

    ma200 = raw_row.get("ma200")
    if ma200 is None:
        ma200 = raw_row.get("ma_200")

    avg_volume = raw_row.get("avg_volume_3m")
    if avg_volume is None:
        avg_volume = raw_row.get("avg_volume_20d")
    if isinstance(avg_volume, float) and avg_volume.is_integer():
        avg_volume = int(avg_volume)

    notes = raw_row.get("notes")
    if not isinstance(notes, list):
        notes = []

    extra_note_keys = ("source_note", "source_citation", "trend_state", "liquidity_tier")
    for key in extra_note_keys:
        value = raw_row.get(key)
        if isinstance(value, str) and value.strip():
            notes.append(f"{key}: {value.strip()}")

    return {
        "ticker": ticker,
        "last_close": raw_row.get("last_close"),
        "price_asof": raw_row.get("price_asof", target_close_date_et),
        "atr_20_30d_pct": atr_pct,
        "ma50": ma50,
        "ma200": ma200,
        "avg_volume_3m": avg_volume if isinstance(avg_volume, int) else avg_volume,
        "last_close_source": raw_row.get("last_close_source", primary_source),
        "price_asof_source": raw_row.get("price_asof_source", primary_source),
        "technicals_source": raw_row.get("technicals_source", primary_source),
        "retrieved_at_utc": raw_row.get("retrieved_at_utc"),
        "same_day_close_required": bool(raw_row.get("same_day_close_required", False)),
        "freshness_ok": bool(raw_row.get("freshness_ok", True)),
        "data_gap": bool(raw_row.get("data_gap", False)),
        "data_gap_reason": raw_row.get("data_gap_reason"),
        "notes": notes,
        "atr_20_abs": raw_row.get("atr_20_abs", raw_row.get("atr_20")),
        "range_52w": raw_row.get("range_52w"),
        "week_52_high": raw_row.get("week_52_high"),
        "week_52_low": raw_row.get("week_52_low"),
        "pct_above_ma50": raw_row.get("pct_above_ma50"),
        "pct_above_ma200": raw_row.get("pct_above_ma200"),
        "pct_from_52w_high": raw_row.get("pct_from_52w_high"),
        "pct_above_52w_low": raw_row.get("pct_above_52w_low"),
        "trend_state": raw_row.get("trend_state"),
        "liquidity_tier": _clean_terminal_punctuation(raw_row.get("liquidity_tier")),
        "quadrant": _clean_terminal_punctuation(raw_row.get("quadrant")),
        "liquidity": _clean_terminal_punctuation(raw_row.get("liquidity")),
    }


def normalize_market_data_snapshot(payload: Any, *, context_text: str) -> dict[str, Any]:
    """Normalize multiple plausible MARKET_DATA_SNAPSHOT shapes to the schema shape."""
    if not isinstance(payload, dict):
        raise Step2ExtractionError("MARKET_DATA_SNAPSHOT must be a JSON object.")

    if payload.get("schema_version") == "1.0" and payload.get("snapshot_type") == "MARKET_DATA_SNAPSHOT":
        return payload

    execution_date_et = _infer_execution_date_et(context_text)
    target_close_date_et = _infer_market_target_close_date(context_text, execution_date_et)
    primary_source = str(payload.get("primary_source") or "Barchart")
    fallback_source = str(payload.get("fallback_source_for_last_close_and_price_asof_only") or "Stooq")

    raw_tickers = payload.get("tickers")
    ticker_rows: list[dict[str, Any]] = []
    if isinstance(raw_tickers, dict):
        for ticker, raw_row in raw_tickers.items():
            if isinstance(raw_row, dict):
                ticker_rows.append(
                    _normalize_snapshot_ticker_row(
                        str(ticker).upper(),
                        raw_row,
                        target_close_date_et=target_close_date_et,
                        primary_source=primary_source,
                    )
                )
    elif isinstance(raw_tickers, list):
        for raw_row in raw_tickers:
            if not isinstance(raw_row, dict):
                continue
            ticker = str(raw_row.get("ticker", "")).upper().strip()
            if not ticker:
                continue
                ticker_rows.append(
                    _normalize_snapshot_ticker_row(
                        ticker,
                        raw_row,
                        target_close_date_et=target_close_date_et,
                        primary_source=primary_source,
                    )
                )

    if not ticker_rows:
        top_level_ticker_map: dict[str, dict[str, Any]] = {}
        for key, value in payload.items():
            if not isinstance(value, dict):
                continue
            normalized_key = str(key).upper().strip()
            if not re.fullmatch(r"[A-Z][A-Z0-9.-]{0,9}", normalized_key):
                continue
            top_level_ticker_map[normalized_key] = value

        for ticker, raw_row in top_level_ticker_map.items():
            ticker_primary_source = str(raw_row.get("source_primary") or primary_source)
            ticker_rows.append(
                _normalize_snapshot_ticker_row(
                    ticker,
                    raw_row,
                    target_close_date_et=target_close_date_et,
                    primary_source=ticker_primary_source,
                )
            )

    if not ticker_rows:
        raise Step2ExtractionError("MARKET_DATA_SNAPSHOT did not contain any usable ticker rows.")

    return {
        "schema_version": "1.0",
        "snapshot_type": "MARKET_DATA_SNAPSHOT",
        "run_timestamp_et": str(payload.get("run_timestamp_et") or f"{execution_date_et} 00:00 ET"),
        "execution_date_et": str(payload.get("execution_date_et") or execution_date_et),
        "market_data_target_close_date_et": str(
            payload.get("market_data_target_close_date_et")
            or payload.get("as_of_et")
            or target_close_date_et
        ),
        "close_time_zone": str(payload.get("close_time_zone") or "America/New_York"),
        "display_time_zone": str(payload.get("display_time_zone") or "America/Los_Angeles"),
        "primary_source": primary_source,
        "fallback_source_for_last_close_and_price_asof_only": fallback_source,
        "holiday_aware_close_resolution": bool(payload.get("holiday_aware_close_resolution", True)),
        "tickers": ticker_rows,
        "session_type": payload.get("session_type"),
        "source_note": payload.get("source_note"),
    }


def _parse_market_data_snapshot(lines: list[str], *, context_text: str) -> dict[str, Any]:
    """Build a schema-compatible MARKET_DATA_SNAPSHOT from outline bullets."""
    execution_date_et = _infer_execution_date_et(context_text)
    target_close_date_et = _infer_market_target_close_date(context_text, execution_date_et)
    tickers: list[dict[str, Any]] = []

    for bullet in _section_to_bullets(lines):
        if ":" not in bullet:
            continue
        ticker, remainder = bullet.split(":", 1)
        ticker = ticker.strip().upper()
        fields = _parse_semicolon_kv_line(remainder)
        last_close = fields.get("last_close")
        ma50 = fields.get("MA50")
        ma200 = fields.get("MA200")
        atr20 = fields.get("ATR20")
        atr20_pct = fields.get("ATR20_pct")
        avg_volume_ref = fields.get("avg_volume_ref")
        range_52w = fields.get("range_52w")
        quadrant = fields.get("quadrant")
        liquidity = fields.get("liquidity")

        ticker_row = {
            "ticker": ticker,
            "last_close": last_close if isinstance(last_close, (int, float)) else None,
            "price_asof": target_close_date_et,
            "atr_20_30d_pct": (
                float(str(atr20_pct).replace("%", "")) if isinstance(atr20_pct, str) and atr20_pct else atr20_pct
            ),
            "ma50": ma50 if isinstance(ma50, (int, float)) else None,
            "ma200": ma200 if isinstance(ma200, (int, float)) else None,
            "avg_volume_3m": avg_volume_ref if isinstance(avg_volume_ref, int) else None,
            "last_close_source": "Barchart",
            "price_asof_source": "Barchart",
            "technicals_source": "Barchart",
            "retrieved_at_utc": None,
            "same_day_close_required": False,
            "freshness_ok": True,
            "data_gap": False,
            "data_gap_reason": None,
            "notes": [],
            "atr_20_abs": atr20 if isinstance(atr20, (int, float)) else atr20,
            "range_52w": range_52w,
            "quadrant": quadrant,
            "liquidity": liquidity,
        }
        tickers.append(ticker_row)

    if not tickers:
        raise Step2ExtractionError(
            "DECISION_PACKET outline parse failed: MARKET_DATA_SNAPSHOT section did not contain any ticker rows."
        )

    return {
        "schema_version": "1.0",
        "snapshot_type": "MARKET_DATA_SNAPSHOT",
        "run_timestamp_et": f"{execution_date_et} 00:00 ET",
        "execution_date_et": execution_date_et,
        "market_data_target_close_date_et": target_close_date_et,
        "close_time_zone": "America/New_York",
        "display_time_zone": "America/Los_Angeles",
        "primary_source": "Barchart",
        "fallback_source_for_last_close_and_price_asof_only": "Stooq",
        "holiday_aware_close_resolution": True,
        "tickers": tickers,
    }


def _parse_structured_list(lines: list[str]) -> list[Any]:
    """Parse common bullet-list sections into strings or key/value dictionaries."""
    output: list[Any] = []
    for bullet in _section_to_bullets(lines):
        if ";" in bullet or "=" in bullet:
            output.append(_parse_semicolon_kv_line(bullet))
            continue
        if ":" in bullet:
            left, right = bullet.split(":", 1)
            output.append(
                {
                    "label": left.strip(),
                    "value": _clean_value_text(right),
                }
            )
            continue
        output.append(_clean_value_text(bullet))
    return output


def parse_outline_decision_packet(text: str, *, context_text: str) -> dict[str, Any]:
    """Parse the outline-style DECISION_PACKET emitted by the current Step 2 prompt."""
    sections = _packet_lines_to_sections(strip_code_fence(text))
    if not sections:
        raise Step2ExtractionError("DECISION_PACKET is not valid JSON/YAML or supported outline text.")

    required = {
        "effective_allowed_buy_universe",
        "MARKET_DATA_SNAPSHOT",
        "active_shortlist",
        "buy_side_delta_table",
        "rotation_decision_layer_8_15",
        "sell_side_delta_table_8_2",
        "execution_plan_drafts_8_5",
        "sell_execution_plan_drafts_8_6",
        "assumptions_and_data_gaps",
        "decision_builder_ready_for_audit",
    }
    missing = [key for key in required if key not in sections]
    if missing:
        raise Step2ExtractionError(
            "DECISION_PACKET outline parse failed because required sections were missing: "
            + ", ".join(missing)
        )

    ready_lines = sections["decision_builder_ready_for_audit"]
    ready_text = ready_lines[0].strip().lower() if ready_lines else ""
    ready = ready_text == "true"

    return {
        "effective_allowed_buy_universe": _parse_effective_allowed_buy_universe(
            sections["effective_allowed_buy_universe"]
        ),
        "MARKET_DATA_SNAPSHOT": _parse_market_data_snapshot(sections["MARKET_DATA_SNAPSHOT"], context_text=context_text),
        "active_shortlist": _parse_structured_list(sections["active_shortlist"]),
        "buy_side_delta_table": _parse_structured_list(sections["buy_side_delta_table"]),
        "rotation_decision_layer_8_15": _parse_structured_list(sections["rotation_decision_layer_8_15"]),
        "sell_side_delta_table_8_2": _parse_structured_list(sections["sell_side_delta_table_8_2"]),
        "execution_plan_drafts_8_5": _parse_structured_list(sections["execution_plan_drafts_8_5"]),
        "sell_execution_plan_drafts_8_6": _parse_structured_list(sections["sell_execution_plan_drafts_8_6"]),
        "assumptions_and_data_gaps": _parse_structured_list(sections["assumptions_and_data_gaps"]),
        "decision_builder_ready_for_audit": ready,
    }


def parse_json_like_mapping(text: str, *, context_text: str = "") -> dict[str, Any]:
    """Parse JSON first, then YAML as a fallback, and require an object root."""
    cleaned = strip_code_fence(text)
    payload: Any

    try:
        payload = json.loads(cleaned)
    except json.JSONDecodeError:
        try:
            payload = yaml.safe_load(cleaned)
        except yaml.YAMLError as exc:
            try:
                return parse_outline_decision_packet(cleaned, context_text=context_text)
            except Step2ExtractionError:
                raise Step2ExtractionError(f"DECISION_PACKET is not valid JSON/YAML: {exc}") from exc

    if not isinstance(payload, dict):
        return parse_outline_decision_packet(cleaned, context_text=context_text)
    return payload


def normalize_decision_packet(payload: dict[str, Any], *, context_text: str) -> dict[str, Any]:
    """Normalize the parsed decision packet into the validator-ready shape."""
    normalized = dict(payload)
    if "MARKET_DATA_SNAPSHOT" in normalized:
        normalized["MARKET_DATA_SNAPSHOT"] = normalize_market_data_snapshot(
            normalized["MARKET_DATA_SNAPSHOT"],
            context_text=context_text,
        )
    return normalized


def parse_step2_output_text(raw_text: str) -> tuple[str, dict[str, Any]]:
    """Parse a raw Step 2 response into Template 2 text plus a validated decision packet."""
    template2_text = extract_required_block(
        raw_text,
        "TEMPLATE2_OUTPUT_START",
        "TEMPLATE2_OUTPUT_END",
    )
    decision_packet_block = extract_required_block(
        raw_text,
        "DECISION_PACKET_START",
        "DECISION_PACKET_END",
    )
    decision_packet = parse_json_like_mapping(
        decision_packet_block,
        context_text=template2_text + "\n\n" + decision_packet_block,
    )
    decision_packet = normalize_decision_packet(
        decision_packet,
        context_text=template2_text + "\n\n" + decision_packet_block,
    )
    validate_decision_packet(decision_packet)
    return template2_text, decision_packet


def extract_template2_and_decision_packet(
    *,
    raw_output_path: str | Path,
    template2_output_path: str | Path,
    decision_packet_path: str | Path,
) -> tuple[str, dict[str, Any]]:
    """Read, parse, validate, and write Step 2 artifacts."""
    template2_text, decision_packet = parse_step2_output_text(read_text(raw_output_path))
    write_text(template2_output_path, template2_text.rstrip() + "\n")
    write_json(decision_packet_path, decision_packet)
    return template2_text, decision_packet


def main() -> int:
    import argparse

    parser = argparse.ArgumentParser(
        description="Extract TEMPLATE2_OUTPUT and DECISION_PACKET from a manual Step 2 output."
    )
    parser.add_argument("--raw-output", required=True, help="Path to step2 raw_output.txt")
    parser.add_argument("--template2-output", required=True, help="Path to write template2_output.txt")
    parser.add_argument("--decision-packet", required=True, help="Path to write decision_packet.json")
    args = parser.parse_args()

    extract_template2_and_decision_packet(
        raw_output_path=Path(args.raw_output),
        template2_output_path=Path(args.template2_output),
        decision_packet_path=Path(args.decision_packet),
    )
    print(args.decision_packet)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
