"""Validation helpers for Step 4 order compiler text artifacts."""

from __future__ import annotations

from collections.abc import Collection, Mapping
from decimal import Decimal, InvalidOperation
from pathlib import Path
import re
from typing import Any

from investment_orchestrator.common.io import read_text


# Strategy settings keys that, together, define the approved buy universe floor.
_UNIVERSE_SETTINGS_KEYS = (
    "core_universe",
    "satellite_universe",
    "user_approved_extended_etf_static_list",
)
# Intents that submit / open / replace-submit a buy order (consume new budget and
# must be inside the allowed universe). Cancel legs are intentionally excluded.
_SUBMIT_BUY_INTENTS = {
    "NEW_ORDER",
    "BUY",
    "SUBMIT_BUY",
    "EXECUTE_BUY",
    "REPLACE_EXISTING_SUBMIT_LEG",
    "REPLACE_EXISTING",
    "REPLACE",
    "SUBMIT",
}
# Intents that open a genuinely new position (count toward max_new_tickers_per_week).
_NET_NEW_BUY_INTENTS = {"NEW_ORDER", "BUY", "SUBMIT_BUY", "EXECUTE_BUY"}
# Cancel legs: never universe/budget/new-ticker checked (e.g. removing GRID/CIBR).
_CANCEL_INTENTS = {"CANCEL_EXISTING", "REPLACE_EXISTING_CANCEL_LEG"}


REQUIRED_OUTPUT_LABELS = (
    ("template4_orders.txt", "template4_orders"),
    ("order_state_export.txt", "order_state_export"),
    ("exec_summary.txt", "exec_summary"),
)
BUY_SECTION_RE = re.compile(
    r"(?ms)^BUY_ORDERS\s*(?P<body>.*?)(?=^(?:TEMPLATE4_ORDERS_END|ORDER_STATE_EXPORT|TEMPLATE5_EXEC_SUMMARY|SELL_ORDERS)\b|\Z)"
)
BUY_ACTION_VALUES = {
    "NEW_ORDER",
    "REPLACE_EXISTING",
    "BUY",
    "REPLACE",
    "SUBMIT_BUY",
    "EXECUTE_BUY",
}
BUY_SIDE_COMPILER_ACTION_VALUES = BUY_ACTION_VALUES | {"CANCEL_EXISTING"}
ROW_INTENTS_BY_FINAL_ACTION = {
    "NEW_ORDER": {"", "NEW_ORDER", "BUY", "SUBMIT_BUY", "EXECUTE_BUY"},
    "BUY": {"", "NEW_ORDER", "BUY", "SUBMIT_BUY", "EXECUTE_BUY"},
    "SUBMIT_BUY": {"", "NEW_ORDER", "BUY", "SUBMIT_BUY", "EXECUTE_BUY"},
    "EXECUTE_BUY": {"", "NEW_ORDER", "BUY", "SUBMIT_BUY", "EXECUTE_BUY"},
    "REPLACE_EXISTING": {
        "",
        "REPLACE_EXISTING",
        "REPLACE",
        "REPLACE_EXISTING_CANCEL_LEG",
        "REPLACE_EXISTING_SUBMIT_LEG",
    },
    "REPLACE": {
        "",
        "REPLACE_EXISTING",
        "REPLACE",
        "REPLACE_EXISTING_CANCEL_LEG",
        "REPLACE_EXISTING_SUBMIT_LEG",
    },
    "CANCEL_EXISTING": {"", "CANCEL_EXISTING"},
}


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def _validate_non_empty_text_file(path: str | Path, *, label: str) -> str:
    text_path = Path(path)
    _require(text_path.exists(), f"Missing required Step 4 artifact: {text_path}")
    text = read_text(text_path)
    _require(text.strip() != "", f"Step 4 artifact is empty for {label}: {text_path}")
    return text


def _normalize_ticker(value: Any) -> str:
    return str(value or "").strip().upper()


def _normalize_action(value: Any) -> str:
    return str(value or "").strip().upper()


def _parse_pipe_row(line: str) -> dict[str, str]:
    fields: dict[str, str] = {}
    for part in line.split("|"):
        key, separator, value = part.strip().partition("=")
        if separator:
            fields[key.strip()] = value.strip()
    return fields


