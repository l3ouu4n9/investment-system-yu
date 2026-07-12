"""Tests for the temporarily disabled standalone Step 4 unsafe parser CLI."""

from __future__ import annotations

import builtins
import os
from pathlib import Path

import pytest

from investment_orchestrator.parsers import extract_orders_and_summary as extract_mod
from investment_orchestrator.parsers.extract_orders_and_summary import main


DISABLED_TOKEN = "unsafe_parse_only_temporarily_disabled"
UNSAFE_OPTION = "--unsafe-parse-only"
HISTORICAL_UNIQUE_UNSAFE_PREFIXES = tuple(
    UNSAFE_OPTION[:end] for end in range(3, len(UNSAFE_OPTION) + 1)
)


def _raw_output(buy_body: str = "NONE") -> str:
    return (
        "TEMPLATE4_ORDERS_START\n"
        "TEMPLATE4_ORDERS\nSELL_ORDERS\nNONE\nBUY_ORDERS\n" + buy_body + "\n"
        "TEMPLATE4_ORDERS_END\n"
        "ORDER_STATE_EXPORT_START\nORDER_STATE_EXPORT\nNONE\nORDER_STATE_EXPORT_END\n"
        "TEMPLATE5_EXEC_SUMMARY_START\nTEMPLATE5_EXEC_SUMMARY\nno diagnostics\n"
        "TEMPLATE5_EXEC_SUMMARY_END\n"
    )


def _default_argv(tmp_path: Path, raw_text: str) -> tuple[list[str], dict[str, Path]]:
    raw = tmp_path / "raw_output.txt"
    raw.write_text(raw_text, encoding="utf-8")
    outputs = {
        "template4": tmp_path / "template4_orders.txt",
        "state": tmp_path / "order_state_export.txt",
        "summary": tmp_path / "exec_summary.txt",
    }
    return (
        [
            "--raw-output",
            str(raw),
            "--template4-orders",
            str(outputs["template4"]),
            "--order-state-export",
            str(outputs["state"]),
            "--exec-summary",
            str(outputs["summary"]),
        ],
        outputs,
    )


def _tree_snapshot(root: Path) -> dict[str, bytes | None]:
    return {
        str(path.relative_to(root)): None if path.is_dir() else path.read_bytes()
        for path in sorted(root.rglob("*"))
    }


