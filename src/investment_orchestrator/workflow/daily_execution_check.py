"""Manual Daily Execution Check workflow."""

from __future__ import annotations

from datetime import date
import json
from pathlib import Path
import re
from typing import Any

from investment_orchestrator.common.io import ensure_dir, file_exists, read_json, read_text, write_text
from investment_orchestrator.common.paths import daily_artifact_dir, repo_root, require_prompt_path
from investment_orchestrator.llm.manual_output import (
    ensure_manual_output_metadata_template,
    render_prompt,
    write_rendered_prompt,
)
from investment_orchestrator.parsers.extract_daily_execution_check import extract_daily_execution_check
from investment_orchestrator.validators.validate_daily_execution_actions import build_weekly_ticker_allowlist
from investment_orchestrator.validators.validate_audited_decision_packet import (
    validate_audited_decision_packet,
)
from investment_orchestrator.validators.strategy_settings import parse_strategy_settings_text


DAILY_STEP_DIRNAME = "daily_execution_check"
PROMPT_FILENAME = "prompt.txt"
RAW_OUTPUT_FILENAME = "raw_output.txt"
DAILY_EXECUTION_ACTIONS_FILENAME = "daily_execution_actions.json"
DAILY_EXECUTION_CHECK_TEXT_FILENAME = "daily_execution_check.txt"
MARKET_DATA_RAW_FILENAME = "market_data_raw.json"
MARKET_DATA_SNAPSHOT_FILENAME = "market_data_snapshot.json"
MARKET_DATA_GENERATION_ERROR_FILENAME = "market_data_generation_error.txt"
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def default_as_of_date() -> str:
    """Return the local date used when the CLI does not provide --date."""
    return date.today().isoformat()


def _resolve_root(root: str | Path | None = None) -> Path:
    return Path(root) if root is not None else repo_root()


def _validate_as_of_date(as_of_date: str) -> str:
    if not DATE_RE.fullmatch(as_of_date):
        raise ValueError(f"Daily Execution Check date must be YYYY-MM-DD, got {as_of_date!r}.")
    return as_of_date


def _daily_root(as_of_date: str, *, root: str | Path | None = None) -> Path:
    _validate_as_of_date(as_of_date)
    if root is None:
        return daily_artifact_dir(as_of_date)
    return _resolve_root(root) / "artifacts" / "daily" / as_of_date


def daily_execution_check_artifact_dir(
    as_of_date: str,
    *,
    root: str | Path | None = None,
) -> Path:
    """Return the daily execution check artifact directory."""
    return ensure_dir(_daily_root(as_of_date, root=root) / DAILY_STEP_DIRNAME)


def daily_execution_check_prompt_path(as_of_date: str, *, root: str | Path | None = None) -> Path:
    return daily_execution_check_artifact_dir(as_of_date, root=root) / PROMPT_FILENAME


def daily_execution_check_raw_output_path(as_of_date: str, *, root: str | Path | None = None) -> Path:
    return daily_execution_check_artifact_dir(as_of_date, root=root) / RAW_OUTPUT_FILENAME


def daily_execution_actions_path(as_of_date: str, *, root: str | Path | None = None) -> Path:
    return daily_execution_check_artifact_dir(as_of_date, root=root) / DAILY_EXECUTION_ACTIONS_FILENAME


def daily_execution_check_text_path(as_of_date: str, *, root: str | Path | None = None) -> Path:
    return daily_execution_check_artifact_dir(as_of_date, root=root) / DAILY_EXECUTION_CHECK_TEXT_FILENAME


def daily_market_data_raw_path(as_of_date: str, *, root: str | Path | None = None) -> Path:
    return _daily_root(as_of_date, root=root) / MARKET_DATA_RAW_FILENAME


def daily_market_data_snapshot_path(as_of_date: str, *, root: str | Path | None = None) -> Path:
    return _daily_root(as_of_date, root=root) / MARKET_DATA_SNAPSHOT_FILENAME


def daily_market_data_generation_error_path(as_of_date: str, *, root: str | Path | None = None) -> Path:
    return _daily_root(as_of_date, root=root) / MARKET_DATA_GENERATION_ERROR_FILENAME


def current_inputs_dir(*, root: str | Path | None = None) -> Path:
    return _resolve_root(root) / "inputs" / "current"