def _buy_order_rows(template4_orders: str) -> list[dict[str, str]]:
    match = BUY_SECTION_RE.search(template4_orders)
    if match is None:
        return []
    rows: list[dict[str, str]] = []
    for line in match.group("body").splitlines():
        fields = _parse_pipe_row(line)
        if _normalize_ticker(fields.get("ticker")):
            rows.append(fields)
    return rows


def _compile_ready_final_plans(audited_decision_packet: Any) -> dict[str, set[str]]:
    if not isinstance(audited_decision_packet, dict):
        return {}
    rows = audited_decision_packet.get("final_execution_plans")
    if not isinstance(rows, list):
        return {}
    plans: dict[str, set[str]] = {}
    for row in rows:
        if not isinstance(row, dict) or row.get("compile_ready") is not True:
            continue
        action = _normalize_action(
            row.get("final_action")
            or row.get("action")
            or row.get("order_intent")
            or ""
        )
        if action in BUY_SIDE_COMPILER_ACTION_VALUES:
            ticker = _normalize_ticker(row.get("ticker"))
            if ticker:
                plans.setdefault(ticker, set()).add(action)
    return plans


def _require_buy_order_rows_match_final_plans(
    *,
    template4_orders: str,
    audited_decision_packet: Any,
) -> None:
    buy_order_rows = _buy_order_rows(template4_orders)
    final_plans = _compile_ready_final_plans(audited_decision_packet)
    unexpected_rows: list[str] = []
    unsupported_intents: list[str] = []

    for row in buy_order_rows:
        ticker = _normalize_ticker(row.get("ticker"))
        row_intent = _normalize_action(row.get("order_intent"))
        final_actions = final_plans.get(ticker, set())
        if not final_actions:
            unexpected_rows.append(ticker)
            continue

        if any(row_intent in ROW_INTENTS_BY_FINAL_ACTION.get(action, set()) for action in final_actions):
            continue
        unsupported_intents.append(
            f"{ticker} order_intent={row_intent or '<missing>'} final_action={','.join(sorted(final_actions))}"
        )

    _require(
        not unexpected_rows,
        "BUY_ORDERS contains tickers that are not compile-ready buy-side final_execution_plans: "
        + ", ".join(sorted(set(unexpected_rows))),
    )
    _require(
        not unsupported_intents,
        "BUY_ORDERS contains order_intent values that do not match final_execution_plans: "
        + "; ".join(unsupported_intents),
    )


def _require_diagnostic_summary(exec_summary: str, audited_decision_packet: Any) -> None:
    if not isinstance(audited_decision_packet, dict):
        return
    diagnostics = audited_decision_packet.get("core_deployment_diagnostics")
    if not isinstance(diagnostics, list) or not diagnostics:
        return

    summary_lower = exec_summary.lower()
    for index, diagnostic in enumerate(diagnostics):
        _require(isinstance(diagnostic, dict), f"core_deployment_diagnostics[{index}] must be an object.")
        for key in (
            "ticker",
            "role_layer",
            "deployment_status",
            "highest_live_limit",
            "current_last_close",
            "distance_to_highest_live_limit_pct",
            "why",
        ):
            value = diagnostic.get(key)
            _require(
                value is not None and str(value).strip().lower() in summary_lower,
                f"exec_summary missing core_deployment_diagnostics {key} value for item {index}.",
            )
        if diagnostic.get("deployment_status") == "underdeployment_review_required":
            _require(
                "weekly_review_needed: core etf deployment adequacy requires weekly decision; "
                "strategy c did not change orders."
                in summary_lower,
                "exec_summary missing WEEKLY_REVIEW_NEEDED diagnostic disclaimer.",
            )


def _parse_decimal(value: Any) -> Decimal | None:
    """Parse a numeric field to Decimal; return None when malformed/missing."""
    text = str(value or "").strip()
    if text == "":
        return None
    try:
        return Decimal(text)
    except InvalidOperation:
        return None


def _row_identity(row: Mapping[str, str]) -> tuple[str, str, str, str]:
    return (
        _normalize_ticker(row.get("ticker")),
        str(row.get("plan_type", "")).strip().lower(),
        str(row.get("step_name", "")).strip().lower(),
        _normalize_action(row.get("order_intent")),
    )


