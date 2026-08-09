from __future__ import annotations

import ast
import hashlib
from pathlib import Path

import pytest

from investment_orchestrator.cli import (
    run_mmi_h2c_consume_archived as cli,
)
from investment_orchestrator.offline.mmi_h2c_consume_persisted_case_v1 import (
    H2cConsumeError,
    H2cConsumeErrorCode,
    H2cConsumeFailureClass,
    H2cConsumeResult,
)
from investment_orchestrator.offline.mmi_h2c_prepare_persisted_case_v1 import (
    prepare_h2c_persisted_case,
)

from tests.unit.test_mmi_h2c_consume_persisted_case_v1 import (
    _capture_at,
    _response_handoff,
)
from tests.unit.test_mmi_h2c_prepare_persisted_case_v1 import (
    _portfolio_bytes,
    _settings_bytes,
)


def _argv(*, case_root: Path, identity: str) -> list[str]:
    return [
        "--case-root",
        str(case_root),
        "--expected-prepared-case-identity-sha256",
        identity,
    ]


def _result(*, workflow_status: str = "COMPLETED") -> H2cConsumeResult:
    return H2cConsumeResult(
        workflow_status=workflow_status,
        case_evidence_bundle_identity_sha256="a" * 64,
        comparison_report_identity_sha256="b" * 64,
        receipt_identity_sha256="c" * 64,
    )


def test_parser_has_exactly_the_two_archived_owner_arguments() -> None:
    actions = cli._parser()._actions
    required = tuple(action for action in actions if action.required)
    assert tuple(action.dest for action in required) == (
        "case_root",
        "expected_prepared_case_identity_sha256",
    )
    assert required[0].type is Path
    assert {
        action.dest for action in actions if action.dest != "help"
    } == {
        "case_root",
        "expected_prepared_case_identity_sha256",
    }


def test_success_delegates_once_with_exact_arguments_and_prints_stable_facts(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    case_root = tmp_path / "case"
    identity = "identity-passed-unchanged"
    expected = _result()
    calls: list[dict[str, object]] = []

    def stub(**kwargs: object) -> H2cConsumeResult:
        calls.append(kwargs)
        return expected

    monkeypatch.setattr(
        cli, "consume_h2c_persisted_case_from_archives", stub
    )

    exit_code = cli.main(_argv(case_root=case_root, identity=identity))

    assert exit_code == 0
    assert calls == [
        {
            "case_root": case_root,
            "expected_prepared_case_identity_sha256": identity,
        }
    ]
    captured = capsys.readouterr()
    assert captured.err == ""
    facts = dict(
        line.split("=", 1) for line in captured.out.splitlines()
    )
    assert facts == {
        "workflow_status": "COMPLETED",
        "prepared_case_identity_sha256": identity,
        "case_evidence_bundle_identity_sha256": "a" * 64,
        "comparison_report_identity_sha256": "b" * 64,
        "receipt_identity_sha256": "c" * 64,
    }


def test_noncompleted_owner_result_is_an_unhandled_internal_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        cli,
        "consume_h2c_persisted_case_from_archives",
        lambda **_kwargs: _result(workflow_status="NOT_COMPLETED"),
    )

    with pytest.raises(
        RuntimeError, match="^archived H2c consume did not complete$"
    ):
        cli.main(_argv(case_root=tmp_path / "case", identity="a" * 64))


