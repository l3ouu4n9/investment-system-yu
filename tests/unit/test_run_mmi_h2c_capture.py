from __future__ import annotations

from io import BytesIO, StringIO
from pathlib import Path
import sys

import pytest

from investment_orchestrator.cli import run_mmi_h2c_capture as cli
from investment_orchestrator.offline.mmi_h2c_manual_capture_session import (
    H2cManualCaptureError,
    H2cManualCaptureErrorCode,
    H2cManualCaptureFailureClass,
    H2cManualCaptureResult,
)


class _Input:
    def __init__(self, value: bytes) -> None:
        self.buffer = BytesIO(value)


def _argv(tmp_path: Path) -> list[str]:
    return [
        "--strategy-settings-expected-sha256",
        "a" * 64,
        "--portfolio-snapshot-expected-sha256",
        "b" * 64,
        "--h1-prompt-output-path",
        str(tmp_path / "h1-prompt"),
        "--legacy-prompt-output-path",
        str(tmp_path / "legacy-prompt"),
        "--h1-response-path",
        str(tmp_path / "h1-response"),
        "--legacy-response-path",
        str(tmp_path / "legacy-response"),
        "--comparison-report-output-path",
        str(tmp_path / "h2"),
        "--receipt-output-path",
        str(tmp_path / "receipt"),
    ]


def test_success_stdout_is_exact_and_all_paths_are_explicit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    observed: dict[str, object] = {}

    def run(**kwargs: object) -> H2cManualCaptureResult:
        observed.update(kwargs)
        return H2cManualCaptureResult(
            comparison_report_identity_sha256="c" * 64,
            receipt_identity_sha256="d" * 64,
        )

    monkeypatch.setattr(cli, "run_h2c_manual_capture", run)
    assert cli.main(_argv(tmp_path)) == 0
    captured = capsys.readouterr()
    assert captured.out == (
        f"comparison_report_identity_sha256={'c' * 64}\n"
        f"receipt_identity_sha256={'d' * 64}\n"
    )
    assert captured.err == ""
    assert {
        key
        for key, value in observed.items()
        if key.endswith("_path") and isinstance(value, Path)
    } == {
        "h1_prompt_output_path",
        "legacy_prompt_output_path",
        "h1_response_path",
        "legacy_response_path",
        "comparison_report_output_path",
        "receipt_output_path",
    }


def test_controlled_failure_is_exit_three_and_hides_owner_reasons(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    def run(**_kwargs: object) -> H2cManualCaptureResult:
        raise H2cManualCaptureError(
            code=H2cManualCaptureErrorCode.H2C_LIVE_CHAIN_INVALID,
            failure_class=H2cManualCaptureFailureClass.VALIDATOR_SCHEMA,
            owner_reason_codes=("PRIVATE_OWNER_REASON",),
        )

    monkeypatch.setattr(cli, "run_h2c_manual_capture", run)
    assert cli.main(_argv(tmp_path)) == 3
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == "H2C_CAPTURE_FAILED H2C_LIVE_CHAIN_INVALID\n"
    assert "PRIVATE_OWNER_REASON" not in captured.err


def test_unexpected_exception_is_not_relabelled(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def run(**_kwargs: object) -> H2cManualCaptureResult:
        raise RuntimeError("true bug")

    monkeypatch.setattr(cli, "run_h2c_manual_capture", run)
    with pytest.raises(RuntimeError, match="^true bug$"):
        cli.main(_argv(tmp_path))


@pytest.mark.parametrize(
    "argv",
    (
        [],
        ["--unknown-option"],
        ["--h1-prompt-output-path"],
    ),
)
def test_standard_argparse_usage_failures_exit_two(
    argv: list[str],
) -> None:
    with pytest.raises(SystemExit) as captured:
        cli.main(argv)
    assert captured.value.code == 2


def test_standard_argparse_help_exits_zero() -> None:
    with pytest.raises(SystemExit) as captured:
        cli.main(["--help"])
    assert captured.value.code == 0


def test_exact_control_record_is_one_20_byte_read(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_input = _Input(b"H2C_RESPONSES_READY\ntrailing")
    stderr = StringIO()
    monkeypatch.setattr(sys, "stdin", fake_input)
    monkeypatch.setattr(sys, "stderr", stderr)
    assert cli._StdinH2cOperatorHandoff().await_response_files_ready() is None
    assert fake_input.buffer.tell() == 20
    assert stderr.getvalue() == cli._INSTRUCTION


@pytest.mark.parametrize(
    "record",
    (
        b"",
        b"H2C_RESPONSES_READY",
        b"H2C_RESPONSES_READY\r\n",
        b"h2c_responses_ready\n",
        b" H2C_RESPONSES_READY\n",
        b"H2C_RESPONSES_READY \n",
    ),
)
def test_any_nonexact_control_record_fails_without_retry(
    record: bytes,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_input = _Input(record)
    monkeypatch.setattr(sys, "stdin", fake_input)
    monkeypatch.setattr(sys, "stderr", StringIO())
    with pytest.raises(H2cManualCaptureError) as captured:
        cli._StdinH2cOperatorHandoff().await_response_files_ready()
    assert captured.value.code is (
        H2cManualCaptureErrorCode.H2C_OPERATOR_CONTROL_INVALID
    )
    assert fake_input.buffer.tell() == min(len(record), 21)


def test_cli_source_has_no_provider_or_background_capability() -> None:
    source = Path(cli.__file__).read_text(encoding="utf-8")
    assert "requests" not in source
    assert "socket" not in source
    assert "subprocess" not in source
    assert "sleep(" not in source
    assert "poll(" not in source
    assert "watch" not in source
    assert "except Exception" not in source