def weekly_audited_decision_packet_path(*, root: str | Path | None = None) -> Path:
    return _resolve_root(root) / "artifacts" / "current" / "step3_audit_engine" / "audited_decision_packet.json"


def weekly_template4_orders_path(*, root: str | Path | None = None) -> Path:
    return _resolve_root(root) / "artifacts" / "current" / "step4_order_compiler" / "template4_orders.txt"


def weekly_order_state_export_path(*, root: str | Path | None = None) -> Path:
    return _resolve_root(root) / "artifacts" / "current" / "step4_order_compiler" / "order_state_export.txt"


def _require_non_empty_text(path: Path, *, label: str) -> str:
    try:
        text = read_text(path)
    except FileNotFoundError as exc:
        raise FileNotFoundError(f"Missing required {label}: {path}") from exc
    if not text.strip():
        raise ValueError(f"Required {label} is empty: {path}")
    return text


def _require_json_object(path: Path, *, label: str) -> dict[str, Any]:
    try:
        payload = read_json(path)
    except FileNotFoundError as exc:
        raise FileNotFoundError(f"Missing required {label}: {path}") from exc
    except json.JSONDecodeError as exc:
        raise ValueError(f"Required {label} is not valid JSON: {path}: {exc}") from exc
    if not isinstance(payload, dict):
        raise ValueError(f"Required {label} must be a JSON object: {path}")
    return payload


def load_weekly_audited_decision_packet(*, root: str | Path | None = None) -> dict[str, Any]:
    """Read, validate, and require the weekly audited packet to be compiler-ready."""
    payload = validate_audited_decision_packet(
        _require_json_object(
            weekly_audited_decision_packet_path(root=root),
            label="weekly audited_decision_packet.json artifact",
        )
    )
    if payload.get("audit_passed") is not True:
        raise ValueError("Daily Execution Check blocked: weekly audited packet has audit_passed != true.")
    if payload.get("order_compiler_ready") is not True:
        raise ValueError("Daily Execution Check blocked: weekly audited packet has order_compiler_ready != true.")
    return payload


def _optional_text(candidates: list[Path], *, empty_text: str) -> str:
    for path in candidates:
        if path.exists() and path.is_file():
            text = read_text(path)
            return text.rstrip() + "\n" if text.strip() else empty_text
    return empty_text


def load_optional_daily_market_data_snapshot(
    as_of_date: str,
    *,
    root: str | Path | None = None,
) -> str:
    """Load optional daily market data snapshot text or a non-failing unavailable marker."""
    base = _daily_root(as_of_date, root=root)
    current_inputs = current_inputs_dir(root=root)
    return _optional_text(
        [
            base / "market_data_snapshot.json",
            base / "daily_market_data_snapshot.json",
            base / DAILY_STEP_DIRNAME / "daily_market_data_snapshot.json",
            current_inputs / "daily_market_data_snapshot.json",
        ],
        empty_text="DATA_UNAVAILABLE: no daily market data snapshot found for this date.\n",
    )


def _optional_json_object(candidates: list[Path]) -> dict[str, Any] | None:
    for path in candidates:
        if not path.exists() or not path.is_file():
            continue
        try:
            payload = read_json(path)
        except (json.JSONDecodeError, OSError):
            return None
        return payload if isinstance(payload, dict) else None
    return None


def load_optional_daily_market_data_snapshot_json(
    as_of_date: str,
    *,
    root: str | Path | None = None,
) -> dict[str, Any] | None:
    """Load optional daily market data snapshot JSON for deterministic diagnostics."""
    base = _daily_root(as_of_date, root=root)
    current_inputs = current_inputs_dir(root=root)
    return _optional_json_object(
        [
            base / "market_data_snapshot.json",
            base / "daily_market_data_snapshot.json",
            base / DAILY_STEP_DIRNAME / "daily_market_data_snapshot.json",
            current_inputs / "daily_market_data_snapshot.json",
        ]
    )


def _float_or_none(value: Any) -> float | None:
    if value is None:
        return None
    try:
        text = str(value).strip().replace(",", "")
        if not text or text.upper() in {"NA", "N/A", "NONE", "NULL"}:
            return None
        return float(text)
    except ValueError:
        return None


def _round_pct(value: float | None) -> float | None:
    return round(value, 2) if value is not None else None


