"""Validation helpers for Step 4 order compiler text artifacts."""

from __future__ import annotations

from collections.abc import Collection, Mapping
from decimal import Decimal, InvalidOperation
from pathlib import Path
import re
from typing import Any

from investment_orchestrator.common.io import read_text
from investment_orchestrator.parsers.portfolio_snapshot_existing_orders import (
    ExistingBuyOpenOrdersParseResult,
)


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

# final_execution_plans.final_action values whose compiled notional must match the
# actual BUY submit rows (i.e. the plan submits new orders). KEEP_EXISTING and
# CANCEL_EXISTING are excluded (no new submit rows to cross-check).
_SUBMIT_CROSS_CHECK_ACTIONS = {
    "NEW_ORDER",
    "REPLACE_EXISTING",
    "BUY",
    "REPLACE",
    "SUBMIT_BUY",
    "EXECUTE_BUY",
    "SUBMIT",
}
# Cents-level tolerance for the per-ticker submit cross-check (whole-share ladders
# at 2dp limit prices reconcile exactly; this absorbs float repr only).
_BUDGET_TOLERANCE = Decimal("0.01")


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
    """Parse a numeric field to Decimal; return None when malformed/missing.

    Note: a numeric/string zero must parse to Decimal(0) (not None) so a
    legitimate 0.0 notional (e.g. a CANCEL plan) is not treated as missing.
    """
    if value is None:
        return None
    text = str(value).strip()
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


def _validate_no_conflicting_buy_actions(buy_order_rows: list[dict[str, str]]) -> None:
    """Reject contradictory buy-side rows for the same ticker / ladder slot.

    Two narrow, format-safe conflict classes are flagged (additive to the
    exact-duplicate check, which catches identical rows):

    * **Action conflict:** the same ticker carries both a net-new buy leg
      (``NEW_ORDER`` / ``BUY`` / ``SUBMIT_BUY`` / ``EXECUTE_BUY``) and a
      *standalone* ``CANCEL_EXISTING`` leg. A coordinated replace uses the
      ``REPLACE_EXISTING_*_LEG`` intents (not plain ``CANCEL_EXISTING``) and is
      intentionally exempt.
    * **Slot intent conflict:** the same ``(ticker, plan_type, step_name)``
      ladder slot appears with two or more distinct non-empty ``order_intent``
      values. Multi-step ladders (distinct ``step_name``) and replace
      cancel/submit legs (distinct ``plan_type``) occupy different slots and are
      not affected.
    """
    net_new_by_ticker: dict[str, bool] = {}
    plain_cancel_by_ticker: dict[str, bool] = {}
    slot_intents: dict[tuple[str, str, str], set[str]] = {}

    for row in buy_order_rows:
        ticker = _normalize_ticker(row.get("ticker"))
        if not ticker:
            continue
        intent = _normalize_action(row.get("order_intent"))
        if intent in _NET_NEW_BUY_INTENTS:
            net_new_by_ticker[ticker] = True
        if intent == "CANCEL_EXISTING":
            plain_cancel_by_ticker[ticker] = True

        slot = (
            ticker,
            str(row.get("plan_type", "")).strip().lower(),
            str(row.get("step_name", "")).strip().lower(),
        )
        if intent:
            slot_intents.setdefault(slot, set()).add(intent)

    action_conflicts = sorted(
        ticker
        for ticker in net_new_by_ticker
        if plain_cancel_by_ticker.get(ticker)
    )
    _require(
        not action_conflicts,
        "BUY_ORDERS contains conflicting actions for the same ticker "
        "(net-new buy and standalone cancel): " + ", ".join(action_conflicts),
    )

    slot_conflicts = sorted(
        f"{slot[0]} plan_type={slot[1] or '<none>'} step={slot[2] or '<none>'} "
        f"intents={','.join(sorted(intents))}"
        for slot, intents in slot_intents.items()
        if len(intents) > 1
    )
    _require(
        not slot_conflicts,
        "BUY_ORDERS has conflicting order_intent values for the same ladder slot: "
        + "; ".join(slot_conflicts),
    )


