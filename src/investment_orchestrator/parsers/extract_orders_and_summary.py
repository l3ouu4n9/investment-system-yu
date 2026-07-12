"""Extract Template 4 order compiler artifacts from a manual Step 4 output."""

from __future__ import annotations

from collections.abc import Collection, Mapping
import json
from pathlib import Path
from typing import Any

from investment_orchestrator.common.io import atomic_write_text, read_text, write_text
from investment_orchestrator.parsers.portfolio_snapshot_existing_orders import (
    ExistingBuyOpenOrdersParseResult,
)
from investment_orchestrator.validators.validate_orders_output import (
    validate_orders_output,
    validate_orders_output_texts,
)


class Step4ExtractionError(ValueError):
    """Raised when a Step 4 raw output cannot be parsed safely."""


class UnsafeParseOnlyError(RuntimeError):
    """Path-free, code-owned failure from stdout-only unsafe diagnostics."""


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
UNSAFE_STDOUT_SCHEMA = "step4_unsafe_parse_only_stdout_v1"
_UNSAFE_STDOUT_METADATA = {
    "schema": UNSAFE_STDOUT_SCHEMA,
    "status": "UNSAFE_UNVALIDATED_DIAGNOSTIC_ONLY",
    "deterministic_order_ready": False,
    "manual_order_authorized": False,
    "broker_ready": False,
    "canonical_artifact": False,
}


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