def _daily_price_rows(snapshot: dict[str, Any] | None) -> dict[str, dict[str, Any]]:
    if not isinstance(snapshot, dict):
        return {}
    rows = snapshot.get("tickers")
    if not isinstance(rows, list):
        return {}
    output: dict[str, dict[str, Any]] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        ticker = str(row.get("ticker", "")).strip().upper()
        if ticker:
            output[ticker] = row
    return output


def _parse_existing_buy_open_orders_summary(portfolio_snapshot: str) -> list[dict[str, Any]]:
    columns = [
        "ticker",
        "budget",
        "compiled_open_order_notional",
        "residual_cash_not_allocated",
        "template_id",
        "anchor_baseline_last_close",
        "anchor_price_asof",
        "last_refresh_date_et",
        "highest_live_limit",
        "lowest_live_limit",
        "live_step_count",
        "live_order_steps_summary",
        "live_order_qtys_summary",
    ]
    rows: list[dict[str, Any]] = []
    in_section = False
    for raw_line in portfolio_snapshot.splitlines():
        line = raw_line.strip()
        if "(2a) existing_buy_open_orders_summary" in line:
            in_section = True
            continue
        if in_section and (line.startswith("(2b)") or line.startswith("sell_open_orders")):
            break
        if not in_section or "|" not in line:
            continue
        parts = [part.strip() for part in line.split("|")]
        if not parts or parts[0].upper() in {"TICKER", "NONE"}:
            continue
        if not re.fullmatch(r"[A-Za-z][A-Za-z0-9.\-]{0,9}", parts[0]):
            continue
        padded = parts[: len(columns)] + [""] * max(0, len(columns) - len(parts))
        row = dict(zip(columns, padded, strict=False))
        row["ticker"] = str(row["ticker"]).upper()
        rows.append(row)
    return rows


def _role_for_ticker(
    ticker: str,
    *,
    row: dict[str, Any],
    audited_packet: dict[str, Any],
    settings: dict[str, Any],
) -> str | None:
    for section_name in ("final_execution_plans", "final_buy_side_delta_table"):
        section = audited_packet.get(section_name)
        if not isinstance(section, list):
            continue
        for item in section:
            if not isinstance(item, dict):
                continue
            if str(item.get("ticker", "")).strip().upper() != ticker:
                continue
            for key in ("role_layer", "role_used", "role", "role_bucket", "template_role"):
                value = item.get(key)
                if isinstance(value, str) and value.strip():
                    return value.strip()
    drift_policy = settings.get("daily_execution_drift_policy")
    if isinstance(drift_policy, dict):
        fallback = drift_policy.get("ticker_role_fallback")
        if isinstance(fallback, dict):
            value = fallback.get(ticker)
            if isinstance(value, str) and value.strip():
                return value.strip()
    template_id = str(row.get("template_id") or "").strip()
    template_map = settings.get("buy_order_template_map")
    if isinstance(template_map, dict) and template_id:
        for role, config in template_map.items():
            if isinstance(config, dict) and str(config.get("template_id") or "").strip() == template_id:
                return str(role)
    return None


def _role_number(settings: dict[str, Any], path: tuple[str, ...], role: str | None) -> float | None:
    value: Any = settings
    for key in path:
        if not isinstance(value, dict):
            return None
        value = value.get(key)
    if not isinstance(value, dict) or role is None:
        return None
    return _float_or_none(value.get(role))