def _enforce_safety_context_present(
    buy_order_rows: list[dict[str, str]],
    *,
    allowed_universe: set[str] | None,
    hard_cap_open_orders_budget: Any | None,
    target_new_buy_budget_this_run: Any | None,
    max_new_tickers_per_week: int | None,
    strategy_settings: Mapping[str, Any] | None,
) -> None:
    """Fail closed when BUY submit rows are present but safety context is missing.

    Opt-in (``require_safety_context``). Standalone callers that do not opt in
    keep the prior lenient behavior (checks skipped when their context is
    absent); the primary ``run_step4 parse`` path opts in so that a settings file
    missing a budget / universe / new-ticker ceiling fails closed rather than
    silently skipping the corresponding check while real BUY rows exist. Pure
    cancel-only output (no submit/new legs) requires no budget/universe context.

    Net-new BUY rows (``_NET_NEW_BUY_INTENTS``) additionally require both a
    ``max_new_tickers_per_week`` ceiling and a per-run ``target_new_buy_budget_this_run``
    (G5). Replacement-/cancel-/keep-only runs have no net-new rows, so neither is
    required of them — they remain governed by ``hard_cap_open_orders_budget``.
    """
    submit_rows = [
        row
        for row in buy_order_rows
        if _normalize_action(row.get("order_intent")) in _SUBMIT_BUY_INTENTS
        or _normalize_action(row.get("order_intent")) == ""
    ]
    if submit_rows:
        _require(
            allowed_universe is not None,
            "BUY_ORDERS has submit rows but the allowed buy universe could not be "
            "resolved (require_safety_context): supply effective_allowed_buy_universe "
            "or strategy_settings universe lists.",
        )
        _require(
            hard_cap_open_orders_budget is not None,
            "BUY_ORDERS has submit rows but hard_cap_open_orders_budget was not "
            "supplied (require_safety_context).",
        )

    net_new_rows = any(
        _normalize_action(row.get("order_intent")) in _NET_NEW_BUY_INTENTS
        for row in buy_order_rows
    )
    if net_new_rows:
        has_per_bucket = isinstance(strategy_settings, Mapping) and isinstance(
            strategy_settings.get("max_new_tickers_per_week"), Mapping
        )
        _require(
            max_new_tickers_per_week is not None or has_per_bucket,
            "BUY_ORDERS opens net-new tickers but no max_new_tickers_per_week ceiling "
            "was supplied (require_safety_context).",
        )
        _require(
            target_new_buy_budget_this_run is not None,
            "BUY_ORDERS opens net-new buy rows but target_new_buy_budget_this_run was "
            "not supplied (require_safety_context).",
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


def _net_new_buy_notional(buy_order_rows: list[dict[str, str]]) -> Decimal:
    """Sum shares*limit_price over net-new buy legs only (``_NET_NEW_BUY_INTENTS``).

    Net-new legs are ``NEW_ORDER`` / ``BUY`` / ``SUBMIT_BUY`` / ``EXECUTE_BUY``.
    ``REPLACE_EXISTING_*`` (budget-neutral reanchors of already-budgeted exposure),
    ``CANCEL_EXISTING``, KEEP rows, and blank-intent rows are excluded. This is the
    notional measured against ``target_new_buy_budget_this_run``: a replacement
    recycles existing open-order budget at a new anchor and must not consume the
    per-run new-buy budget — it stays bound by ``hard_cap_open_orders_budget``.
    """
    total = Decimal("0")
    for row in buy_order_rows:
        if _normalize_action(row.get("order_intent")) not in _NET_NEW_BUY_INTENTS:
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
    """Fail if a recomputed buy notional exceeds its provided budget ceiling.

    Two distinct notionals are measured against two distinct ceilings:

    * ``hard_cap_open_orders_budget`` is checked against the broader **submit-side**
      notional (new + replace-submit legs; cancel legs excluded). This upper-bound
      submit-side floor is **unchanged** and is **not weakened** by G5 — the full
      total-exposure reconciliation (incl. KEEP existing notional) is the separate
      G3 check.
    * ``target_new_buy_budget_this_run`` is checked against the **net-new-only**
      notional (``_NET_NEW_BUY_INTENTS``; replace / cancel / keep / blank excluded),
      so replacements and cancels never consume the per-run new-buy budget while
      remaining subject to the hard cap.
    """
    submit_notional = _submit_buy_notional(buy_order_rows)
    net_new_notional = _net_new_buy_notional(buy_order_rows)
    for label, budget, notional, descriptor in (
        ("hard_cap_open_orders_budget", hard_cap_open_orders_budget, submit_notional, "new-submit"),
        (
            "target_new_buy_budget_this_run",
            target_new_buy_budget_this_run,
            net_new_notional,
            "net-new",
        ),
    ):
        if budget is None:
            continue
        ceiling = _parse_decimal(budget)
        _require(ceiling is not None, f"{label} is not a valid number: {budget!r}.")
        _require(
            notional <= ceiling,
            f"BUY_ORDERS {descriptor} notional {notional} exceeds {label} {ceiling}.",
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


def _new_buy_tickers(buy_order_rows: list[dict[str, str]]) -> set[str]:
    """Distinct net-new buy tickers (ladder rows for a ticker count once)."""
    return {
        _normalize_ticker(row.get("ticker"))
        for row in buy_order_rows
        if _normalize_action(row.get("order_intent")) in _NET_NEW_BUY_INTENTS
        and _normalize_ticker(row.get("ticker"))
    }


def _require_int_subkey(container: Mapping[str, Any], key: str) -> int:
    value = container.get(key)
    _require(
        isinstance(value, int) and not isinstance(value, bool),
        f"max_new_tickers_per_week.{key} must be an integer; got {value!r}.",
    )
    return value


def _validate_per_bucket_new_tickers(
    buy_order_rows: list[dict[str, str]],
    strategy_settings: Mapping[str, Any],
) -> None:
    """Enforce base vs extended new-ticker ceilings separately (not just the sum).

    Classification source is the operator-maintained strategy settings universe
    lists (the authoritative SSOT): base = core_universe ∪ satellite_universe,
    extended = user_approved_extended_etf_static_list. A net-new ticker present
    in both lists is conservatively counted as base (a settings inconsistency); a
    net-new ticker in neither list fails closed. Fails closed when the
    max_new_tickers_per_week settings are malformed.
    """
    limits = strategy_settings.get("max_new_tickers_per_week")
    if not isinstance(limits, Mapping):
        _require(
            limits is None,
            f"max_new_tickers_per_week must be a mapping of base/extended limits; got {limits!r}.",
        )
        return  # absent -> per-bucket not applicable (aggregate check still runs)

    base_limit = _require_int_subkey(limits, "base_universe_new_tickers_per_week")
    extended_limit = _require_int_subkey(limits, "extended_etf_sleeve_new_tickers_per_week")

    base_universe = _string_list_set(strategy_settings.get("core_universe")) | _string_list_set(
        strategy_settings.get("satellite_universe")
    )
    extended_universe = _string_list_set(
        strategy_settings.get("user_approved_extended_etf_static_list")
    )

    new_base: set[str] = set()
    new_extended: set[str] = set()
    unclassifiable: set[str] = set()
    for ticker in _new_buy_tickers(buy_order_rows):
        if ticker in base_universe:  # in-both -> base (conservative)
            new_base.add(ticker)
        elif ticker in extended_universe:
            new_extended.add(ticker)
        else:
            unclassifiable.add(ticker)

    _require(
        not unclassifiable,
        "BUY_ORDERS opens net-new tickers not classifiable as base or extended "
        f"(absent from both strategy_settings universe lists): {', '.join(sorted(unclassifiable))}.",
    )
    _require(
        len(new_base) <= base_limit,
        f"BUY_ORDERS opens {len(new_base)} new base ticker(s) ({', '.join(sorted(new_base))}), "
        f"exceeding base_universe_new_tickers_per_week={base_limit}.",
    )
    _require(
        len(new_extended) <= extended_limit,
        f"BUY_ORDERS opens {len(new_extended)} new extended ETF ticker(s) "
        f"({', '.join(sorted(new_extended))}), "
        f"exceeding extended_etf_sleeve_new_tickers_per_week={extended_limit}.",
    )


def _string_list_set(value: Any) -> set[str]:
    if not isinstance(value, list):
        return set()
    return {_normalize_ticker(item) for item in value if isinstance(item, str) and item.strip()}


def _compile_ready_plans(audited_decision_packet: Any) -> list[Mapping[str, Any]]:
    if not isinstance(audited_decision_packet, Mapping):
        return []
    rows = audited_decision_packet.get("final_execution_plans")
    if not isinstance(rows, list):
        return []
    return [row for row in rows if isinstance(row, Mapping) and row.get("compile_ready") is True]


def _require_non_negative_decimal_field(
    plan: Mapping[str, Any],
    key: str,
    ticker: str,
) -> Decimal:
    raw = plan.get(key)
    value = _parse_decimal(raw)
    _require(
        value is not None,
        f"final_execution_plans[{ticker}].{key} is missing or non-numeric: {raw!r}.",
    )
    _require(value >= 0, f"final_execution_plans[{ticker}].{key} must be non-negative: {value}.")
    return value


def _submit_notional_by_ticker(buy_order_rows: list[dict[str, str]]) -> dict[str, Decimal]:
    """Sum shares*limit_price over submit/new legs, grouped by ticker (cancel legs excluded)."""
    totals: dict[str, Decimal] = {}
    for row in buy_order_rows:
        intent = _normalize_action(row.get("order_intent"))
        if intent not in _SUBMIT_BUY_INTENTS and intent != "":
            continue
        ticker = _normalize_ticker(row.get("ticker"))
        if not ticker:
            continue
        shares = _parse_decimal(row.get("shares"))
        price = _parse_decimal(row.get("limit_price"))
        if shares is not None and price is not None:
            totals[ticker] = totals.get(ticker, Decimal("0")) + shares * price
    return totals


def _validate_total_open_order_exposure(
    *,
    buy_order_rows: list[dict[str, str]],
    audited_decision_packet: Any,
    hard_cap_open_orders_budget: Any,
) -> None:
    """Reconcile TOTAL intended open-order exposure against the hard cap (PR G3).

    Totals are recomputed from ``audited_decision_packet.final_execution_plans``
    (the structured, authoritative source) — never from the exec_summary
    aggregate PASS flags, which are LLM/compiler-restated diagnostics. This
    captures KEEP_EXISTING existing notional that the submit-side G1 floor omits.
    For NEW_ORDER / REPLACE_EXISTING plans, the actual BUY submit rows are
    cross-checked against the plan's compiled notional. Fails closed.
    """
    plans = _compile_ready_plans(audited_decision_packet)
    if not plans:
        # NO_TRADE / no compile-ready plans -> nothing to reconcile (totals 0).
        return

    cap = _parse_decimal(hard_cap_open_orders_budget)
    _require(
        cap is not None,
        f"hard_cap_open_orders_budget is not a valid number: {hard_cap_open_orders_budget!r}.",
    )

    submit_by_ticker = _submit_notional_by_ticker(buy_order_rows)
    total_compiled = Decimal("0")
    total_target = Decimal("0")

    for plan in plans:
        ticker = _normalize_ticker(plan.get("ticker")) or "<missing>"
        compiled = _require_non_negative_decimal_field(plan, "compiled_open_order_notional", ticker)
        target = _require_non_negative_decimal_field(plan, "target_open_order_budget", ticker)
        total_compiled += compiled
        total_target += target

        action = _normalize_action(
            plan.get("final_action") or plan.get("action") or plan.get("order_intent")
        )
        if action in _SUBMIT_CROSS_CHECK_ACTIONS:
            submit_sum = submit_by_ticker.get(ticker, Decimal("0"))
            _require(
                abs(submit_sum - compiled) <= _BUDGET_TOLERANCE,
                f"BUY_ORDERS submit notional for {ticker} ({submit_sum}) does not match "
                f"final_execution_plans compiled_open_order_notional ({compiled}).",
            )

    _require(
        total_target <= cap,
        f"total target open-order budget {total_target} exceeds "
        f"hard_cap_open_orders_budget {cap}.",
    )
    _require(
        total_compiled <= total_target,
        f"total compiled open-order notional {total_compiled} exceeds "
        f"total target open-order budget {total_target}.",
    )


def _validate_keep_existing_against_snapshot(
    *,
    audited_decision_packet: Any,
    existing_buy_open_orders: ExistingBuyOpenOrdersParseResult,
) -> None:
    """Independently verify KEEP_EXISTING open-order notional vs portfolio snapshot (PR G4).

    KEEP_EXISTING existing notional is otherwise trusted from the audited packet.
    This cross-checks it against section (2a) of the operator-maintained portfolio
    snapshot (the SSOT for buy-side existing open orders). Fails closed when a KEEP
    ticker is missing from (2a), (2a) is absent while KEEP plans exist, the (2a)
    row is parse-blocked, or any value disagrees beyond cents tolerance. Only
    KEEP_EXISTING is checked here (NEW_ORDER / REPLACE_EXISTING / CANCEL_EXISTING
    are handled by the G3 submit-side reconciliation).
    """
    keep_plans = [
        plan
        for plan in _compile_ready_plans(audited_decision_packet)
        if _normalize_action(plan.get("final_action") or plan.get("action") or plan.get("order_intent"))
        == "KEEP_EXISTING"
    ]
    if not keep_plans:
        return

    _require(
        existing_buy_open_orders.section_present,
        "audited packet has KEEP_EXISTING plans but portfolio snapshot section (2a) "
        "existing_buy_open_orders_summary is missing or empty.",
    )

    for plan in keep_plans:
        ticker = _normalize_ticker(plan.get("ticker")) or "<missing>"
        order = existing_buy_open_orders.orders.get(ticker)
        _require(
            order is not None,
            f"KEEP_EXISTING ticker {ticker} is not present in portfolio snapshot (2a).",
        )
        _require(
            not order.data_gap,
            f"portfolio snapshot (2a) row for {ticker} is parse-blocked / data-gapped.",
        )

        audited_budget = _require_non_negative_decimal_field(plan, "existing_open_order_budget", ticker)
        audited_compiled = _require_non_negative_decimal_field(plan, "compiled_open_order_notional", ticker)

        _require(
            order.budget is not None,
            f"portfolio snapshot (2a) row for {ticker} has no parseable budget.",
        )
        _require(
            abs(audited_budget - order.budget) <= _BUDGET_TOLERANCE,
            f"KEEP_EXISTING {ticker}: audited existing_open_order_budget ({audited_budget}) "
            f"does not match portfolio snapshot (2a) budget ({order.budget}).",
        )

        # Snapshot internal consistency: stated vs reconstructed must agree when both exist.
        if order.stated_compiled_notional is not None and order.reconstructed_notional is not None:
            _require(
                abs(order.stated_compiled_notional - order.reconstructed_notional) <= _BUDGET_TOLERANCE,
                f"portfolio snapshot (2a) {ticker}: stated compiled notional "
                f"({order.stated_compiled_notional}) does not match reconstructed "
                f"Σ(qty×limit_price) ({order.reconstructed_notional}).",
            )

        snapshot_notional = (
            order.stated_compiled_notional
            if order.stated_compiled_notional is not None
            else order.reconstructed_notional
        )
        _require(
            snapshot_notional is not None,
            f"portfolio snapshot (2a) row for {ticker} has no verifiable compiled notional "
            "(neither stated nor reconstructable).",
        )
        _require(
            abs(audited_compiled - snapshot_notional) <= _BUDGET_TOLERANCE,
            f"KEEP_EXISTING {ticker}: audited compiled_open_order_notional ({audited_compiled}) "
            f"does not match portfolio snapshot (2a) notional ({snapshot_notional}).",
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
    existing_buy_open_orders: ExistingBuyOpenOrdersParseResult | None = None,
    require_safety_context: bool = False,
) -> dict[str, str]:
    """Validate the required Step 4 output text artifacts.

    Beyond existence / non-empty / compile-ready cross-checks, deterministic
    safety checks run on BUY_ORDERS rows:

    * numeric validity (malformed / negative / zero-on-submit) and exact-duplicate
      row rejection always run;
    * universe allowlist, submit-side budget floor, and aggregate max-new-ticker
      checks run when the corresponding context (settings / universe / budget) is
      supplied; a per-bucket new-ticker check (base vs extended, PR per-bucket)
      additionally runs whenever strategy settings are supplied;
    * ``hard_cap_open_orders_budget`` bounds the broader submit-side notional while
      ``target_new_buy_budget_this_run`` (G5) bounds the **net-new-only** notional
      (replacement / cancel / keep legs excluded); each runs when supplied;
    * total open-order exposure reconciliation (PR G3) runs when both an audited
      decision packet and a hard cap are supplied: totals are recomputed from
      ``audited_decision_packet.final_execution_plans`` (not the exec_summary
      aggregate PASS flags) and reconciled against the hard cap, capturing
      KEEP_EXISTING existing notional the submit-side floor omits;
    * KEEP_EXISTING independent verification (PR G4) runs when both an audited
      decision packet and parsed ``existing_buy_open_orders`` (portfolio snapshot
      section (2a), the buy-side SSOT) are supplied: each KEEP plan's existing
      budget / compiled notional is cross-checked against the operator snapshot
      rather than trusted from the audited packet alone.

    Conflicting-action detection (same ticker net-new + standalone cancel, or a
    ladder slot carrying inconsistent ``order_intent`` values) always runs.

    When ``require_safety_context`` is set, the validator fails closed if BUY
    submit rows are present but the universe / hard-cap budget context is missing,
    or if net-new BUY rows are present but the new-ticker ceiling or
    ``target_new_buy_budget_this_run`` is missing (the primary ``run_step4 parse``
    path opts in).
    With ``require_safety_context`` left ``False`` (the default for standalone
    callers), those checks remain *skipped* when their context is absent — so a
    standalone call without an audited packet / settings / budgets is **not** a
    complete safety validator and must not be treated as one.

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
    _validate_no_conflicting_buy_actions(buy_order_rows)

    allowed_universe = _resolve_allowed_universe(
        effective_allowed_buy_universe,
        strategy_settings,
    )

    if require_safety_context:
        _enforce_safety_context_present(
            buy_order_rows,
            allowed_universe=allowed_universe,
            hard_cap_open_orders_budget=hard_cap_open_orders_budget,
            target_new_buy_budget_this_run=target_new_buy_budget_this_run,
            max_new_tickers_per_week=max_new_tickers_per_week,
            strategy_settings=strategy_settings,
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

    if strategy_settings is not None:
        _validate_per_bucket_new_tickers(buy_order_rows, strategy_settings)

    if audited_decision_packet is not None:
        _require_buy_order_rows_match_final_plans(
            template4_orders=template4_orders,
            audited_decision_packet=audited_decision_packet,
        )
        _require_diagnostic_summary(exec_summary, audited_decision_packet)

    if audited_decision_packet is not None and hard_cap_open_orders_budget is not None:
        _validate_total_open_order_exposure(
            buy_order_rows=buy_order_rows,
            audited_decision_packet=audited_decision_packet,
            hard_cap_open_orders_budget=hard_cap_open_orders_budget,
        )

    if audited_decision_packet is not None and existing_buy_open_orders is not None:
        _validate_keep_existing_against_snapshot(
            audited_decision_packet=audited_decision_packet,
            existing_buy_open_orders=existing_buy_open_orders,
        )

    return {
        "template4_orders": template4_orders,
        "order_state_export": order_state_export,
        "exec_summary": exec_summary,
    }
