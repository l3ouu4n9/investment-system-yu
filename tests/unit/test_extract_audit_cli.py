"""Tests for the disabled standalone Step 3 extractor CLI."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from investment_orchestrator.parsers import extract_audit_and_audited_packet as step3_parser


def _valid_raw_output() -> str:
    packet = {
        "audit_passed": True,
        "order_compiler_ready": True,
        "final_buy_side_delta_table": [],
        "final_sell_side_delta_table": [],
        "final_execution_plans": [],
        "final_sell_execution_plans": [],
    }
    return (
        "TEMPLATE3_AUDIT_START\nAUDIT BODY\nTEMPLATE3_AUDIT_END\n"
        "AUDITED_DECISION_PACKET_START\n"
        + json.dumps(packet)
        + "\nAUDITED_DECISION_PACKET_END\n"
    )


def test_standalone_cli_refuses_before_parsing_or_writing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    raw_output = tmp_path / "raw_output.txt"
    raw_output.write_text("must not be read", encoding="utf-8")
    template3_audit = tmp_path / "template3_audit.txt"
    template2_patch = tmp_path / "template2_patch.txt"
    audited_decision_packet = tmp_path / "audited_decision_packet.json"

    def forbidden(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("standalone CLI invoked the content parser")

    monkeypatch.setattr(step3_parser, "extract_audit_and_audited_packet", forbidden)

    result = step3_parser.main(
        [
            "--raw-output",
            str(raw_output),
            "--template3-audit",
            str(template3_audit),
            "--template2-patch",
            str(template2_patch),
            "--audited-decision-packet",
            str(audited_decision_packet),
        ]
    )

    captured = capsys.readouterr()
    assert result == 2
    assert captured.out == ""
    assert "refusing to run" in captured.err
    assert "run_step3 parse" in captured.err
    assert raw_output.read_text(encoding="utf-8") == "must not be read"
    assert not template3_audit.exists()
    assert not template2_patch.exists()
    assert not audited_decision_packet.exists()


def test_standalone_cli_refuses_with_canonical_workflow_paths(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Even pointed at real canonical Step 3 artifacts, nothing is read or written."""
    step3_dir = tmp_path / "artifacts" / "current" / "step3_audit_engine"
    step3_dir.mkdir(parents=True)
    raw_output = step3_dir / "raw_output.txt"
    raw_output.write_text(_valid_raw_output(), encoding="utf-8")
    before = {path.name: path.read_bytes() for path in sorted(step3_dir.iterdir())}

    result = step3_parser.main(
        [
            "--raw-output",
            str(raw_output),
            "--template3-audit",
            str(step3_dir / "template3_audit.txt"),
            "--template2-patch",
            str(step3_dir / "template2_patch.txt"),
            "--audited-decision-packet",
            str(step3_dir / "audited_decision_packet.json"),
        ]
    )

    assert result == 2
    assert capsys.readouterr().out == ""
    assert {path.name: path.read_bytes() for path in sorted(step3_dir.iterdir())} == before


def test_reusable_extraction_function_remains_content_only(tmp_path: Path) -> None:
    """The gated workflow's parser is unchanged and takes no admission opinion."""
    raw_output = tmp_path / "raw_output.txt"
    raw_output.write_text(_valid_raw_output(), encoding="utf-8")
    template3_audit = tmp_path / "template3_audit.txt"
    template2_patch = tmp_path / "template2_patch.txt"
    audited_decision_packet = tmp_path / "audited_decision_packet.json"

    audit_text, patch_text, packet = step3_parser.extract_audit_and_audited_packet(
        raw_output_path=raw_output,
        template3_audit_path=template3_audit,
        template2_patch_path=template2_patch,
        audited_decision_packet_path=audited_decision_packet,
    )

    assert audit_text.strip() == "AUDIT BODY"
    assert patch_text == ""
    assert packet["audit_passed"] is True
    assert template3_audit.read_text(encoding="utf-8") == "AUDIT BODY\n"
    assert json.loads(audited_decision_packet.read_text(encoding="utf-8"))["order_compiler_ready"]