def build_unsafe_parse_only_stdout(
    *, raw_output_path: str | Path
) -> dict[str, str | bool]:
    """Return one validated-as-diagnostic, non-authoritative stdout envelope."""
    try:
        raw_text = read_text(raw_output_path)
    except (OSError, UnicodeError, ValueError):
        raise UnsafeParseOnlyError("unsafe_parse_only_input_read_failed") from None
    try:
        template4_text, state_text, summary_text = parse_step4_output_text(raw_text)
    except (Step4ExtractionError, ValueError):
        raise UnsafeParseOnlyError("unsafe_parse_only_input_invalid") from None
    template4_text = _normalize_artifact_text(template4_text)
    state_text = _normalize_artifact_text(state_text)
    summary_text = _normalize_artifact_text(summary_text)
    try:
        validate_orders_output_texts(
            template4_orders=template4_text,
            order_state_export=state_text,
            exec_summary=summary_text,
        )
    except ValueError:
        raise UnsafeParseOnlyError("unsafe_parse_only_validation_failed") from None
    return {
        **_UNSAFE_STDOUT_METADATA,
        "template4_orders_text": template4_text,
        "order_state_export_text": state_text,
        "exec_summary_text": summary_text,
    }


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
    existing_buy_open_orders: ExistingBuyOpenOrdersParseResult | None = None,
    require_safety_context: bool = False,
) -> tuple[str, str, str]:
    """Read, parse, validate, and write Step 4 text artifacts.

    Validate-before-write (PR G2): candidate artifacts are written to a
    ``quarantine/`` subdirectory and validated there; canonical artifacts are
    published only after deterministic validation passes. On validation failure
    the exception propagates and the canonical artifacts are never written or
    overwritten (a prior-good set is preserved); the rejected candidates remain
    under ``quarantine/`` as diagnostics.

    Atomic publish (PR G2.2): each canonical artifact is published via
    ``atomic_write_text`` (temp-in-dir + ``os.replace``), so a canonical file is
    never left with partial content even on a mid-publish crash. The replace is
    per-file, not group-atomic across the three files; see the publish-site
    comment for the group-level limitation and recovery semantics.

    Deterministic post-order safety context (settings / universe / budgets) is
    forwarded to ``validate_orders_output``; omitting it preserves the prior
    (narrower) validation behavior for standalone callers. ``require_safety_context``
    (set by the primary ``run_step4 parse`` path) makes the validator fail closed
    when BUY submit rows are present but that context is missing.
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
        existing_buy_open_orders=existing_buy_open_orders,
        require_safety_context=require_safety_context,
    )

    # Validation passed: publish canonical artifacts atomically (G2.2), then clean
    # quarantine. Each canonical file is written via atomic_write_text (temp-in-dir +
    # os.replace), so no canonical file is ever left with partial content — a reader
    # sees either the complete prior file or the complete new one.
    #
    # Per-file atomic, NOT group-atomic: the three replaces are independent, so a
    # crash between them can still leave a *mixed* set (some files updated, others
    # stale/absent) — but never a partially written file. The validate-before-publish
    # ordering still guarantees only validated content is ever published, and because
    # quarantine cleanup runs only after all three replaces succeed, the full validated
    # set survives under quarantine/ for recovery on a mid-publish failure. A
    # manifest/versioned-directory publish for true group atomicity is a future option.
    atomic_write_text(template4_orders_path, _normalize_artifact_text(template4_orders_text))
    atomic_write_text(order_state_export_path, _normalize_artifact_text(order_state_export_text))
    atomic_write_text(exec_summary_path, _normalize_artifact_text(exec_summary_text))
    _cleanup_quarantine([quarantine_template4, quarantine_state, quarantine_summary])

    return template4_orders_text, order_state_export_text, exec_summary_text


# --- standalone CLI safety messaging (G6) ------------------------------------

PRIMARY_PATH_HINT = (
    "PYTHONPATH=src uv run python -m investment_orchestrator.cli.run_step4 parse"
)
_REFUSAL_MESSAGE = (
    "refusing to run: this standalone extractor is NOT the primary Step 4 safety "
    "path and does not supply strategy settings / budgets / universe / audited "
    "packet (require_safety_context=False), so its substantive order-safety checks "
    "are skipped.\n"
    f"Use the safe primary path instead:\n    {PRIMARY_PATH_HINT}\n"
    "If you only need parser-development / debugging output (NOT validated orders), "
    "re-run with --unsafe-parse-only; diagnostics are emitted only to stdout."
)
_UNSAFE_WARNING = (
    "WARNING: --unsafe-parse-only is a parser-development / debugging mode. It does "
    "NOT perform complete Step 4 order-safety validation (no strategy settings / "
    "budgets / universe / audited packet; require_safety_context=False). Its output "
    "is one stdout-only JSON document that is unvalidated, non-authoritative, "
    "not manual-order-ready, not broker-ready, "
    "and not accepted by deterministic final validation. It MUST NOT be treated as "
    "validated order output or used to approve trades. "
    f"Use the primary path to approve orders: {PRIMARY_PATH_HINT}"
)


def main(argv: list[str] | None = None) -> int:
    """Standalone extractor CLI (parser-development / debugging only).

    This is **not** the primary Step 4 safety path. By default it now **refuses**
    to run and directs the operator to ``run_step4 parse`` (which supplies the
    audited packet / strategy settings / budgets / universe and opts in to
    ``require_safety_context=True``). Weaker parsing is available **only** behind
    the explicit ``--unsafe-parse-only`` flag, which emits one stdout-only JSON
    diagnostic and prints a clear non-safety warning to stderr. The
    authoritative ``extract_orders_and_summary`` function remains separate.
    """
    import argparse
    import sys

    parser = argparse.ArgumentParser(
        description=(
            "Standalone extractor for TEMPLATE4_ORDERS / ORDER_STATE_EXPORT / "
            "TEMPLATE5_EXEC_SUMMARY. NOT the primary Step 4 safety path: use "
            f"`{PRIMARY_PATH_HINT}` to produce validated order output. This command "
            "runs only in explicit --unsafe-parse-only (parser-development) mode."
        )
    )
    parser.add_argument("--raw-output", required=True, help="Path to step4 raw_output.txt")
    parser.add_argument(
        "--template4-orders",
        help="Legacy per-file output option; forbidden with --unsafe-parse-only.",
    )
    parser.add_argument(
        "--order-state-export",
        help="Legacy per-file output option; forbidden with --unsafe-parse-only.",
    )
    parser.add_argument(
        "--exec-summary",
        help="Legacy per-file output option; forbidden with --unsafe-parse-only.",
    )
    parser.add_argument(
        "--unsafe-debug-output-dir",
        help="Obsolete unsafe filesystem output option; always rejected.",
    )
    parser.add_argument(
        "--unsafe-parse-only",
        action="store_true",
        help=(
            "Run the legacy weaker parse-only extraction (require_safety_context=False; "
            "no settings/budgets/universe/audited packet). Output is NOT validated "
            "order output and must not be used to approve trades."
        ),
    )
    args = parser.parse_args(argv)

    if not args.unsafe_parse_only:
        # Fail closed: do not silently run weaker validation or write artifacts.
        print(f"{parser.prog}: {_REFUSAL_MESSAGE}", file=sys.stderr)
        return 2

    legacy_outputs = (
        args.template4_orders,
        args.order_state_export,
        args.exec_summary,
        args.unsafe_debug_output_dir,
    )
    if any(value is not None for value in legacy_outputs):
        print(
            f"{parser.prog}: unsafe_parse_only_stdout_only_legacy_output_flags_forbidden",
            file=sys.stderr,
        )
        return 2

    print(_UNSAFE_WARNING, file=sys.stderr)
    try:
        result = build_unsafe_parse_only_stdout(raw_output_path=Path(args.raw_output))
    except UnsafeParseOnlyError as exc:
        print(f"{parser.prog}: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(result, ensure_ascii=False, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
