"""Tests for V1 BUY review CLI."""

import argparse
from pathlib import Path
from unittest import mock

import pytest

from investment_orchestrator.cli.run_v1_buy_review import build_parser, main
from investment_orchestrator.workflow.p8_v1_review_order_publication import (
    V1P8APublicationError,
    V1ReviewOrderPublicationResult,
)


@pytest.fixture
def parser() -> argparse.ArgumentParser:
    return build_parser()


def test_no_investment_cli_inputs(parser: argparse.ArgumentParser):
    """B. Prove parser does not accept operator-supplied investment facts."""
    # SystemExit is expected when argparse fails to parse arguments
    with pytest.raises(SystemExit) as exc_info:
        parser.parse_args(["publish", "--quantity", "100"])
    assert exc_info.value.code == 2

    # Verify no positional args allowed after 'publish'
    with pytest.raises(SystemExit) as exc_info:
        parser.parse_args(["publish", "TLT"])
    assert exc_info.value.code == 2


@mock.patch("investment_orchestrator.cli.run_v1_buy_review.publish_h1_v1_review_order")
def test_positive_invocation_behavior(mock_publish, capsys, monkeypatch):
    """A. Exact positive command invokes P8A exactly once and prints path."""
    expected_path = Path("/mocked/path/v1_review_orders/abc.json")
    mock_publish.return_value = V1ReviewOrderPublicationResult(
        terminal_outcome="POSTCOMPILE_CANDIDATE_VALID",
        artifact_identity_sha256="abc",
        immutable_path=expected_path,
        existed_idempotently=False,
        selected_ticker="TLT",
        total_candidate_notional="1000",
    )

    monkeypatch.setattr("sys.argv", ["run_v1_buy_review.py", "publish"])

    exit_code = main()

    assert exit_code == 0
    mock_publish.assert_called_once_with()

    captured = capsys.readouterr()
    assert captured.out.strip() == str(expected_path)
    assert captured.err == ""


@mock.patch("investment_orchestrator.cli.run_v1_buy_review.publish_h1_v1_review_order")
def test_subject_mismatch_behavior(mock_publish, capsys, monkeypatch):
    """C. Subject mismatch (e.g. HOLD/NO_TRADE) prints to stderr and exits 1."""
    mock_publish.side_effect = V1P8APublicationError("V1_P8A_PUBLICATION_SUBJECT_MISMATCH")

    monkeypatch.setattr("sys.argv", ["run_v1_buy_review.py", "publish"])

    exit_code = main()

    assert exit_code == 1
    mock_publish.assert_called_once_with()

    captured = capsys.readouterr()
    assert "V1_P8A_PUBLICATION_SUBJECT_MISMATCH" in captured.err
    assert captured.out == ""


@mock.patch("investment_orchestrator.cli.run_v1_buy_review.publish_h1_v1_review_order")
def test_publication_integrity_failure(mock_publish, capsys, monkeypatch):
    """D. Publication failure prints to stderr and exits 1."""
    mock_publish.side_effect = V1P8APublicationError("V1_P8A_EXISTING_IDENTITY_MISMATCH")

    monkeypatch.setattr("sys.argv", ["run_v1_buy_review.py", "publish"])

    exit_code = main()

    assert exit_code == 1
    mock_publish.assert_called_once_with()

    captured = capsys.readouterr()
    assert "V1_P8A_EXISTING_IDENTITY_MISMATCH" in captured.err


@mock.patch("investment_orchestrator.cli.run_v1_buy_review.publish_h1_v1_review_order")
def test_unexpected_ordinary_exception(mock_publish, capsys, monkeypatch):
    """E. Unexpected exception fails closed."""
    mock_publish.side_effect = RuntimeError("Something completely broken")

    monkeypatch.setattr("sys.argv", ["run_v1_buy_review.py", "publish"])

    exit_code = main()

    assert exit_code == 1

    captured = capsys.readouterr()
    assert "Something completely broken" in captured.err


def test_no_direct_p6_authority():
    """F. run_v1_buy_review.py does not import P6/Step4/daily execution."""
    from investment_orchestrator.cli import run_v1_buy_review

    # Simple static oracle checking imports inside the module's globals
    assert "evaluate_h1_v1_postcompile_final_safety" not in run_v1_buy_review.__dict__
    assert "render_step4_prompt" not in run_v1_buy_review.__dict__
    assert "run_weekly" not in run_v1_buy_review.__dict__
