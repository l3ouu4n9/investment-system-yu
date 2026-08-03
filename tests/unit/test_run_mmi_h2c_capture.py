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
    H2cOperatorHandoff,
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
        "--case-evidence-bundle-output-path",
        str(tmp_path / "case-bundle"),
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
        "case_evidence_bundle_output_path",
        "comparison_report_output_path",
        "receipt_output_path",
    }


def test_all_nine_parsed_values_reach_the_correct_session_keyword(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    settings_sha = "5" * 64
    portfolio_sha = "6" * 64
    h1_prompt_output_path = tmp_path / "role-h1-prompt-output"
    legacy_prompt_output_path = tmp_path / "role-legacy-prompt-output"
    h1_response_path = tmp_path / "role-h1-response-input"
    legacy_response_path = tmp_path / "role-legacy-response-input"
    case_evidence_bundle_output_path = (
        tmp_path / "role-case-evidence-bundle-output"
    )
    comparison_report_output_path = (
        tmp_path / "role-comparison-report-output"
    )
    receipt_output_path = tmp_path / "role-receipt-output"

    captured: dict[str, object] = {}

    def run(**kwargs: object) -> H2cManualCaptureResult:
        captured.update(kwargs)
        return H2cManualCaptureResult(
            comparison_report_identity_sha256="7" * 64,
            receipt_identity_sha256="8" * 64,
        )

    monkeypatch.setattr(cli, "run_h2c_manual_capture", run)
    exit_code = cli.main(
        [
            "--strategy-settings-expected-sha256",
            settings_sha,
            "--portfolio-snapshot-expected-sha256",
            portfolio_sha,
            "--h1-prompt-output-path",
            str(h1_prompt_output_path),
            "--legacy-prompt-output-path",
            str(legacy_prompt_output_path),
            "--h1-response-path",
            str(h1_response_path),
            "--legacy-response-path",
            str(legacy_response_path),
            "--case-evidence-bundle-output-path",
            str(case_evidence_bundle_output_path),
            "--comparison-report-output-path",
            str(comparison_report_output_path),
            "--receipt-output-path",
            str(receipt_output_path),
        ]
    )

    assert exit_code == 0
    assert set(captured) == {
        "strategy_settings_expected_sha256",
        "portfolio_snapshot_expected_sha256",
        "h1_prompt_output_path",
        "legacy_prompt_output_path",
        "h1_response_path",
        "legacy_response_path",
        "case_evidence_bundle_output_path",
        "comparison_report_output_path",
        "receipt_output_path",
        "operator_handoff",
    }
    assert captured["strategy_settings_expected_sha256"] == settings_sha
    assert captured["portfolio_snapshot_expected_sha256"] == portfolio_sha
    assert captured["h1_prompt_output_path"] == h1_prompt_output_path
    assert (
        captured["legacy_prompt_output_path"] == legacy_prompt_output_path
    )
    assert captured["h1_response_path"] == h1_response_path
    assert captured["legacy_response_path"] == legacy_response_path
    assert (
        captured["case_evidence_bundle_output_path"]
        == case_evidence_bundle_output_path
    )
    assert (
        captured["comparison_report_output_path"]
        == comparison_report_output_path
    )
    assert captured["receipt_output_path"] == receipt_output_path

    handoff = captured["operator_handoff"]
    assert isinstance(handoff, H2cOperatorHandoff)
    assert not isinstance(handoff, (str, bytes, Path))
    assert not any(isinstance(value, bytes) for value in captured.values())

    captured_stdio = capsys.readouterr()
    assert captured_stdio.out == (
        f"comparison_report_identity_sha256={'7' * 64}\n"
        f"receipt_identity_sha256={'8' * 64}\n"
    )
    assert captured_stdio.err == ""


def test_new_bundle_option_is_required_by_argparse(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        cli,
        "run_h2c_manual_capture",
        lambda **_kwargs: pytest.fail("session must not begin"),
    )
    argv = _argv(tmp_path)
    option_index = argv.index("--case-evidence-bundle-output-path")
    del argv[option_index : option_index + 2]
    with pytest.raises(SystemExit) as captured:
        cli.main(argv)
    assert captured.value.code == 2


def test_parser_has_exactly_two_sha_and_seven_required_path_options() -> None:
    required = tuple(
        action
        for action in cli._parser()._actions
        if action.required
    )
    assert tuple(action.dest for action in required) == (
        "strategy_settings_expected_sha256",
        "portfolio_snapshot_expected_sha256",
        "h1_prompt_output_path",
        "legacy_prompt_output_path",
        "h1_response_path",
        "legacy_response_path",
        "case_evidence_bundle_output_path",
        "comparison_report_output_path",
        "receipt_output_path",
    )
    assert all(action.type is Path for action in required[2:])


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


@pytest.mark.parametrize(
    ("code", "failure_class"),
    (
        (
            H2cManualCaptureErrorCode.H2C_CASE_EVIDENCE_BUNDLE_VALIDATION_INVALID,
            H2cManualCaptureFailureClass.VALIDATOR_SCHEMA,
        ),
        (
            H2cManualCaptureErrorCode.H2C_CASE_EVIDENCE_BUNDLE_PERSISTENCE_FAILED,
            H2cManualCaptureFailureClass.PERSISTENCE,
        ),
    ),
)
def test_bundle_controlled_failures_use_existing_exit_three_format(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    code: H2cManualCaptureErrorCode,
    failure_class: H2cManualCaptureFailureClass,
) -> None:
    def run(**_kwargs: object) -> H2cManualCaptureResult:
        raise H2cManualCaptureError(
            code=code,
            failure_class=failure_class,
            owner_reason_codes=("PRIVATE",),
        )

    monkeypatch.setattr(cli, "run_h2c_manual_capture", run)
    assert cli.main(_argv(tmp_path)) == 3
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == f"H2C_CAPTURE_FAILED {code.value}\n"
    assert "PRIVATE" not in captured.err


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