def build_daily_execution_precomputed_diagnostics(
    *,
    portfolio_snapshot: str,
    daily_market_data_snapshot: dict[str, Any] | None,
    audited_packet: dict[str, Any],
    settings: dict[str, Any],
) -> dict[str, Any]:
    """Precompute Daily Execution Check diagnostics for model aid and parser guardrails."""
    price_rows = _daily_price_rows(daily_market_data_snapshot)
    diagnostics: list[dict[str, Any]] = []

    for row in _parse_existing_buy_open_orders_summary(portfolio_snapshot):
        ticker = str(row.get("ticker", "")).upper()
        price_row = price_rows.get(ticker, {})
        daily_last_close = _float_or_none(price_row.get("last_close"))
        anchor_baseline = _float_or_none(row.get("anchor_baseline_last_close"))
        highest_live_limit = _float_or_none(row.get("highest_live_limit"))
        lowest_live_limit = _float_or_none(row.get("lowest_live_limit"))
        role = _role_for_ticker(ticker, row=row, audited_packet=audited_packet, settings=settings)
        atr_pct = _float_or_none(price_row.get("atr_20_30d_pct"))

        anchor_static_cap = _role_number(
            settings,
            ("daily_execution_drift_policy", "keep_tolerance", "anchor_drift_abs_pct_static_cap"),
            role,
        )
        anchor_atr_multiple = _role_number(
            settings,
            ("daily_execution_drift_policy", "keep_tolerance", "anchor_drift_atr_multiple_cap"),
            role,
        )
        anchor_atr_cap = anchor_atr_multiple * atr_pct if anchor_atr_multiple is not None and atr_pct is not None else None
        anchor_tolerance_candidates = [item for item in (anchor_static_cap, anchor_atr_cap) if item is not None]
        anchor_drift_tolerance = max(anchor_tolerance_candidates) if anchor_tolerance_candidates else None
        highest_cap = _role_number(
            settings,
            (
                "daily_execution_drift_policy",
                "keep_tolerance",
                "max_negative_distance_to_highest_live_limit_pct",
            ),
            role,
        )
        anchor_band = _role_number(
            settings,
            ("daily_execution_drift_policy", "near_edge_monitor_band", "anchor_drift_pct_band"),
            role,
        )
        highest_band = _role_number(
            settings,
            ("daily_execution_drift_policy", "near_edge_monitor_band", "highest_live_limit_distance_pct_band"),
            role,
        )

        anchor_drift = None
        if daily_last_close is not None and anchor_baseline not in (None, 0):
            anchor_drift = (daily_last_close - anchor_baseline) / anchor_baseline * 100
        distance_highest = None
        if daily_last_close not in (None, 0) and highest_live_limit is not None:
            distance_highest = (highest_live_limit - daily_last_close) / daily_last_close * 100
        distance_lowest = None
        if daily_last_close not in (None, 0) and lowest_live_limit is not None:
            distance_lowest = (lowest_live_limit - daily_last_close) / daily_last_close * 100

        remaining_anchor = (
            anchor_drift_tolerance - abs(anchor_drift)
            if anchor_drift_tolerance is not None and anchor_drift is not None
            else None
        )
        remaining_highest = (
            distance_highest - (-1 * highest_cap)
            if distance_highest is not None and highest_cap is not None
            else None
        )
        near_anchor = (
            remaining_anchor is not None and anchor_band is not None and remaining_anchor <= anchor_band
        )
        near_highest = (
            remaining_highest is not None and highest_band is not None and remaining_highest <= highest_band
        )
        data_gap_fields = [
            name
            for name, value in (
                ("daily_last_close", daily_last_close),
                ("daily_price_asof", price_row.get("price_asof")),
                ("anchor_baseline_last_close", anchor_baseline),
                ("anchor_price_asof", row.get("anchor_price_asof")),
                ("role_used", role),
                ("anchor_drift_tolerance", anchor_drift_tolerance),
                ("max_negative_distance_to_highest_live_limit_pct", highest_cap),
            )
            if value is None or value == ""
        ]

        diagnostics.append(
            {
                "ticker": ticker,
                "daily_last_close": daily_last_close,
                "daily_price_asof": price_row.get("price_asof"),
                "anchor_baseline_last_close": anchor_baseline,
                "anchor_price_asof": row.get("anchor_price_asof") or None,
                "anchor_drift_pct": _round_pct(anchor_drift),
                "distance_to_highest_live_limit_pct": _round_pct(distance_highest),
                "distance_to_lowest_live_limit_pct": _round_pct(distance_lowest),
                "role_used": role,
                "anchor_drift_tolerance": _round_pct(anchor_drift_tolerance),
                "max_negative_distance_to_highest_live_limit_pct": _round_pct(highest_cap),
                "remaining_anchor_margin_pct": _round_pct(remaining_anchor),
                "remaining_highest_distance_margin_pct": _round_pct(remaining_highest),
                "near_anchor_edge": near_anchor,
                "near_highest_live_limit_edge": near_highest,
                "data_gap": bool(data_gap_fields),
                "data_gap_fields": data_gap_fields,
            }
        )

    return {
        "purpose": "model aid and parser guardrail only; not a replacement for final DAILY_EXECUTION_ACTIONS",
        "diagnostics": diagnostics,
    }


