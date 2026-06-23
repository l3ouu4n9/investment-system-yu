"""Extract Template 4 order compiler artifacts from a manual Step 4 output."""

from __future__ import annotations

from collections.abc import Collection, Mapping
from pathlib import Path
from typing import Any

from investment_orchestrator.common.io import read_text, write_text
from investment_orchestrator.validators.validate_orders_output import validate_orders_output


class Step4ExtractionError(ValueError):
    """Raised when a Step 4 raw output cannot be parsed safely."""


def extract_required_block(text: str, start_marker: str, end_marker: str) -> str:
    """Return the text between two required markers."""
    start = text.find(start_marker)
    if start == -1:
        raise Step4ExtractionError(f"Missing required marker {start_marker!r}.")
    end = text.rfind(end_marker)
    if end == -1 or end <= start:
        raise Step4ExtractionError(f"Missing or malformed closing marker {end_marker!r}.")
    return text[start + len(start_marker) : end].strip()


QUARANTINE_DIRNAME = "quarantine"


def _normalize_artifact_text(text: str) -> str:
    """Canonical Step 4 artifact normalization (must match canonical writes)."""
    return text.rstrip() + "\n"


def _quarantine_path(path: str | Path) -> Path:
    """Return the quarantine candidate path for a canonical artifact path."""
    canonical = Path(path)
    return canonical.parent / QUARANTINE_DIRNAME / canonical.name


def _cleanup_quarantine(paths: list[Path]) -> None:
    """Remove this run's quarantine files, then the dir only if it is empty."""
    parents: set[Path] = set()
    for path in paths:
        try:
            path.unlink()
        except FileNotFoundError:
            pass
        parents.add(path.parent)
    for parent in parents:
        try:
            parent.rmdir()
        except OSError:
            # Directory not empty or already gone; leave it.
            pass


def parse_step4_output_text(raw_text: str) -> tuple[str, str, str]:
    """Parse a raw Step 4 response into the three required text blocks."""
    template4_orders_text = extract_required_block(
        raw_text,
        "TEMPLATE4_ORDERS_START",
        "TEMPLATE4_ORDERS_END",
    )
    order_state_export_text = extract_required_block(
        raw_text,
        "ORDER_STATE_EXPORT_START",
        "ORDER_STATE_EXPORT_END",
    )
    exec_summary_text = extract_required_block(
        raw_text,
        "TEMPLATE5_EXEC_SUMMARY_START",
        "TEMPLATE5_EXEC_SUMMARY_END",
    )
    return template4_orders_text, order_state_export_text, exec_summary_text


def extract_orders_and_summary(
    *,
    raw_output_path: str | Path,
    template4_orders_path: str | Path,
    order_state_export_path: str | Path,
    exec_summary_path: str | Path,
    audited_decision_packet: Any | None = None,
    strategy_settings: Mapping[str, Any] | None = None,
    effective_allowed_buy_universe: Collection[str] | None = None,
    hard_cap_open_orders_budget: Any | None = None,
    target_new_buy_budget_this_run: Any | None = None,
    max_new_tickers_per_week: int | None = None,
) -> tuple[str, str, str]:
    """Read, parse, validate, and write Step 4 text artifacts.

    Validate-before-write (PR G2): candidate artifacts are written to a
    ``quarantine/`` subdirectory and validated there; canonical artifacts are
    published only after deterministic validation passes. On validation failure
    the exception propagates and the canonical artifacts are never written or
    overwritten (a prior-good set is preserved); the rejected candidates remain
    under ``quarantine/`` as diagnostics.

    Deterministic post-order safety context (settings / universe / budgets) is
    forwarded to ``validate_orders_output``; omitting it preserves the prior
    (narrower) validation behavior for standalone callers.
    """
    template4_orders_text, order_state_export_text, exec_summary_text = parse_step4_output_text(
        read_text(raw_output_path)
    )

    quarantine_template4 = _quarantine_path(template4_orders_path)
    quarantine_state = _quarantine_path(order_state_export_path)
    quarantine_summary = _quarantine_path(exec_summary_path)

    # Write candidates to quarantine using the exact canonical normalization,
    # then validate the quarantine paths (validator API unchanged / G1 semantics).
    write_text(quarantine_template4, _normalize_artifact_text(template4_orders_text))
    write_text(quarantine_state, _normalize_artifact_text(order_state_export_text))
    write_text(quarantine_summary, _normalize_artifact_text(exec_summary_text))

    validate_orders_output(
        template4_orders_path=quarantine_template4,
        order_state_export_path=quarantine_state,
        exec_summary_path=quarantine_summary,
        audited_decision_packet=audited_decision_packet,
        strategy_settings=strategy_settings,
        effective_allowed_buy_universe=effective_allowed_buy_universe,
        hard_cap_open_orders_budget=hard_cap_open_orders_budget,
        target_new_buy_budget_this_run=target_new_buy_budget_this_run,
        max_new_tickers_per_week=max_new_tickers_per_week,
    )

    # Validation passed: publish canonical artifacts, then clean quarantine.
    write_text(template4_orders_path, _normalize_artifact_text(template4_orders_text))
    write_text(order_state_export_path, _normalize_artifact_text(order_state_export_text))
    write_text(exec_summary_path, _normalize_artifact_text(exec_summary_text))
    _cleanup_quarantine([quarantine_template4, quarantine_state, quarantine_summary])

    return template4_orders_text, order_state_export_text, exec_summary_text


def main() -> int:
    import argparse

    parser = argparse.ArgumentParser(
        description="Extract TEMPLATE4_ORDERS, ORDER_STATE_EXPORT, and TEMPLATE5_EXEC_SUMMARY from Step 4 output."
    )
    parser.add_argument("--raw-output", required=True, help="Path to step4 raw_output.txt")
    parser.add_argument("--template4-orders", required=True, help="Path to write template4_orders.txt")
    parser.add_argument(
        "--order-state-export",
        required=True,
        help="Path to write order_state_export.txt",
    )
    parser.add_argument(
        "--exec-summary",
        required=True,
        help="Path to write exec_summary.txt",
    )
    args = parser.parse_args()

    extract_orders_and_summary(
        raw_output_path=Path(args.raw_output),
        template4_orders_path=Path(args.template4_orders),
        order_state_export_path=Path(args.order_state_export),
        exec_summary_path=Path(args.exec_summary),
    )
    print(args.template4_orders)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
