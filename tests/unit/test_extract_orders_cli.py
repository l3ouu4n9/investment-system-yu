"""Tests for the stdout-only standalone Step 4 unsafe parser CLI."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from investment_orchestrator.parsers import extract_orders_and_summary as extract_mod
from investment_orchestrator.parsers.extract_orders_and_summary import (
    UNSAFE_STDOUT_SCHEMA,
    Step4ExtractionError,
    main,
    parse_step4_output_text,
)


def _raw_output(buy_body: str = "NONE") -> str:
    return (
        "TEMPLATE4_ORDERS_START\n"
        "TEMPLATE4_ORDERS\nSELL_ORDERS\nNONE\nBUY_ORDERS\n"
        + buy_body
        + "\nTEMPLATE4_ORDERS_END\n"
        "ORDER_STATE_EXPORT_START\nORDER_STATE_EXPORT\nNONE\nORDER_STATE_EXPORT_END\n"
        "TEMPLATE5_EXEC_SUMMARY_START\nTEMPLATE5_EXEC_SUMMARY\nno diagnostics\n"
        "TEMPLATE5_EXEC_SUMMARY_END\n"
    )


def _unsafe_argv(tmp_path: Path, raw_text: str) -> tuple[list[str], Path]:
    raw = tmp_path / "raw_output.txt"
    raw.write_text(raw_text, encoding="utf-8")
    return ["--raw-output", str(raw), "--unsafe-parse-only"], raw


def _expected_envelope(buy_body: str = "NONE") -> dict[str, object]:
    return {
        "schema": UNSAFE_STDOUT_SCHEMA,
        "status": "UNSAFE_UNVALIDATED_DIAGNOSTIC_ONLY",
        "deterministic_order_ready": False,
        "manual_order_authorized": False,
        "broker_ready": False,
        "canonical_artifact": False,
        "template4_orders_text": (
            "TEMPLATE4_ORDERS\nSELL_ORDERS\nNONE\nBUY_ORDERS\n" + buy_body + "\n"
        ),
        "order_state_export_text": "ORDER_STATE_EXPORT\nNONE\n",
        "exec_summary_text": "TEMPLATE5_EXEC_SUMMARY\nno diagnostics\n",
    }


def _tree_snapshot(root: Path) -> dict[str, bytes | None]:
    return {
        str(path.relative_to(root)): None if path.is_dir() else path.read_bytes()
        for path in sorted(root.rglob("*"))
    }


def test_default_invocation_refuses_and_writes_nothing(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    raw = tmp_path / "raw.txt"
    raw.write_text(_raw_output(), encoding="utf-8")
    outputs = [tmp_path / name for name in ("orders.txt", "state.txt", "summary.txt")]
    rc = main(
        [
            "--raw-output",
            str(raw),
            "--template4-orders",
            str(outputs[0]),
            "--order-state-export",
            str(outputs[1]),
            "--exec-summary",
            str(outputs[2]),
        ]
    )
    captured = capsys.readouterr()
    assert rc == 2
    assert captured.out == ""
    assert "refusing to run" in captured.err
    assert "run_step4 parse" in captured.err
    assert "--unsafe-parse-only" in captured.err
    assert all(not path.exists() for path in outputs)


def test_unsafe_success_emits_exactly_one_deterministic_json_document(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    argv, _ = _unsafe_argv(tmp_path, _raw_output())
    assert main(argv) == 0
    captured = capsys.readouterr()
    expected = _expected_envelope()
    assert captured.out == json.dumps(
        expected,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ) + "\n"
    assert json.loads(captured.out) == expected


def test_unsafe_warning_remains_on_stderr_only(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    argv, _ = _unsafe_argv(tmp_path, _raw_output())
    assert main(argv) == 0
    captured = capsys.readouterr()
    for phrase in (
        "warning",
        "stdout-only",
        "unvalidated",
        "non-authoritative",
        "not manual-order-ready",
        "not broker-ready",
        "not accepted by deterministic final validation",
        "used to approve trades",
        "run_step4 parse",
    ):
        assert phrase in captured.err.lower()
    assert "WARNING" not in captured.out


def test_unsafe_mode_retains_its_explicitly_weaker_nonblank_parse_contract(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    body = "ticker=ZZZZ | step_name=L1 | shares=1 | limit_price=10.00 | order_intent=NEW_ORDER"
    argv, _ = _unsafe_argv(tmp_path, _raw_output(body))
    assert main(argv) == 0
    envelope = json.loads(capsys.readouterr().out)
    assert body in envelope["template4_orders_text"]
    assert envelope["deterministic_order_ready"] is False


def test_unsafe_mode_calls_no_publication_or_filesystem_writer(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    argv, raw = _unsafe_argv(tmp_path, _raw_output())
    canonical = tmp_path / "artifacts" / "current" / "step4_order_compiler"
    canonical.mkdir(parents=True)
    for filename in ("template4_orders.txt", "order_state_export.txt", "exec_summary.txt"):
        (canonical / filename).write_text(f"PRIOR {filename}\n", encoding="utf-8")

    def forbidden(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("filesystem publication helper called")

    monkeypatch.setattr(extract_mod, "extract_orders_and_summary", forbidden)
    monkeypatch.setattr(extract_mod, "write_text", forbidden)
    monkeypatch.setattr(extract_mod, "atomic_write_text", forbidden)
    before = _tree_snapshot(tmp_path)
    assert raw.is_file()
    assert main(argv) == 0
    assert json.loads(capsys.readouterr().out)["canonical_artifact"] is False
    assert _tree_snapshot(tmp_path) == before


@pytest.mark.parametrize(
    "flag",
    (
        "--template4-orders",
        "--order-state-export",
        "--exec-summary",
        "--unsafe-debug-output-dir",
    ),
)
@pytest.mark.parametrize("value", ("legacy-output", "bad\x00value"))
def test_unsafe_legacy_output_options_are_rejected_before_path_use(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    flag: str,
    value: str,
) -> None:
    argv, raw = _unsafe_argv(tmp_path, _raw_output())
    argv.extend([flag, value])
    assert main(argv) == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "unsafe_parse_only_stdout_only_legacy_output_flags_forbidden" in captured.err
    assert "Traceback" not in captured.err
    assert "embedded null" not in captured.err
    assert {path.relative_to(tmp_path) for path in tmp_path.rglob("*")} == {
        raw.relative_to(tmp_path)
    }


def test_multiple_legacy_output_options_fail_without_partial_behavior(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    argv, raw = _unsafe_argv(tmp_path, _raw_output())
    argv.extend(
        [
            "--template4-orders",
            str(tmp_path / "canonical.txt"),
            "--unsafe-debug-output-dir",
            str(tmp_path / "debug"),
        ]
    )
    assert main(argv) == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "unsafe_parse_only_stdout_only_legacy_output_flags_forbidden" in captured.err
    assert {path.relative_to(tmp_path) for path in tmp_path.rglob("*")} == {
        raw.relative_to(tmp_path)
    }


@pytest.mark.parametrize(
    "buy_body",
    (
        "ticker=QQQ | step_name=L1 | shares=1 | limit_price=10.00",
        "ticker=QQQ | step_name=L1 | shares=1 | limit_price=10.00 | order_intent",
        "ticker=QQQ | step_name=L1 | shares=1 | limit_price=10.00 | order_intent=",
        "ticker=QQQ | step_name=L1 | shares=1 | limit_price=10.00 | order_intent=   ",
    ),
    ids=("missing", "malformed-key", "empty", "whitespace-only"),
)
def test_unsafe_blank_intent_fails_without_success_envelope_or_files(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    buy_body: str,
) -> None:
    argv, raw = _unsafe_argv(tmp_path, _raw_output(buy_body))
    assert main(argv) == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "unsafe_parse_only_validation_failed" in captured.err
    assert "Traceback" not in captured.err
    assert {path.relative_to(tmp_path) for path in tmp_path.rglob("*")} == {
        raw.relative_to(tmp_path)
    }


def test_unsafe_blank_failure_preserves_prior_canonical_files(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    canonical = tmp_path / "artifacts" / "current" / "step4_order_compiler"
    canonical.mkdir(parents=True)
    for filename in ("template4_orders.txt", "order_state_export.txt", "exec_summary.txt"):
        (canonical / filename).write_text(f"PRIOR {filename}\n", encoding="utf-8")
    body = "ticker=QQQ | shares=1 | limit_price=10.00 | order_intent="
    argv, _ = _unsafe_argv(tmp_path, _raw_output(body))
    before = _tree_snapshot(tmp_path)
    assert main(argv) == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "unsafe_parse_only_validation_failed" in captured.err
    assert _tree_snapshot(tmp_path) == before


@pytest.mark.parametrize(
    "intent",
    ("NEW_ORDER", "new_order", "  NEW_ORDER  ", "BUY", "REPLACE_EXISTING"),
)
def test_unsafe_nonblank_compatibility_remains_unchanged(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    intent: str,
) -> None:
    body = f"ticker=QQQ | shares=1 | limit_price=10.00 | order_intent={intent}"
    argv, _ = _unsafe_argv(tmp_path, _raw_output(body))
    assert main(argv) == 0
    assert f"order_intent={intent.rstrip()}" in json.loads(capsys.readouterr().out)[
        "template4_orders_text"
    ]


def test_unsafe_malformed_input_fails_without_stdout_or_files(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    argv, raw = _unsafe_argv(tmp_path, "not a Step 4 response")
    assert main(argv) == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "unsafe_parse_only_input_invalid" in captured.err
    assert "Traceback" not in captured.err
    assert {path.relative_to(tmp_path) for path in tmp_path.rglob("*")} == {
        raw.relative_to(tmp_path)
    }


def test_unsafe_invalid_input_path_is_code_owned_and_path_free(
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert main(["--raw-output", "bad\x00input", "--unsafe-parse-only"]) == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "unsafe_parse_only_input_read_failed" in captured.err
    assert "Traceback" not in captured.err
    assert "embedded null" not in captured.err


def test_stdout_envelope_is_not_a_canonical_step4_artifact(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    argv, raw = _unsafe_argv(tmp_path, _raw_output())
    assert main(argv) == 0
    stdout = capsys.readouterr().out
    redirected = tmp_path / "unrelated-debug.json"
    redirected.write_text(stdout, encoding="utf-8")
    with pytest.raises(Step4ExtractionError):
        parse_step4_output_text(stdout)
    assert json.loads(redirected.read_text(encoding="utf-8"))["canonical_artifact"] is False
    assert not (tmp_path / "artifacts" / "current").exists()
    assert raw.is_file()