def _validate_buy_row_numerics(buy_order_rows: list[dict[str, str]]) -> None:
    """Reject malformed / negative / zero-on-submit numeric fields on present rows."""
    for row in buy_order_rows:
        ticker = _normalize_ticker(row.get("ticker"))
        intent = _normalize_action(row.get("order_intent"))
        is_submit = intent in _SUBMIT_BUY_INTENTS or intent == ""

        if "limit_price" in row:
            price = _parse_decimal(row.get("limit_price"))
            _require(
                price is not None and price > 0,
                f"BUY_ORDERS row for {ticker} has invalid limit_price={row.get('limit_price')!r}.",
            )
        if "shares" in row:
            shares = _parse_decimal(row.get("shares"))
            _require(
                shares is not None and shares >= 0 and shares == shares.to_integral_value(),
                f"BUY_ORDERS row for {ticker} has invalid shares={row.get('shares')!r}.",
            )
            if is_submit:
                _require(
                    shares is not None and shares > 0,
                    f"BUY_ORDERS submit row for {ticker} has non-positive shares={row.get('shares')!r}.",
                )


def _validate_no_duplicate_buy_rows(buy_order_rows: list[dict[str, str]]) -> None:
    """Reject exact-duplicate ladder rows. Multi-step ladders / replace legs are allowed."""
    seen: set[tuple[str, str, str, str]] = set()
    duplicates: list[str] = []
    for row in buy_order_rows:
        identity = _row_identity(row)
        if identity in seen:
            duplicates.append(
                f"{identity[0]} plan_type={identity[1]} step={identity[2]} intent={identity[3]}"
            )
        seen.add(identity)
    _require(
        not duplicates,
        "BUY_ORDERS contains duplicate rows (same ticker/plan_type/step/intent): "
        + "; ".join(sorted(set(duplicates))),
    )


def _resolve_allowed_universe(
    effective_allowed_buy_universe: Collection[str] | None,
    strategy_settings: Mapping[str, Any] | None,
) -> set[str] | None:
    if effective_allowed_buy_universe is not None:
        return {
            _normalize_ticker(ticker)
            for ticker in effective_allowed_buy_universe
            if _normalize_ticker(ticker)
        }
    if isinstance(strategy_settings, Mapping):
        universe: set[str] = set()
        for key in _UNIVERSE_SETTINGS_KEYS:
            value = strategy_settings.get(key)
            if isinstance(value, list):
                universe |= {
                    _normalize_ticker(item)
                    for item in value
                    if isinstance(item, str) and item.strip()
                }
        return universe or None
    return None


def _validate_buy_universe_allowlist(
    buy_order_rows: list[dict[str, str]],
    allowed_universe: set[str],
) -> None:
    """Submit/new buy legs must target an allowed-universe ticker. Cancel legs are exempt."""
    offenders: list[str] = []
    for row in buy_order_rows:
        intent = _normalize_action(row.get("order_intent"))
        if intent in _CANCEL_INTENTS:
            continue
        if intent not in _SUBMIT_BUY_INTENTS and intent != "":
            continue
        ticker = _normalize_ticker(row.get("ticker"))
        if ticker and ticker not in allowed_universe:
            offenders.append(ticker)
    _require(
        not offenders,
        "BUY_ORDERS submit rows contain tickers outside the allowed buy universe: "
        + ", ".join(sorted(set(offenders))),
    )


def _submit_buy_notional(buy_order_rows: list[dict[str, str]]) -> Decimal:
    """Sum shares*limit_price over submit/new legs only (cancel legs excluded)."""
    total = Decimal("0")
    for row in buy_order_rows:
        intent = _normalize_action(row.get("order_intent"))
        if intent not in _SUBMIT_BUY_INTENTS and intent != "":
            continue
        shares = _parse_decimal(row.get("shares"))
        price = _parse_decimal(row.get("limit_price"))
        if shares is not None and price is not None:
            total += shares * price
    return total


