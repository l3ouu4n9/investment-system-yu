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
    )
    return {
        "daily_execution_actions_path": str(daily_execution_actions_path(resolved_date, root=root)),
        "daily_execution_check_text_path": str(daily_execution_check_text_path(resolved_date, root=root)),
        "action_count": str(len(payload.get("actions", []))),
        "as_of": resolved_date,
    }
