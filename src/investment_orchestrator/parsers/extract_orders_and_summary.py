"""Extract Template 4 order compiler artifacts from a manual Step 4 output."""

from __future__ import annotations

import argparse
from collections.abc import Collection, Mapping
from pathlib import Path
from typing import Any

from investment_orchestrator.common.io import atomic_write_text, read_text, write_text
from investment_orchestrator.parsers.portfolio_snapshot_existing_orders import (
    ExistingBuyOpenOrdersParseResult,
)
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
    "Unsafe parse-only diagnostics are temporarily disabled pending the canonical "
    "Step 4 grammar repair."
)
_UNSAFE_DISABLED_TOKEN = "unsafe_parse_only_temporarily_disabled"


def _build_argument_parser() -> argparse.ArgumentParser:
    """Build the standalone CLI grammar without long-option abbreviations."""
    parser = argparse.ArgumentParser(
        allow_abbrev=False,
        description=(
            "Standalone extractor for TEMPLATE4_ORDERS / ORDER_STATE_EXPORT / "
            "TEMPLATE5_EXEC_SUMMARY. NOT the primary Step 4 safety path: use "
            f"`{PRIMARY_PATH_HINT}` to produce validated order output. This command "
            "runs only in explicit --unsafe-parse-only (parser-development) mode."
        ),
    )
    parser.add_argument("--raw-output", required=True, help="Path to step4 raw_output.txt")
    parser.add_argument(
        "--template4-orders",
        required=True,
        help="Path to write template4_orders.txt",
    )
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
    parser.add_argument(
        "--unsafe-parse-only",
        action="store_true",
        help="Temporarily disabled pending the canonical Step 4 grammar repair.",
    )
    return parser


def _unique_long_option_prefixes(
    parser: argparse.ArgumentParser,
    target: str,
) -> frozenset[str]:
    """Return prefixes that argparse historically resolved only to ``target``."""
    long_options = tuple(
        option
        for option in parser._option_string_actions  # noqa: SLF001 - parser-owned grammar
        if option.startswith("--")
    )
    return frozenset(
        prefix
        for end in range(3, len(target) + 1)
        if (prefix := target[:end])
        and tuple(option for option in long_options if option.startswith(prefix)) == (target,)
    )


def _fixed_option_value_count(action: argparse.Action) -> int:
    """Return the fixed number of following values consumed by current CLI options."""
    if action.nargs is None:
        return 1
    if isinstance(action.nargs, int):
        return action.nargs
    # The current standalone grammar has no variable-arity options. Treat any future
    # such option conservatively as consuming no values until its preflight contract
    # is designed explicitly.
    return 0


def _unsafe_option_attempted(
    parser: argparse.ArgumentParser,
    raw_argv: list[str],
) -> bool:
    """Detect disabled unsafe-mode option attempts without inspecting their values."""
    unsafe_option = "--unsafe-parse-only"
    unsafe_prefixes = _unique_long_option_prefixes(parser, unsafe_option)
    option_actions = parser._option_string_actions  # noqa: SLF001 - parser-owned grammar

    index = 0
    while index < len(raw_argv):
        token = raw_argv[index]
        if token == "--":
            return False

        option, has_equals, _value = token.partition("=")
        if option in unsafe_prefixes:
            return True

        action = option_actions.get(option)
        if action is None or has_equals:
            index += 1
            continue

        value_count = _fixed_option_value_count(action)
        if value_count and index + 1 < len(raw_argv) and raw_argv[index + 1] == "--":
            return False
        index += 1 + value_count

    return False


def main(argv: list[str] | None = None) -> int:
    """Standalone extractor CLI (parser-development / debugging only).

    This is **not** the primary Step 4 safety path. By default it now **refuses**
    to run and directs the operator to ``run_step4 parse`` (which supplies the
    audited packet / strategy settings / budgets / universe and opts in to
    ``require_safety_context=True``). The former weaker ``--unsafe-parse-only``
    mode is temporarily disabled and fails before argument/path handling, input
    reads, parsing, validation, or publication. The authoritative
    ``extract_orders_and_summary`` function remains unchanged.
    """
    import sys

    raw_argv = list(sys.argv[1:] if argv is None else argv)
    parser = _build_argument_parser()
    if _unsafe_option_attempted(parser, raw_argv):
        print(_UNSAFE_DISABLED_TOKEN, file=sys.stderr)
        return 2

    parser.parse_args(raw_argv)

    # The unsafe flag is intercepted before argument parsing. Every remaining
    # standalone invocation fails closed without parsing or writing artifacts.
    print(f"{parser.prog}: {_REFUSAL_MESSAGE}", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