def _validate_buy_budget(
    buy_order_rows: list[dict[str, str]],
    hard_cap_open_orders_budget: Any | None,
    target_new_buy_budget_this_run: Any | None,
) -> None:
    """Fail if the recomputed new-submit notional exceeds a provided budget ceiling.

    Conservative: only new/replace-submit legs contribute (cancel legs excluded);
    existing kept notional is not added here, so this is an upper-bound submit-side
    ceiling check, not a full open-order-state reconciliation (deferred to G2).
    """
    submit_notional = _submit_buy_notional(buy_order_rows)
    for label, budget in (
        ("hard_cap_open_orders_budget", hard_cap_open_orders_budget),
        ("target_new_buy_budget_this_run", target_new_buy_budget_this_run),
    ):
        if budget is None:
            continue
        ceiling = _parse_decimal(budget)
        _require(ceiling is not None, f"{label} is not a valid number: {budget!r}.")
        _require(
            submit_notional <= ceiling,
            f"BUY_ORDERS new-submit notional {submit_notional} exceeds {label} {ceiling}.",
        )


def _validate_max_new_tickers(
    buy_order_rows: list[dict[str, str]],
    max_new_tickers_per_week: int,
) -> None:
    """Fail if distinct net-new buy tickers exceed the weekly ceiling."""
    new_tickers = {
        _normalize_ticker(row.get("ticker"))
        for row in buy_order_rows
        if _normalize_action(row.get("order_intent")) in _NET_NEW_BUY_INTENTS
        and _normalize_ticker(row.get("ticker"))
    }
    _require(
        len(new_tickers) <= max_new_tickers_per_week,
        f"BUY_ORDERS opens {len(new_tickers)} new tickers ({', '.join(sorted(new_tickers))}), "
        f"exceeding max_new_tickers_per_week={max_new_tickers_per_week}.",
    )


def validate_orders_output(
    *,
    template4_orders_path: str | Path,
    order_state_export_path: str | Path,
    exec_summary_path: str | Path,
    audited_decision_packet: Any | None = None,
    strategy_settings: Mapping[str, Any] | None = None,
    effective_allowed_buy_universe: Collection[str] | None = None,
    hard_cap_open_orders_budget: Any | None = None,
    target_new_buy_budget_this_run: Any | None = None,
    max_new_tickers_per_week: int | None = None,
) -> dict[str, str]:
    """Validate the required Step 4 output text artifacts.

    Beyond existence / non-empty / compile-ready cross-checks, deterministic
    safety checks run on BUY_ORDERS rows:

    * numeric validity (malformed / negative / zero-on-submit) and exact-duplicate
      row rejection always run;
    * universe allowlist, budget ceiling, and max-new-ticker checks run when the
      corresponding context (settings / universe / budget) is supplied.

    All checks are deterministic and fail closed (raise ``ValueError``). The
    universe/budget/new-ticker checks apply only to submit/new buy legs, never to
    cancel legs.
    """
    template4_orders = _validate_non_empty_text_file(
        template4_orders_path,
        label="template4_orders",
    )
    order_state_export = _validate_non_empty_text_file(
        order_state_export_path,
        label="order_state_export",
    )
    exec_summary = _validate_non_empty_text_file(
        exec_summary_path,
        label="exec_summary",
    )

    buy_order_rows = _buy_order_rows(template4_orders)
    _validate_buy_row_numerics(buy_order_rows)
    _validate_no_duplicate_buy_rows(buy_order_rows)

    allowed_universe = _resolve_allowed_universe(
        effective_allowed_buy_universe,
        strategy_settings,
    )
    if allowed_universe is not None:
        _validate_buy_universe_allowlist(buy_order_rows, allowed_universe)

    if hard_cap_open_orders_budget is not None or target_new_buy_budget_this_run is not None:
        _validate_buy_budget(
            buy_order_rows,
            hard_cap_open_orders_budget,
            target_new_buy_budget_this_run,
        )

    if max_new_tickers_per_week is not None:
        _validate_max_new_tickers(buy_order_rows, max_new_tickers_per_week)

    if audited_decision_packet is not None:
        _require_buy_order_rows_match_final_plans(
            template4_orders=template4_orders,
            audited_decision_packet=audited_decision_packet,
        )
        _require_diagnostic_summary(exec_summary, audited_decision_packet)

    return {
        "template4_orders": template4_orders,
        "order_state_export": order_state_export,
        "exec_summary": exec_summary,
    }
