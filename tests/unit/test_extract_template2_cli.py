"""Tests for the disabled standalone Step 2 extractor CLI."""

from __future__ import annotations

from pathlib import Path

import pytest

from investment_orchestrator.parsers import extract_template2_and_decision_packet as step2_parser


def test_standalone_cli_refuses_before_parsing_or_writing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    raw_output = tmp_path / "raw_output.txt"
    raw_output.write_text("must not be read", encoding="utf-8")
    template_output = tmp_path / "template2_output.txt"
    decision_packet = tmp_path / "decision_packet.json"

    def forbidden(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("standalone CLI invoked the content parser")

    monkeypatch.setattr(step2_parser, "extract_template2_and_decision_packet", forbidden)

    result = step2_parser.main(
        [
            "--raw-output",
            str(raw_output),
            "--template2-output",
            str(template_output),
            "--decision-packet",
            str(decision_packet),
        ]
    )

    captured = capsys.readouterr()
    assert result == 2
    assert captured.out == ""
    assert "refusing to run" in captured.err
    assert "run_step2 parse" in captured.err
    assert raw_output.read_text(encoding="utf-8") == "must not be read"
    assert not template_output.exists()
    assert not decision_packet.exists()