def test_controlled_collision_returns_three_and_is_not_retried(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    calls = 0

    def stub(**_kwargs: object) -> H2cConsumeResult:
        nonlocal calls
        calls += 1
        raise H2cConsumeError(
            code=H2cConsumeErrorCode.H2C_CONSUME_COLLISION,
            failure_class=H2cConsumeFailureClass.PERSISTENCE,
            owner_reason_codes=("PRIVATE_OWNER_REASON",),
        )

    monkeypatch.setattr(
        cli, "consume_h2c_persisted_case_from_archives", stub
    )

    exit_code = cli.main(
        _argv(case_root=tmp_path / "case", identity="a" * 64)
    )

    assert exit_code == 3
    assert calls == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == (
        "H2C_CONSUME_FAILED "
        "code=H2C_CONSUME_COLLISION "
        "failure_class=PERSISTENCE\n"
    )
    assert "PRIVATE_OWNER_REASON" not in captured.err


def test_unknown_internal_exception_propagates_same_instance(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    original = RuntimeError("true internal defect")

    def stub(**_kwargs: object) -> H2cConsumeResult:
        raise original

    monkeypatch.setattr(
        cli, "consume_h2c_persisted_case_from_archives", stub
    )

    with pytest.raises(RuntimeError) as raised:
        cli.main(_argv(case_root=tmp_path / "case", identity="a" * 64))
    assert raised.value is original


def test_missing_required_argument_exits_two() -> None:
    with pytest.raises(SystemExit) as raised:
        cli.main(["--case-root", "/tmp/case"])
    assert raised.value.code == 2


def test_cli_import_and_call_surface_is_archived_owner_only() -> None:
    source = Path(cli.__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)
    imported_modules = {
        node.module
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module
    } | {
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    }
    assert imported_modules == {
        "__future__",
        "argparse",
        "collections.abc",
        "pathlib",
        "sys",
        (
            "investment_orchestrator.offline."
            "mmi_h2c_consume_persisted_case_v1"
        ),
    }
    owner_imports = {
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
        and node.module
        == (
            "investment_orchestrator.offline."
            "mmi_h2c_consume_persisted_case_v1"
        )
        for alias in node.names
    }
    assert owner_imports == {
        "H2cConsumeError",
        "consume_h2c_persisted_case_from_archives",
    }
    archived_calls = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "consume_h2c_persisted_case_from_archives"
    ]
    assert len(archived_calls) == 1


def test_cli_performs_no_response_or_artifact_file_io() -> None:
    source = Path(cli.__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)
    call_names = {
        node.func.id
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    }
    call_attributes = {
        node.func.attr
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
    }
    assert "open" not in call_names
    assert not call_attributes & {
        "open",
        "read_bytes",
        "read_text",
        "write_bytes",
        "write_text",
    }


def test_real_cli_completes_one_genuine_prepared_case(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    from investment_orchestrator.offline import (
        mmi_h2c_prepare_persisted_case_v1 as prepare_engine,
    )

    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        prepare_engine, "capture_current_mmi_source", _capture_at(tmp_path)
    )

    settings = _settings_bytes()
    portfolio = _portfolio_bytes()
    current = tmp_path / "inputs" / "current"
    current.mkdir(parents=True)
    (current / "strategy_settings.yaml").write_bytes(settings)
    (current / "portfolio_snapshot.txt").write_bytes(portfolio)

    case_root = tmp_path / "case"
    prepared = prepare_h2c_persisted_case(
        strategy_settings_expected_sha256=(
            hashlib.sha256(settings).hexdigest()
        ),
        portfolio_snapshot_expected_sha256=(
            hashlib.sha256(portfolio).hexdigest()
        ),
        case_root=case_root,
    )
    h1_bytes, legacy_bytes = _response_handoff(
        case_root / "prompts" / "h1_prompt.txt"
    )
    (case_root / "responses" / "h1_response.raw").write_bytes(h1_bytes)
    (case_root / "responses" / "legacy_response.raw").write_bytes(
        legacy_bytes
    )

    exit_code = cli.main(
        _argv(
            case_root=case_root,
            identity=prepared.prepared_case_identity_sha256,
        )
    )

    assert exit_code == 0
    captured = capsys.readouterr()
    assert "workflow_status=COMPLETED\n" in captured.out
    assert captured.err == ""
    for relative_path in (
        "artifacts/case_evidence_bundle.json",
        "artifacts/comparison_report.json",
        "artifacts/receipt.json",
    ):
        assert (case_root / relative_path).is_file(), relative_path
