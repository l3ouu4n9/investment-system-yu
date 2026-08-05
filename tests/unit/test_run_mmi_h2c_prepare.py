from __future__ import annotations

import ast
import hashlib
import json
from pathlib import Path

import pytest

import _mmi_hermetic_source_checkout as hermetic
from investment_orchestrator.cli import run_mmi_h2c_prepare as cli
from investment_orchestrator.mmi import source_capture
from investment_orchestrator.mmi.contracts import MmiSourceRole
from investment_orchestrator.offline import (
    mmi_h2c_prepare_persisted_case_v1 as engine,
)
from investment_orchestrator.offline.mmi_h2c_prepare_persisted_case_v1 import (
    H2cPrepareError,
    H2cPrepareErrorCode,
    H2cPrepareFailureClass,
    H2cPrepareResult,
)
from investment_orchestrator.offline.mmi_h2c_prepared_case_v1 import (
    validate_mmi_h2c_prepared_case_v1,
)


def _digest(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _argv(*, settings_sha: str, portfolio_sha: str, case_root: Path) -> list[str]:
    return [
        "--strategy-settings-expected-sha256",
        settings_sha,
        "--portfolio-snapshot-expected-sha256",
        portfolio_sha,
        "--case-root",
        str(case_root),
    ]


def _install_hermetic_sources(
    monkeypatch: pytest.MonkeyPatch,
    source_root: Path,
) -> tuple[bytes, bytes]:
    """Redirect the engine's current-source capture to test-owned bytes."""
    settings_raw = hermetic.strategy_settings_bytes()
    portfolio_raw = hermetic.portfolio_snapshot_bytes()
    hermetic.install_source(
        source_root, role=MmiSourceRole.STRATEGY_SETTINGS, raw=settings_raw
    )
    hermetic.install_source(
        source_root, role=MmiSourceRole.PORTFOLIO_SNAPSHOT, raw=portfolio_raw
    )

    def capture(
        role: MmiSourceRole, *, expected_source_sha256: str
    ) -> object:
        return source_capture._capture_mmi_source_at_root(
            source_root,
            role=role,
            expected_source_sha256=expected_source_sha256,
        )

    monkeypatch.setattr(engine, "capture_current_mmi_source", capture)
    return settings_raw, portfolio_raw


# --- positive integration -----------------------------------------------------


def test_prepare_only_success_persists_a_validating_case_with_no_downstream_artifacts(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    source_root = tmp_path / "checkout"
    settings_raw, portfolio_raw = _install_hermetic_sources(
        monkeypatch, source_root
    )
    (tmp_path / "cases").mkdir()
    case_root = tmp_path / "cases" / "case-0001"

    real_prepare = cli.prepare_h2c_persisted_case
    observed: dict[str, object] = {}

    def recording_prepare(**kwargs: object) -> H2cPrepareResult:
        result = real_prepare(**kwargs)
        observed["result"] = result
        return result

    monkeypatch.setattr(cli, "prepare_h2c_persisted_case", recording_prepare)

    exit_code = cli.main(
        _argv(
            settings_sha=_digest(settings_raw),
            portfolio_sha=_digest(portfolio_raw),
            case_root=case_root,
        )
    )

    assert exit_code == 0
    result = observed["result"]
    assert isinstance(result, H2cPrepareResult)

    captured = capsys.readouterr()
    assert captured.out == (
        f"workflow_status={result.workflow_status}\n"
        "prepared_case_identity_sha256="
        f"{result.prepared_case_identity_sha256}\n"
    )
    assert captured.err == ""

    manifest_path = case_root / "prepared" / "prepared_case.json"
    assert manifest_path.is_file()
    prepared_case = json.loads(manifest_path.read_text(encoding="utf-8"))
    validate_mmi_h2c_prepared_case_v1(prepared_case=prepared_case)
    assert (
        prepared_case["prepared_case_identity_sha256"]
        == result.prepared_case_identity_sha256
    )

    for relative in (
        "responses/h1_response.raw",
        "responses/legacy_response.raw",
        "artifacts/case_evidence_bundle.json",
        "artifacts/comparison_report.json",
        "artifacts/receipt.json",
    ):
        assert not (case_root / relative).exists(), relative


# --- argument and path behavior ------------------------------------------------


def test_missing_required_argument_exits_two() -> None:
    with pytest.raises(SystemExit) as captured:
        cli.main(
            [
                "--portfolio-snapshot-expected-sha256",
                "b" * 64,
                "--case-root",
                "/tmp/does-not-matter",
            ]
        )
    assert captured.value.code == 2


def test_unknown_option_exits_two() -> None:
    with pytest.raises(SystemExit) as captured:
        cli.main(["--unknown-option"])
    assert captured.value.code == 2


def test_standard_argparse_help_exits_zero() -> None:
    with pytest.raises(SystemExit) as captured:
        cli.main(["--help"])
    assert captured.value.code == 0


def test_parser_has_exactly_the_three_engine_arguments() -> None:
    actions = cli._parser()._actions
    required = tuple(action for action in actions if action.required)
    assert tuple(action.dest for action in required) == (
        "strategy_settings_expected_sha256",
        "portfolio_snapshot_expected_sha256",
        "case_root",
    )
    assert required[2].type is Path
    all_dests = {action.dest for action in actions if action.dest != "help"}
    assert all_dests == {
        "strategy_settings_expected_sha256",
        "portfolio_snapshot_expected_sha256",
        "case_root",
    }


def test_cli_is_repo_root_independent_given_an_absolute_case_root(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    non_repo_cwd = tmp_path / "not-a-repository-checkout"
    non_repo_cwd.mkdir()
    (tmp_path / "cases").mkdir()
    case_root = tmp_path / "cases" / "case-elsewhere"

    observed: dict[str, object] = {}

    def stub(**kwargs: object) -> H2cPrepareResult:
        observed.update(kwargs)
        return H2cPrepareResult(
            workflow_status="AWAITING_OPERATOR_RESPONSES",
            prepared_case_identity_sha256="0" * 64,
        )

    monkeypatch.setattr(cli, "prepare_h2c_persisted_case", stub)
    monkeypatch.chdir(non_repo_cwd)

    exit_code = cli.main(
        _argv(settings_sha="a" * 64, portfolio_sha="b" * 64, case_root=case_root)
    )

    assert exit_code == 0
    assert observed["case_root"] == case_root


# --- fail-closed behavior -------------------------------------------------------


def test_real_engine_argument_invalid_failure_is_fail_closed(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    (tmp_path / "cases").mkdir()
    case_root = tmp_path / "cases" / "case-invalid-args"

    exit_code = cli.main(
        _argv(
            settings_sha="not-a-valid-sha256",
            portfolio_sha="b" * 64,
            case_root=case_root,
        )
    )

    assert exit_code == 3
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == (
        "H2C_PREPARE_FAILED "
        f"{H2cPrepareErrorCode.H2C_PREPARE_ARGUMENT_INVALID.value}\n"
    )
    assert not case_root.exists()


def test_controlled_failure_hides_owner_reason_codes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    def stub(**_kwargs: object) -> H2cPrepareResult:
        raise H2cPrepareError(
            code=H2cPrepareErrorCode.H2C_PREPARE_LIVE_CHAIN_INVALID,
            failure_class=H2cPrepareFailureClass.VALIDATOR_SCHEMA,
            owner_reason_codes=("PRIVATE_OWNER_REASON",),
        )

    monkeypatch.setattr(cli, "prepare_h2c_persisted_case", stub)
    case_root = tmp_path / "case-stub-failure"

    exit_code = cli.main(
        _argv(settings_sha="a" * 64, portfolio_sha="b" * 64, case_root=case_root)
    )

    assert exit_code == 3
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == "H2C_PREPARE_FAILED H2C_PREPARE_LIVE_CHAIN_INVALID\n"
    assert "PRIVATE_OWNER_REASON" not in captured.err


def test_unexpected_exception_is_not_relabelled(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def stub(**_kwargs: object) -> H2cPrepareResult:
        raise RuntimeError("true bug")

    monkeypatch.setattr(cli, "prepare_h2c_persisted_case", stub)
    case_root = tmp_path / "case-true-bug"

    with pytest.raises(RuntimeError, match="^true bug$"):
        cli.main(
            _argv(
                settings_sha="a" * 64, portfolio_sha="b" * 64, case_root=case_root
            )
        )


# --- manual LLM boundary / negative authority proof ----------------------------


def test_cli_source_imports_only_the_existing_phase_a_engine() -> None:
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
        "investment_orchestrator.offline.mmi_h2c_prepare_persisted_case_v1",
    }
    imported_names = {
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
        and node.module
        == "investment_orchestrator.offline.mmi_h2c_prepare_persisted_case_v1"
        for alias in node.names
    }
    assert imported_names == {"H2cPrepareError", "prepare_h2c_persisted_case"}


def test_cli_source_performs_no_file_io_of_its_own() -> None:
    source = Path(cli.__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)
    call_names = {
        node.func.id
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    }
    call_attrs = {
        node.func.attr
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
    }
    assert "open" not in call_names
    assert not call_attrs & {
        "read_bytes",
        "read_text",
        "write_bytes",
        "write_text",
        "open",
    }


def test_cli_source_has_no_provider_submission_or_background_capability() -> None:
    source = Path(cli.__file__).read_text(encoding="utf-8")
    for forbidden in (
        "requests",
        "socket",
        "subprocess",
        "sleep(",
        "poll(",
        "except Exception",
        "credential",
        "api_key",
        "getenv",
        "environ",
        "urllib",
        "http",
    ):
        assert forbidden not in source, forbidden