def generate_daily_market_data_snapshot(
    as_of_date: str,
    *,
    root: str | Path | None = None,
) -> dict[str, str]:
    """Generate daily market data files for the weekly-approved ticker allowlist when possible."""
    as_of_date = _validate_as_of_date(as_of_date)
    snapshot_path = daily_market_data_snapshot_path(as_of_date, root=root)
    raw_path = daily_market_data_raw_path(as_of_date, root=root)
    error_path = daily_market_data_generation_error_path(as_of_date, root=root)

    try:
        audited_packet = load_weekly_audited_decision_packet(root=root)
        template4_orders = _require_non_empty_text(
            weekly_template4_orders_path(root=root),
            label="weekly template4_orders.txt artifact",
        )
        order_state_export = _require_non_empty_text(
            weekly_order_state_export_path(root=root),
            label="weekly order_state_export.txt artifact",
        )
        tickers = sorted(
            build_weekly_ticker_allowlist(
                audited_decision_packet=audited_packet,
                template4_orders_text=template4_orders,
                order_state_export_text=order_state_export,
            )
        )
        if not tickers:
            raise ValueError("Unable to build weekly-approved ticker allowlist for market data generation.")

        from investment_orchestrator.market.build_market_data_snapshot import build_market_data_snapshot
        from investment_orchestrator.market.generate_market_data_raw import generate_market_data_raw

        run_timestamp_et = f"{as_of_date} 20:00 ET"
        ensure_dir(raw_path.parent)
        generate_market_data_raw(
            output_path=raw_path,
            tickers=tickers,
            run_timestamp_et=run_timestamp_et,
            pretty=True,
        )
        build_market_data_snapshot(
            raw_input_path=raw_path,
            output_path=snapshot_path,
            run_timestamp_et=run_timestamp_et,
            pretty=True,
        )
        if error_path.exists():
            error_path.unlink()
        return {
            "status": "generated",
            "market_data_raw_path": str(raw_path),
            "market_data_snapshot_path": str(snapshot_path),
            "tickers": ",".join(tickers),
        }
    except SystemExit as exc:
        message = str(exc)
    except Exception as exc:  # noqa: BLE001
        message = str(exc)

    write_text(error_path, message.rstrip() + "\n")
    return {
        "status": "unavailable",
        "market_data_raw_path": str(raw_path),
        "market_data_snapshot_path": str(snapshot_path),
        "market_data_generation_error_path": str(error_path),
        "error": message,
    }


def load_optional_override_event_notes(
    as_of_date: str,
    *,
    root: str | Path | None = None,
) -> str:
    """Load optional override event notes text or EMPTY."""
    base = _daily_root(as_of_date, root=root)
    current_inputs = current_inputs_dir(root=root)
    return _optional_text(
        [
            base / "override_event_notes.output.txt",
            base / "override_event_notes.txt",
            base / DAILY_STEP_DIRNAME / "override_event_notes.txt",
            current_inputs / "override_event_notes.txt",
        ],
        empty_text="EMPTY\n",
    )


def build_daily_execution_check_prompt_text(
    as_of_date: str,
    *,
    root: str | Path | None = None,
    generate_market_data: bool = False,
) -> str:
    """Render the Daily Execution Check prompt from weekly artifacts plus current inputs."""
    as_of_date = _validate_as_of_date(as_of_date)
    if generate_market_data:
        generate_daily_market_data_snapshot(as_of_date, root=root)

    prompt_template = read_text(require_prompt_path("daily_execution_check.txt"))
    audited_packet_path = weekly_audited_decision_packet_path(root=root)
    template4_path = weekly_template4_orders_path(root=root)
    order_state_path = weekly_order_state_export_path(root=root)

    audited_packet = load_weekly_audited_decision_packet(root=root)
    template4_orders = _require_non_empty_text(template4_path, label="weekly template4_orders.txt artifact")
    order_state_export = _require_non_empty_text(order_state_path, label="weekly order_state_export.txt artifact")
    portfolio_snapshot = _require_non_empty_text(
        current_inputs_dir(root=root) / "portfolio_snapshot.txt",
        label="portfolio snapshot input",
    )
    strategy_settings = _require_non_empty_text(
        current_inputs_dir(root=root) / "strategy_settings.yaml",
        label="strategy settings YAML input",
    )
    strategy_settings_payload = parse_strategy_settings_text(strategy_settings)
    daily_market_data_snapshot_payload = load_optional_daily_market_data_snapshot_json(as_of_date, root=root)
    precomputed_diagnostics = build_daily_execution_precomputed_diagnostics(
        portfolio_snapshot=portfolio_snapshot,
        daily_market_data_snapshot=daily_market_data_snapshot_payload,
        audited_packet=audited_packet,
        settings=strategy_settings_payload,
    )

    rendered = render_prompt(
        prompt_template,
        {
            "as_of": as_of_date,
            "audited_decision_packet_path": str(audited_packet_path),
            "audited_decision_packet": json.dumps(audited_packet, ensure_ascii=False, indent=2),
            "template4_orders_path": str(template4_path),
            "template4_orders": template4_orders,
            "order_state_export_path": str(order_state_path),
            "order_state_export": order_state_export,
            "portfolio_snapshot": portfolio_snapshot,
            "strategy_settings": strategy_settings,
            "daily_market_data_snapshot": load_optional_daily_market_data_snapshot(as_of_date, root=root),
            "daily_execution_precomputed_diagnostics": json.dumps(
                precomputed_diagnostics,
                ensure_ascii=False,
                indent=2,
            ),
            "override_event_notes": load_optional_override_event_notes(as_of_date, root=root),
        },
    )
    return rendered.rstrip() + "\n"