def test_default_invocation_still_refuses_and_writes_nothing(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    argv, outputs = _default_argv(tmp_path, _raw_output())
    assert main(argv) == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "refusing to run" in captured.err
    assert "run_step4 parse" in captured.err
    assert "temporarily disabled" in captured.err
    assert all(not path.exists() for path in outputs.values())


def test_default_invocation_with_buy_rows_still_refuses_and_writes_nothing(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    body = "ticker=ZZZZ | step_name=L1 | shares=1 | limit_price=10.00 | order_intent=NEW_ORDER"
    argv, outputs = _default_argv(tmp_path, _raw_output(body))
    assert main(argv) == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "refusing to run" in captured.err
    assert all(not path.exists() for path in outputs.values())


@pytest.mark.parametrize(
    "argv",
    (
        ["--unsafe-parse-only"],
        ["--unsafe-parse-only", "--unsafe-parse-only"],
        ["--unsafe-parse-only=true"],
        ["--unsafe-parse-only=/sensitive/operator/path"],
        ["--unsafe-parse-only=value=with=equals"],
        ["--unsafe-parse-only", "--raw-output", "missing.txt"],
        ["--raw-output", "bad\x00input", "--unsafe-parse-only"],
        ["--template4-orders", "bad\x00output", "--unsafe-parse-only"],
        ["--order-state-export", "bad\x00output", "--unsafe-parse-only"],
        ["--exec-summary", "bad\x00output", "--unsafe-parse-only"],
        ["--unsafe-debug-output-dir", "bad\x00output", "--unsafe-parse-only"],
        ["--unknown-option", "value", "--unsafe-parse-only"],
    ),
    ids=(
        "missing-all-arguments",
        "duplicate-exact-flags",
        "exact-assignment-true",
        "exact-assignment-sensitive-path",
        "exact-assignment-multiple-equals",
        "missing-input-file",
        "nul-raw-input",
        "nul-template-output",
        "nul-state-output",
        "nul-summary-output",
        "nul-obsolete-debug-output",
        "unknown-option",
    ),
)
def test_unsafe_parse_only_always_returns_exact_disable_token_without_filesystem_access(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    argv: list[str],
) -> None:
    before = _tree_snapshot(tmp_path)
    assert main(argv) == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == DISABLED_TOKEN + "\n"
    assert _tree_snapshot(tmp_path) == before


@pytest.mark.parametrize("prefix", HISTORICAL_UNIQUE_UNSAFE_PREFIXES)
def test_every_historically_unique_unsafe_prefix_is_disabled(
    prefix: str,
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert main([prefix]) == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == DISABLED_TOKEN + "\n"


@pytest.mark.parametrize("prefix", HISTORICAL_UNIQUE_UNSAFE_PREFIXES)
def test_every_historically_unique_unsafe_prefix_assignment_is_disabled_and_redacted(
    prefix: str,
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert main([prefix + "=/sensitive/operator/path=secret"]) == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == DISABLED_TOKEN + "\n"


@pytest.mark.parametrize(
    "value",
    (
        "/sensitive/operator/path",
        "relative/private/path",
        "value with spaces",
        "unicodé/秘密",
        "value=with=equals",
        "--raw-output=/another/path",
        "nul\x00value",
    ),
)
def test_unsafe_assignment_values_are_never_echoed(
    value: str,
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert main([f"{UNSAFE_OPTION}={value}"]) == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == DISABLED_TOKEN + "\n"
    assert value not in captured.err


@pytest.mark.parametrize(
    "argv",
    (
        ["--raw-output=--unsafe-parse-only"],
        ["--raw-output", "--unsafe-parse-only"],
        ["--template4-orders", "report--unsafe-parse-only.txt"],
        ["--order-state-export=folder/--unsafe/state.txt"],
        ["filename-containing---unsafe-parse-only.txt"],
    ),
)
def test_unsafe_text_used_as_an_option_value_does_not_trigger_preflight(argv: list[str]) -> None:
    parser = extract_mod._build_argument_parser()
    assert extract_mod._unsafe_option_attempted(parser, argv) is False


def test_false_positive_safe_complete_invocation_uses_normal_refusal(
    capsys: pytest.CaptureFixture[str],
) -> None:
    argv = [
        "--raw-output=--unsafe-parse-only",
        "--template4-orders",
        "report--unsafe-parse-only.txt",
        "--order-state-export",
        "state.txt",
        "--exec-summary",
        "summary.txt",
    ]
    assert main(argv) == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert DISABLED_TOKEN not in captured.err
    assert "refusing to run" in captured.err


@pytest.mark.parametrize(
    ("argv", "unsafe_attempted"),
    (
        ([UNSAFE_OPTION, "--"], True),
        (["--", UNSAFE_OPTION], False),
        (["--", UNSAFE_OPTION + "=/sensitive/operator/path"], False),
        ([UNSAFE_OPTION, "--", UNSAFE_OPTION], True),
        (["--", UNSAFE_OPTION, UNSAFE_OPTION], False),
    ),
)
def test_end_of_options_delimiter_bounds_unsafe_preflight(
    argv: list[str],
    unsafe_attempted: bool,
) -> None:
    parser = extract_mod._build_argument_parser()
    assert extract_mod._unsafe_option_attempted(parser, argv) is unsafe_attempted


def test_post_delimiter_unsafe_text_does_not_receive_disable_token(
    capsys: pytest.CaptureFixture[str],
) -> None:
    with pytest.raises(SystemExit) as exc_info:
        main(["--", UNSAFE_OPTION])
    assert exc_info.value.code == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert DISABLED_TOKEN not in captured.err


@pytest.mark.parametrize(
    "unsafe_token",
    tuple(HISTORICAL_UNIQUE_UNSAFE_PREFIXES)
    + tuple(prefix + "=/sensitive/operator/path" for prefix in HISTORICAL_UNIQUE_UNSAFE_PREFIXES),
)
def test_unsafe_disable_runs_before_parsing_validation_and_publication(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    unsafe_token: str,
) -> None:
    def forbidden(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("unsafe disabled path invoked an authority-bearing helper")

    monkeypatch.setattr(extract_mod, "read_text", forbidden)
    monkeypatch.setattr(extract_mod, "parse_step4_output_text", forbidden)
    monkeypatch.setattr(extract_mod, "validate_orders_output", forbidden)
    monkeypatch.setattr(extract_mod, "extract_orders_and_summary", forbidden)
    monkeypatch.setattr(extract_mod, "_quarantine_path", forbidden)
    monkeypatch.setattr(extract_mod, "write_text", forbidden)
    monkeypatch.setattr(extract_mod, "atomic_write_text", forbidden)
    monkeypatch.setattr(builtins, "open", forbidden)
    monkeypatch.setattr(Path, "exists", forbidden)
    monkeypatch.setattr(Path, "resolve", forbidden)
    monkeypatch.setattr(Path, "open", forbidden)
    monkeypatch.setattr(Path, "mkdir", forbidden)
    monkeypatch.setattr(os, "replace", forbidden)

    assert main([unsafe_token, "--raw-output", "never-read.txt"]) == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == DISABLED_TOKEN + "\n"


def test_long_option_abbreviations_are_disabled_for_normal_argparse(
    capsys: pytest.CaptureFixture[str],
) -> None:
    with pytest.raises(SystemExit) as exc_info:
        main(
            [
                "--raw",
                "input.txt",
                "--template4-orders",
                "orders.txt",
                "--order-state-export",
                "state.txt",
                "--exec-summary",
                "summary.txt",
            ]
        )
    assert exc_info.value.code == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert DISABLED_TOKEN not in captured.err
    assert "arguments are required: --raw-output" in captured.err


@pytest.mark.parametrize("argv", ([], ["--ordinary-unknown-option", "value"]))
def test_ordinary_argument_errors_remain_argparse_errors(
    argv: list[str],
    capsys: pytest.CaptureFixture[str],
) -> None:
    with pytest.raises(SystemExit) as exc_info:
        main(argv)
    assert exc_info.value.code == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert DISABLED_TOKEN not in captured.err
    assert "usage:" in captured.err


def test_unsafe_disable_preserves_prior_canonical_artifacts_byte_for_byte(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    canonical = tmp_path / "artifacts" / "current" / "step4_order_compiler"
    canonical.mkdir(parents=True)
    for filename in ("template4_orders.txt", "order_state_export.txt", "exec_summary.txt"):
        (canonical / filename).write_bytes(("PRIOR " + filename + "\n").encode())
    before = _tree_snapshot(tmp_path)

    assert main(["--unsafe-parse-only", "--raw-output", str(tmp_path / "missing.txt")]) == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == DISABLED_TOKEN + "\n"
    assert _tree_snapshot(tmp_path) == before
