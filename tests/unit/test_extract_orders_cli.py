"""Tests for the standalone extractor CLI safety gate (G6).

The standalone ``extract_orders_and_summary.main`` is a parser-development /
debugging entrypoint, NOT the primary Step 4 safety path. By default it must
refuse to run (and write nothing), directing the operator to ``run_step4 parse``;
the legacy weaker parse-only behavior is allowed only behind the explicit
``--unsafe-parse-only`` flag, which emits a clear non-safety warning.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from investment_orchestrator.parsers.extract_orders_and_summary import main


def _raw_output(buy_body: str = "NONE") -> str:
    return (
        "TEMPLATE4_ORDERS_START\n"
        "TEMPLATE4_ORDERS\nSELL_ORDERS\nNONE\nBUY_ORDERS\n" + buy_body + "\n"
        "TEMPLATE4_ORDERS_END\n"
        "ORDER_STATE_EXPORT_START\nORDER_STATE_EXPORT\nNONE\nORDER_STATE_EXPORT_END\n"
        "TEMPLATE5_EXEC_SUMMARY_START\nTEMPLATE5_EXEC_SUMMARY\nno diagnostics\nTEMPLATE5_EXEC_SUMMARY_END\n"
    )


def _argv(tmp_path: Path, raw_text: str, *, unsafe: bool) -> tuple[list[str], dict[str, Path]]:
    raw = tmp_path / "raw_output.txt"
    raw.write_text(raw_text, encoding="utf-8")
    out = {
        "template4": tmp_path / "template4_orders.txt",
        "state": tmp_path / "order_state_export.txt",
        "summary": tmp_path / "exec_summary.txt",
    }
    argv = [
        "--raw-output", str(raw),
        "--template4-orders", str(out["template4"]),
        "--order-state-export", str(out["state"]),
        "--exec-summary", str(out["summary"]),
    ]
    if unsafe:
        argv.append("--unsafe-parse-only")
    return argv, out


# --- default mode: fail closed, write nothing --------------------------------


def test_default_invocation_refuses_and_returns_nonzero(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    argv, out = _argv(tmp_path, _raw_output(), unsafe=False)
    rc = main(argv)
    err = capsys.readouterr().err
    assert rc == 2
    assert "refusing to run" in err
    assert "run_step4 parse" in err  # directs operator to the primary safe path
    assert "--unsafe-parse-only" in err  # tells how to get debug behavior
    # Nothing was written.
    assert not out["template4"].exists()
    assert not out["state"].exists()
    assert not out["summary"].exists()


def test_default_invocation_with_buy_rows_still_refuses_and_writes_nothing(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    body = "ticker=ZZZZ | step_name=L1 | shares=1 | limit_price=10.00 | order_intent=NEW_ORDER"
    argv, out = _argv(tmp_path, _raw_output(body), unsafe=False)
    rc = main(argv)
    assert rc == 2
    assert "refusing to run" in capsys.readouterr().err
    assert not out["template4"].exists()


# --- explicit unsafe parse-only mode -----------------------------------------


def test_unsafe_flag_runs_legacy_parse_and_writes_artifacts(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    argv, out = _argv(tmp_path, _raw_output(), unsafe=True)
    rc = main(argv)
    captured = capsys.readouterr()
    assert rc == 0
    # Legacy behavior: canonical artifacts are written...
    assert out["template4"].is_file()
    assert out["state"].is_file()
    assert out["summary"].is_file()
    # ...and the path is printed to stdout (backward-compatible).
    assert str(out["template4"]) in captured.out


def test_unsafe_flag_emits_non_safety_warning(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    argv, _ = _argv(tmp_path, _raw_output(), unsafe=True)
    main(argv)
    err = capsys.readouterr().err
    assert "WARNING" in err
    assert "must not" in err.lower()
    assert "used to approve trades" in err.lower()
    assert "run_step4 parse" in err  # warning points at the primary path too


def test_unsafe_flag_allows_weaker_parse_even_with_out_of_universe_buy_row(
    tmp_path: Path,
) -> None:
    # Parser-dev mode runs the weaker validator (require_safety_context=False, no
    # universe), so an out-of-universe NEW_ORDER row that the primary path would
    # reject is parsed here. This documents WHY the mode is unsafe.
    body = "ticker=ZZZZ | step_name=L1 | shares=1 | limit_price=10.00 | order_intent=NEW_ORDER"
    argv, out = _argv(tmp_path, _raw_output(body), unsafe=True)
    rc = main(argv)
    assert rc == 0
    assert "ticker=ZZZZ" in out["template4"].read_text(encoding="utf-8")