def render_daily_execution_check_prompt(
    *,
    as_of_date: str | None = None,
    root: str | Path | None = None,
    generate_market_data: bool = False,
) -> dict[str, str]:
    """Write the rendered Daily Execution Check prompt and prepare raw_output.txt."""
    resolved_date = _validate_as_of_date(as_of_date or default_as_of_date())
    artifact_dir = daily_execution_check_artifact_dir(resolved_date, root=root)
    prompt_output_path = daily_execution_check_prompt_path(resolved_date, root=root)
    raw_output_path = daily_execution_check_raw_output_path(resolved_date, root=root)

    write_rendered_prompt(
        prompt_output_path,
        build_daily_execution_check_prompt_text(
            resolved_date,
            root=root,
            generate_market_data=generate_market_data,
        ),
    )
    if not file_exists(raw_output_path):
        write_text(raw_output_path, "")
    metadata_path = ensure_manual_output_metadata_template(
        raw_output_path,
        prompt_path=prompt_output_path,
    )

    return {
        "artifact_dir": str(artifact_dir),
        "prompt_path": str(prompt_output_path),
        "raw_output_path": str(raw_output_path),
        "raw_output_metadata_path": str(metadata_path),
        "as_of": resolved_date,
        "market_data_snapshot_path": str(daily_market_data_snapshot_path(resolved_date, root=root)),
        "market_data_generation_error_path": str(
            daily_market_data_generation_error_path(resolved_date, root=root)
        ),
    }


def parse_daily_execution_check_output(
    *,
    as_of_date: str | None = None,
    root: str | Path | None = None,
) -> dict[str, str]:
    """Parse and validate the Daily Execution Check raw output."""
    resolved_date = _validate_as_of_date(as_of_date or default_as_of_date())
    payload = extract_daily_execution_check(
        raw_output_path=daily_execution_check_raw_output_path(resolved_date, root=root),
        daily_execution_actions_path=daily_execution_actions_path(resolved_date, root=root),
        daily_execution_check_text_path=daily_execution_check_text_path(resolved_date, root=root),
        audited_decision_packet_path=weekly_audited_decision_packet_path(root=root),
        template4_orders_path=weekly_template4_orders_path(root=root),
        order_state_export_path=weekly_order_state_export_path(root=root),
        strategy_settings_path=current_inputs_dir(root=root) / "strategy_settings.yaml",
        precomputed_diagnostics=build_daily_execution_precomputed_diagnostics(
            portfolio_snapshot=_require_non_empty_text(
                current_inputs_dir(root=root) / "portfolio_snapshot.txt",
                label="portfolio snapshot input",
            ),
            daily_market_data_snapshot=load_optional_daily_market_data_snapshot_json(resolved_date, root=root),
            audited_packet=load_weekly_audited_decision_packet(root=root),
            settings=parse_strategy_settings_text(
                _require_non_empty_text(
                    current_inputs_dir(root=root) / "strategy_settings.yaml",
                    label="strategy settings YAML input",
                )
            ),
        ),
    )
    return {
        "daily_execution_actions_path": str(daily_execution_actions_path(resolved_date, root=root)),
        "daily_execution_check_text_path": str(daily_execution_check_text_path(resolved_date, root=root)),
        "action_count": str(len(payload.get("actions", []))),
        "as_of": resolved_date,
    }
