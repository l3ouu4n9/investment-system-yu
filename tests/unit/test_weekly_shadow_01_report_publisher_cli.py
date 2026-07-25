"""WS01e explicit operator publication CLI tests.

The command owns exactly one selector -- the operator's raw-response file --
and hands its exact stable bytes to the WS01d public API once.  These tests
drive real production ordering, use an independent byte oracle for every
accepted payload, inject mutations at deterministic protocol boundaries, and
assert closed inventories rather than snapshots.
"""

from __future__ import annotations

import argparse
import ast
import copy
from dataclasses import dataclass
from datetime import date
import inspect
import json
import os
from pathlib import Path
import re
import socket
import stat
import subprocess
import sys
from typing import Any

import pytest
import yaml

from investment_orchestrator.observability import (
    ltetf_target_architecture_gap_report as gap,
)
from investment_orchestrator.observability import weekly_shadow_01_contracts as contracts
from investment_orchestrator.observability import weekly_shadow_01_package_builder as builder
from investment_orchestrator.observability import weekly_shadow_01_report_publisher as publisher
from investment_orchestrator.cli import weekly_shadow_01_report_publisher_cli as cli
from investment_orchestrator.research import replacement_observation as r2f


_CLI_RELATIVE_PATH = (
    "src/investment_orchestrator/cli/"
    "weekly_shadow_01_report_publisher_cli.py"
)
_CLI_MODULE = (
    "investment_orchestrator.cli.weekly_shadow_01_report_publisher_cli"
)
_OBSERVER_CLI_RELATIVE_PATH = (
    "src/investment_orchestrator/cli/"
    "observe_ltetf_target_architecture_gaps.py"
)
_PUBLISHER_MODULE = (
    "investment_orchestrator.observability.weekly_shadow_01_report_publisher"
)
_REPORT_FILENAME = "weekly_shadow_01_analyst_report.json"
_SUMMARY_FILENAME = "weekly_shadow_01_run_summary.json"
_LOCAL_TOKENS = (
    "raw_response_file_not_absolute",
    "raw_response_file_wrong_type",
    "raw_response_file_unstable",
    "raw_response_file_unreadable",
    "raw_response_file_oversized",
)


# --------------------------------------------------------------- fixture setup


def _write(path: Path, value: str | bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if type(value) is bytes:
        path.write_bytes(value)
    else:
        path.write_text(value, encoding="utf-8")


def _anchor(index: int) -> dict[str, Any]:
    return {
        "anchor_id": f"ANCHOR_{index:02d}",
        "anchor_type": "structural_theme",
        "applicable_tickers": ["FIX00"],
        "anchor_date_et": "2026-07-01",
        "valid_from": "2026-07-01",
        "valid_until": "2026-12-31",
        "source_type": "operator",
        "confidence_floor": "medium",
        "summary": f"Evidence summary {index}",
    }


def _setup_repo(root: Path) -> None:
    source_root = Path(__file__).parents[2]
    _write(
        root / "inputs/current/strategy_settings.yaml",
        """as_of: "2026-07-12"
benchmark: "FIX00"
core_universe: [FIX00]
satellite_universe: [FIX01]
user_approved_extended_etf_static_list: [FIX02]
hard_cap_open_orders_budget: 100
target_new_buy_budget_this_run: 10
max_new_tickers_per_week: 0
ticker_role_fallback:
  FIX00: benchmark_carrier_core
  FIX01: sector_alpha_tilt
  FIX02: extended_etf_minority_sleeve
""",
    )
    _write(root / "inputs/current/portfolio_snapshot.txt", "fixture portfolio\n")
    _write(
        root / "inputs/current/research_anchors.yaml",
        yaml.safe_dump(
            {
                "schema_version": "research_anchors_v1",
                "as_of_date": "2026-07-12",
                "is_llm_generated": False,
                "anchors": [_anchor(index) for index in range(16)],
            },
            sort_keys=False,
        ),
    )
    _write(
        root / "inputs/current/research_anchor_approvals.yaml",
        yaml.safe_dump(
            {
                "schema_version": "research_anchor_approvals_v1",
                "is_llm_generated": False,
                "as_of_date": "2026-07-12",
                "approvals": [],
            },
            sort_keys=False,
        ),
    )
    _write(
        root / "prompts/r2f_analyst_memo_content_v2.txt",
        (source_root / "prompts/r2f_analyst_memo_content_v2.txt").read_bytes(),
    )
    for relative in contracts.SCHEMA_FILENAME_BY_VERSION.values():
        _write(root / relative, (source_root / relative).read_bytes())
    contract_path = (
        "src/investment_orchestrator/observability/weekly_shadow_01_contracts.py"
    )
    _write(root / contract_path, (source_root / contract_path).read_bytes())


def _canonical(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


@dataclass(frozen=True)
class _OperatorContext:
    repository_root: Path
    generation_id: str
    raw_response: bytes


@pytest.fixture(scope="module")
def operator_context(
    tmp_path_factory: pytest.TempPathFactory,
) -> _OperatorContext:
    root = tmp_path_factory.mktemp("ws01e-repo")
    _setup_repo(root)
    patch = pytest.MonkeyPatch()
    patch.setattr(r2f, "repo_root", lambda: root)
    patch.setattr(r2f, "_today", lambda: date(2026, 7, 12))
    try:
        generation = r2f.replacement_render()
    finally:
        patch.undo()
    generation_id = generation["generation_id"]
    package_result = builder.build_analyst_input_package(
        generation_id,
        repository_root=root,
    )
    assert package_result.ok is True
    payload = package_result.value.to_dict()
    response = {
        "schema_version": "weekly_shadow_01_analyst_response_v2",
        "stage_version": "weekly_shadow_01_stage_a_v1",
        "run_id": payload["run_id"],
        "input_package_identity_sha256": payload[
            "input_package_identity_sha256"
        ],
        "prompt_template_identity_sha256": payload[
            "prompt_template_identity_sha256"
        ],
        "source_generation_id": payload["source_generation_id"],
        "source_artifact_bindings": copy.deepcopy(
            payload["source_artifact_bindings"]
        ),
        "evidence_record_bindings": [
            {
                "evidence_record_id": record["evidence_record_id"],
                "evidence_record_identity_sha256": record[
                    "evidence_record_identity_sha256"
                ],
            }
            for record in payload["evidence_records"]
        ],
        "analyst_conclusion": "OBSERVATIONS_AVAILABLE",
        "analyst_confidence": "MEDIUM",
        "analytical_sections": {
            "observations": [
                {
                    "entry_id": "observation-01",
                    "statement": (
                        "The supplied evidence supports a bounded observation."
                    ),
                    "evidence_record_ids": [
                        payload["evidence_records"][0]["evidence_record_id"]
                    ],
                }
            ],
            "risks_and_uncertainties": [],
            "missing_evidence_notes": [],
        },
        "analyst_limitation_codes": [],
        "negative_authority": copy.deepcopy(payload["negative_authority"]),
    }
    return _OperatorContext(root, generation_id, _canonical(response))


# ------------------------------------------------------------------- utilities


def _output_root(tmp_path: Path) -> Path:
    output = tmp_path / "output"
    output.mkdir(mode=0o700)
    return output


def _response_file(tmp_path: Path, payload: bytes) -> Path:
    path = tmp_path / "raw-response.json"
    path.write_bytes(payload)
    return path


def _argv(
    context: _OperatorContext,
    *,
    response_file: object,
    output_root: object,
    repository_root: object | None,
    generation_id: object | None = None,
) -> list[str]:
    argv = [
        "publish",
        "--generation-id",
        context.generation_id if generation_id is None else str(generation_id),
        "--raw-response-file",
        str(response_file),
        "--output-root",
        str(output_root),
    ]
    if repository_root is not None:
        argv.extend(["--repository-root", str(repository_root)])
    return argv


def _open_descriptor_count() -> int:
    return len(os.listdir("/proc/self/fd"))


def _run(
    context: _OperatorContext,
    tmp_path: Path,
    *,
    payload: bytes | None = None,
    response_file: object | None = None,
    output_root: object | None = None,
    repository_root: object | None = "default",
    capsys: pytest.CaptureFixture[str] | None = None,
) -> tuple[int, str, str]:
    output = _output_root(tmp_path) if output_root is None else output_root
    selector = (
        _response_file(tmp_path, context.raw_response if payload is None else payload)
        if response_file is None
        else response_file
    )
    root = (
        context.repository_root
        if repository_root == "default"
        else repository_root
    )
    exit_code = cli.main(
        _argv(
            context,
            response_file=selector,
            output_root=output,
            repository_root=root,
        )
    )
    if capsys is None:
        return exit_code, "", ""
    captured = capsys.readouterr()
    return exit_code, captured.out, captured.err


def _install_recorder(
    monkeypatch: pytest.MonkeyPatch,
    *,
    result: object | None = None,
    raises: BaseException | None = None,
) -> list[dict[str, Any]]:
    """Record every WS01d invocation, optionally replacing its outcome."""
    calls: list[dict[str, Any]] = []
    original = publisher.publish_weekly_shadow_report

    def record(generation_id, *, raw_response_bytes, output_root, repository_root):
        calls.append(
            {
                "generation_id": generation_id,
                "raw_response_bytes": raw_response_bytes,
                "output_root": output_root,
                "repository_root": repository_root,
            }
        )
        if raises is not None:
            raise raises
        if result is not None:
            return result
        return original(
            generation_id,
            raw_response_bytes=raw_response_bytes,
            output_root=output_root,
            repository_root=repository_root,
        )

    monkeypatch.setattr(
        cli._report_publisher,
        "publish_weekly_shadow_report",
        record,
    )
    return calls


def _cli_tree() -> ast.Module:
    return ast.parse(Path(cli.__file__).read_text(encoding="utf-8"))


# ------------------------------------------------------------ parser + surface


def test_module_surface_is_exactly_build_parser_and_main() -> None:
    assert cli.__all__ == ("build_parser", "main")
    assert {
        name for name in vars(cli) if not name.startswith("_")
    } == set(cli.__all__)
    parser_signature = inspect.signature(cli.build_parser)
    assert tuple(parser_signature.parameters) == ()
    main_signature = inspect.signature(cli.main)
    assert tuple(main_signature.parameters) == ("argv",)
    assert main_signature.parameters["argv"].default is None


def test_publish_subcommand_is_required_with_exactly_four_options() -> None:
    parser = cli.build_parser()
    subparser_actions = [
        action
        for action in parser._actions
        if isinstance(action, __import__("argparse")._SubParsersAction)
    ]
    assert len(subparser_actions) == 1
    assert subparser_actions[0].required is True
    assert set(subparser_actions[0].choices) == {"publish"}
    publish = subparser_actions[0].choices["publish"]
    options = {
        option: action
        for action in publish._actions
        for option in action.option_strings
        if option.startswith("--") and option != "--help"
    }
    assert set(options) == {
        "--generation-id",
        "--raw-response-file",
        "--output-root",
        "--repository-root",
    }
    for name in ("--generation-id", "--raw-response-file", "--output-root"):
        assert options[name].required is True
        assert options[name].nargs is None
    assert options["--repository-root"].required is False
    assert options["--repository-root"].default is None
    assert not any(
        action.const is True or action.nargs in ("*", "+")
        for action in publish._actions
    )


@pytest.mark.parametrize(
    "argv",
    (
        [],
        ["publish"],
        ["render"],
        ["publish", "--generation-id", "g"],
        ["publish", "--raw-response-file", "/tmp/x"],
        ["publish", "--generation-id", "g", "--raw-response-file", "/tmp/x"],
        [
            "publish",
            "--generation-id",
            "g",
            "--raw-response-file",
            "/tmp/x",
            "--output-root",
            "/tmp/o",
            "--unknown",
            "1",
        ],
        [
            "publish",
            "--generation-id",
            "g",
            "--raw-response-file",
            "/tmp/x",
            "/tmp/extra",
            "--output-root",
            "/tmp/o",
        ],
        [
            "publish",
            "--generation-id",
            "g",
            "--raw-response-file",
            "/tmp/a",
            "/tmp/b",
            "--output-root",
            "/tmp/o",
        ],
    ),
    ids=(
        "no-subcommand",
        "publish-without-options",
        "unknown-subcommand",
        "only-generation-id",
        "only-response-file",
        "missing-output-root",
        "unknown-flag",
        "extra-positional",
        "two-values-for-one-response-flag",
    ),
)
def test_usage_errors_exit_two(argv: list[str]) -> None:
    with pytest.raises(SystemExit) as raised:
        cli.main(argv)
    assert raised.value.code == 2


_OPERATIONAL_SELECTORS = (
    ("--generation-id", "GENERATION-ONE", "GENERATION-TWO"),
    ("--raw-response-file", "/tmp/response-one.json", "/tmp/response-two.json"),
    ("--output-root", "/tmp/output-one", "/tmp/output-two"),
    ("--repository-root", "/tmp/repository-one", "/tmp/repository-two"),
)
_SELECTOR_IDS = (
    "generation-id",
    "raw-response-file",
    "output-root",
    "repository-root",
)


def _duplicate_selector_argv(
    context: _OperatorContext,
    *,
    flag: str,
    first: str,
    second: str,
    response_file: Path,
    output_root: Path,
) -> list[str]:
    """Build one otherwise-valid invocation with exactly one repeated flag."""
    single = {
        "--generation-id": context.generation_id,
        "--raw-response-file": str(response_file),
        "--output-root": str(output_root),
        "--repository-root": str(context.repository_root),
    }
    argv = ["publish"]
    for name in (
        "--generation-id",
        "--raw-response-file",
        "--output-root",
        "--repository-root",
    ):
        if name == flag:
            argv.extend([name, first, name, second])
        else:
            argv.extend([name, single[name]])
    return argv


def _assert_duplicate_selector_rejected(
    context: _OperatorContext,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    *,
    flag: str,
    first: str,
    second: str,
) -> None:
    """A repeated operational selector must fail before any real work."""
    calls = _install_recorder(monkeypatch)
    reads: list[object] = []

    def forbidden_read(selector: object) -> bytes:
        reads.append(selector)
        raise AssertionError("the response file must never be read")

    monkeypatch.setattr(cli, "_authenticated_response_bytes", forbidden_read)
    output = _output_root(tmp_path)
    response_file = _response_file(tmp_path, context.raw_response)
    argv = _duplicate_selector_argv(
        context,
        flag=flag,
        first=first,
        second=second,
        response_file=response_file,
        output_root=output,
    )
    with pytest.raises(SystemExit) as raised:
        cli.main(argv)
    captured = capsys.readouterr()
    assert raised.value.code == 2
    assert calls == []
    assert reads == []
    assert captured.out == ""
    assert f"argument {flag}" in captured.err
    assert "may be supplied at most once" in captured.err
    # No receipt field may appear, and no operand value may be echoed beyond
    # ordinary argparse usage rendering.
    for leaked in (
        "publication_reused",
        "report_identity_sha256",
        "run_summary_identity_sha256",
        "publication_relative_path",
        "artifact_filenames",
        str(response_file),
        str(output),
        str(context.repository_root),
        context.generation_id,
        first,
        second,
    ):
        assert leaked not in captured.err
    assert list(output.iterdir()) == []
    assert response_file.read_bytes() == context.raw_response


@pytest.mark.parametrize(
    ("flag", "first", "second"),
    _OPERATIONAL_SELECTORS,
    ids=_SELECTOR_IDS,
)
def test_repeated_operational_selector_is_a_usage_error(
    operator_context: _OperatorContext,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    flag: str,
    first: str,
    second: str,
) -> None:
    _assert_duplicate_selector_rejected(
        operator_context,
        tmp_path,
        monkeypatch,
        capsys,
        flag=flag,
        first=first,
        second=second,
    )


@pytest.mark.parametrize(
    ("flag", "first", "second"),
    _OPERATIONAL_SELECTORS,
    ids=_SELECTOR_IDS,
)
def test_repeated_identical_operational_selector_is_a_usage_error(
    operator_context: _OperatorContext,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    flag: str,
    first: str,
    second: str,
) -> None:
    """Identical repeated values are ambiguous operator input, not a no-op."""
    _assert_duplicate_selector_rejected(
        operator_context,
        tmp_path,
        monkeypatch,
        capsys,
        flag=flag,
        first=first,
        second=first,
    )


def test_single_occurrence_enforcement_is_private_and_parser_owned() -> None:
    """Every operational selector uses the one private parser-owned action."""
    assert cli.__all__ == ("build_parser", "main")
    assert {
        name for name in vars(cli) if not name.startswith("_")
    } == set(cli.__all__)
    assert issubclass(cli._SingleOccurrenceAction, argparse.Action)
    parser = cli.build_parser()
    subparser_actions = [
        action
        for action in parser._actions
        if isinstance(action, argparse._SubParsersAction)
    ]
    publish = subparser_actions[0].choices["publish"]
    guarded = {
        option: action
        for action in publish._actions
        for option in action.option_strings
        if option.startswith("--") and option != "--help"
    }
    assert set(guarded) == {
        "--generation-id",
        "--raw-response-file",
        "--output-root",
        "--repository-root",
    }
    for option, action in guarded.items():
        assert type(action) is cli._SingleOccurrenceAction, option
        assert action.nargs is None
        assert action.const is None
    # State is carried on the per-parse namespace, never in module globals.
    first = parser.parse_args(
        [
            "publish",
            "--generation-id",
            "g",
            "--raw-response-file",
            "/tmp/a",
            "--output-root",
            "/tmp/o",
        ]
    )
    assert getattr(first, cli._SUPPLIED_SELECTOR_DESTINATIONS) == {
        "generation_id",
        "raw_response_file",
        "output_root",
    }
    assert first.repository_root is None
    # The same parser instance stays reusable for a second independent parse.
    second = parser.parse_args(
        [
            "publish",
            "--generation-id",
            "h",
            "--raw-response-file",
            "/tmp/b",
            "--output-root",
            "/tmp/p",
            "--repository-root",
            "/tmp/r",
        ]
    )
    assert (second.generation_id, second.raw_response_file) == ("h", "/tmp/b")
    assert (second.output_root, second.repository_root) == ("/tmp/p", "/tmp/r")
    assert cli._SUPPLIED_SELECTOR_DESTINATIONS.startswith("_")
    assert not hasattr(cli, "_supplied_selector_destinations_state")


def test_no_packaging_entry_point_declares_this_cli() -> None:
    root = Path(__file__).parents[2]
    text = (root / "pyproject.toml").read_text(encoding="utf-8")
    assert "[project.scripts]" not in text
    assert "entry-points" not in text
    assert "console_scripts" not in text
    assert gap._scan_production_inventory(root).entry_points == ()


def test_module_execution_convention_matches_repository() -> None:
    tree = _cli_tree()
    guards = [
        node
        for node in tree.body
        if isinstance(node, ast.If)
        and isinstance(node.test, ast.Compare)
        and isinstance(node.test.left, ast.Name)
        and node.test.left.id == "__name__"
    ]
    assert len(guards) == 1
    body = guards[0].body
    assert len(body) == 1
    assert isinstance(body[0], ast.Raise)
    assert ast.unparse(body[0]) == "raise SystemExit(main())"


# ------------------------------------------------------------- manual boundary


def test_production_import_closure_is_stdlib_plus_publisher_only() -> None:
    root = Path(__file__).parents[2]
    tree = _cli_tree()
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            assert node.level == 0
            module = node.module or ""
            imported.add(module)
            imported.update(f"{module}.{alias.name}" for alias in node.names)
    project = {name for name in imported if name.startswith("investment_orchestrator")}
    assert project == {
        "investment_orchestrator.observability",
        f"investment_orchestrator.observability.{_PUBLISHER_MODULE.rsplit('.', 1)[-1]}",
    }
    third_party = {
        name
        for name in imported
        if not name.startswith("investment_orchestrator")
        and name.split(".")[0]
        not in set(sys.stdlib_module_names) | {"__future__"}
    }
    assert third_party == set()
    source = gap._scan_production_inventory(root)
    assert source.dynamic_findings == ()


@pytest.mark.parametrize(
    "forbidden",
    (
        "importlib",
        "__import__",
        "sys.modules",
        "getattr(",
        "setattr(",
        "openai",
        "anthropic",
        "langchain",
        "google.generativeai",
        "cohere",
        "requests",
        "httpx",
        "urllib",
        "http.client",
        "socket",
        "subprocess",
        "environ",
        "getenv",
        "getpass",
        "asyncio",
        "threading",
        "input(",
        "stdin",
        "sleep(",
        "sched.",
        "APIKey",
        "api_key",
        "endpoint",
        "Session(",
    ),
)
def test_cli_production_source_declares_no_forbidden_capability(
    forbidden: str,
) -> None:
    assert forbidden not in Path(cli.__file__).read_text(encoding="utf-8")


def test_cli_ast_has_no_dynamic_import_or_reflective_package_access() -> None:
    tree = _cli_tree()
    called = {
        node.func.attr if isinstance(node.func, ast.Attribute) else node.func.id
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, (ast.Attribute, ast.Name))
    }
    assert not called.intersection(
        {
            "import_module",
            "__import__",
            "getattr",
            "setattr",
            "eval",
            "exec",
            "system",
            "popen",
            "Popen",
            "run",
            "socket",
            "connect",
            "urlopen",
            "sleep",
            "input",
        }
    )
    assert not any(
        isinstance(node, ast.Subscript)
        and isinstance(node.value, ast.Attribute)
        and node.value.attr == "modules"
        for node in ast.walk(tree)
    )
    # Nothing outside the two public operations may be non-private.
    assert {
        node.name
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
        and not node.name.startswith("_")
    } == {"build_parser", "main"}


def test_no_environment_or_config_fallback_changes_behaviour(
    operator_context: _OperatorContext,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    for name in (
        "WS01E_RAW_RESPONSE_FILE",
        "WS01E_OUTPUT_ROOT",
        "WS01E_REPOSITORY_ROOT",
        "WS01E_GENERATION_ID",
    ):
        monkeypatch.setenv(name, "/nonexistent")
    monkeypatch.chdir(tmp_path)
    _write(tmp_path / "ws01e.cfg", "raw_response_file=/nonexistent\n")
    exit_code, out, err = _run(
        operator_context, tmp_path, capsys=capsys
    )
    assert exit_code == 0
    assert out.startswith("publication_reused=false\n")
    assert err == ""


def test_stdin_is_never_read_as_the_response(
    operator_context: _OperatorContext,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    def explode(*_args: object, **_kwargs: object) -> object:
        raise AssertionError("stdin must never be read")

    monkeypatch.setattr(sys.stdin, "read", explode, raising=False)
    monkeypatch.setattr(sys.stdin, "readline", explode, raising=False)
    exit_code, out, _ = _run(operator_context, tmp_path, capsys=capsys)
    assert exit_code == 0
    assert out


def test_relative_response_selector_is_rejected_without_ws01d_call(
    operator_context: _OperatorContext,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    calls = _install_recorder(monkeypatch)
    path = _response_file(tmp_path, operator_context.raw_response)
    monkeypatch.chdir(tmp_path)
    exit_code, out, err = _run(
        operator_context,
        tmp_path,
        response_file=path.name,
        capsys=capsys,
    )
    assert exit_code == 3
    assert err == "raw_response_file_not_absolute\n"
    assert out == ""
    assert calls == []


@pytest.mark.parametrize(
    "selector",
    (
        "relative/raw-response.json",
        "",
        ".",
        "..",
        "raw-response.json",
        "/tmp/",
        "/tmp//raw-response.json",
        "/tmp/./raw-response.json",
        "/tmp/nested/../raw-response.json",
        "/",
    ),
)
def test_forbidden_selector_components_are_rejected(
    operator_context: _OperatorContext,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    selector: str,
) -> None:
    calls = _install_recorder(monkeypatch)
    exit_code, out, err = _run(
        operator_context,
        tmp_path,
        response_file=selector,
        capsys=capsys,
    )
    assert exit_code == 3
    assert err == "raw_response_file_not_absolute\n"
    assert out == ""
    assert calls == []


# -------------------------------------------------------------- special files


@pytest.mark.parametrize(
    "kind",
    ("directory", "symlink", "fifo", "socket", "character-device"),
)
def test_non_regular_response_objects_are_rejected(
    operator_context: _OperatorContext,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    kind: str,
) -> None:
    calls = _install_recorder(monkeypatch)
    if kind == "directory":
        target: object = tmp_path / "as-directory"
        (tmp_path / "as-directory").mkdir()
    elif kind == "symlink":
        real = _response_file(tmp_path, operator_context.raw_response)
        target = tmp_path / "as-symlink"
        (tmp_path / "as-symlink").symlink_to(real)
    elif kind == "fifo":
        target = tmp_path / "as-fifo"
        os.mkfifo(target)
    elif kind == "socket":
        target = tmp_path / "s"
        server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        server.bind(str(target))
        server.close()
    else:
        target = "/dev/null"
    before = _open_descriptor_count()
    exit_code, out, err = _run(
        operator_context,
        tmp_path,
        response_file=target,
        capsys=capsys,
    )
    assert exit_code == 3
    assert err == "raw_response_file_wrong_type\n"
    assert out == ""
    assert calls == []
    assert _open_descriptor_count() == before


def test_symlinked_ancestor_fails_closed(
    operator_context: _OperatorContext,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    calls = _install_recorder(monkeypatch)
    real_directory = tmp_path / "real"
    real_directory.mkdir()
    (real_directory / "raw-response.json").write_bytes(
        operator_context.raw_response
    )
    (tmp_path / "linked").symlink_to(real_directory, target_is_directory=True)
    exit_code, out, err = _run(
        operator_context,
        tmp_path,
        response_file=tmp_path / "linked" / "raw-response.json",
        capsys=capsys,
    )
    assert exit_code == 3
    assert err == "raw_response_file_unreadable\n"
    assert out == ""
    assert calls == []


# ---------------------------------------------------- exact-byte stable read


@pytest.mark.parametrize(
    "payload",
    (
        b"",
        b"{}",
        b"\x00\x01\x02\xff\xfe",
        b"line one\nline two\r\nline three\r",
        b"\xef\xbb\xbf{}",
        b'{"embedded":"nul\x00byte"}',
        b"a" * 131_072,
        b"x",
    ),
    ids=(
        "zero-bytes",
        "minimal-json",
        "binary",
        "mixed-newlines",
        "bom",
        "embedded-nul",
        "exactly-maximum",
        "single-byte",
    ),
)
def test_exact_operator_bytes_reach_ws01d_once(
    operator_context: _OperatorContext,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    payload: bytes,
) -> None:
    calls = _install_recorder(
        monkeypatch,
        result=publisher.publish_weekly_shadow_report(
            "unused",
            raw_response_bytes=b"",
            output_root="/nonexistent-root",
        ),
    )
    path = _response_file(tmp_path, payload)
    oracle = path.read_bytes()
    assert oracle == payload
    _run(operator_context, tmp_path, response_file=path, capsys=capsys)
    assert len(calls) == 1
    assert calls[0]["raw_response_bytes"] == oracle
    assert type(calls[0]["raw_response_bytes"]) is bytes


def test_zero_byte_response_is_accepted_and_ws01d_reports_missing(
    operator_context: _OperatorContext,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    exit_code, out, err = _run(
        operator_context,
        tmp_path,
        payload=b"",
        capsys=capsys,
    )
    assert exit_code == 1
    assert err == "WS01_BR_RESPONSE_MISSING\n"
    assert out == ""


def test_local_maximum_is_pinned_to_the_committed_contract_bound() -> None:
    assert cli._MAXIMUM_RAW_RESPONSE_BYTES == (
        contracts.RESOURCE_BOUND_PROFILE["raw_response_max_bytes"]
    )
    assert cli._MAXIMUM_RAW_RESPONSE_BYTES == 131_072


def test_exactly_maximum_size_is_read_and_forwarded_whole(
    operator_context: _OperatorContext,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    payload = bytes(range(256)) * 512
    assert len(payload) == 131_072
    calls = _install_recorder(
        monkeypatch,
        result=publisher.publish_weekly_shadow_report(
            "unused",
            raw_response_bytes=b"",
            output_root="/nonexistent-root",
        ),
    )
    path = _response_file(tmp_path, payload)
    _run(operator_context, tmp_path, response_file=path, capsys=capsys)
    assert len(calls) == 1
    assert calls[0]["raw_response_bytes"] == path.read_bytes()
    assert len(calls[0]["raw_response_bytes"]) == 131_072


@pytest.mark.parametrize("excess", (1, 4_096))
def test_over_limit_response_is_rejected_locally_without_truncation(
    operator_context: _OperatorContext,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    excess: int,
) -> None:
    calls = _install_recorder(monkeypatch)
    payload = b"z" * (131_072 + excess)
    path = _response_file(tmp_path, payload)
    exit_code, out, err = _run(
        operator_context,
        tmp_path,
        response_file=path,
        capsys=capsys,
    )
    assert exit_code == 3
    assert err == "raw_response_file_oversized\n"
    assert out == ""
    assert calls == []
    assert path.read_bytes() == payload


# ------------------------------------------------------------ mutation windows


def _mutation_recorder(
    monkeypatch: pytest.MonkeyPatch,
    *,
    hook: str,
    action,
    fire_at: int = 1,
) -> dict[str, int]:
    """Fire one deterministic mutation at an exact protocol boundary."""
    state = {"calls": 0, "fired": 0}
    if hook == "after-entry-stat":
        original = cli._require_regular

        def wrapper(status: object) -> None:
            original(status)
            state["calls"] += 1
            if state["calls"] == fire_at:
                action()
                state["fired"] += 1

        monkeypatch.setattr(cli, "_require_regular", wrapper)
    elif hook == "during-read":
        original_read = cli._os.read

        def read_wrapper(descriptor: int, size: int) -> bytes:
            chunk = original_read(descriptor, size)
            state["calls"] += 1
            if state["calls"] == fire_at:
                action()
                state["fired"] += 1
            return chunk

        monkeypatch.setattr(cli._os, "read", read_wrapper)
    elif hook == "between-reads":
        original_seek = cli._os.lseek

        def seek_wrapper(descriptor: int, offset: int, whence: int) -> int:
            state["calls"] += 1
            if state["calls"] == fire_at:
                action()
                state["fired"] += 1
            return original_seek(descriptor, offset, whence)

        monkeypatch.setattr(cli._os, "lseek", seek_wrapper)
    else:  # pragma: no cover - guarded by parametrisation
        raise AssertionError(hook)
    return state


@pytest.mark.parametrize(
    ("hook", "fire_at", "mutation"),
    (
        ("after-entry-stat", 1, "same-size-replacement"),
        ("after-entry-stat", 1, "inode-replacement"),
        ("after-entry-stat", 1, "grow"),
        ("after-entry-stat", 1, "shrink"),
        ("after-entry-stat", 1, "mode-change"),
        ("after-entry-stat", 1, "extra-hard-link"),
        ("after-entry-stat", 1, "grow-over-limit"),
        ("during-read", 1, "same-size-replacement"),
        ("during-read", 1, "grow"),
        ("between-reads", 1, "same-size-replacement"),
        ("between-reads", 1, "inode-replacement"),
        ("between-reads", 1, "grow"),
        ("between-reads", 1, "shrink"),
    ),
    ids=lambda value: str(value),
)
def test_response_file_mutation_is_detected_and_never_forwarded(
    operator_context: _OperatorContext,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    hook: str,
    fire_at: int,
    mutation: str,
) -> None:
    calls = _install_recorder(monkeypatch)
    original_payload = b"k" * 4_096
    path = _response_file(tmp_path, original_payload)

    def mutate() -> None:
        if mutation == "same-size-replacement":
            replacement = tmp_path / "replacement"
            replacement.write_bytes(b"!" * len(original_payload))
            os.replace(replacement, path)
        elif mutation == "inode-replacement":
            replacement = tmp_path / "replacement"
            replacement.write_bytes(original_payload)
            os.replace(replacement, path)
        elif mutation == "grow":
            with open(path, "ab") as handle:
                handle.write(b"extra")
        elif mutation == "shrink":
            with open(path, "r+b") as handle:
                handle.truncate(16)
        elif mutation == "mode-change":
            os.chmod(path, 0o600 if path.stat().st_mode & 0o077 else 0o644)
        elif mutation == "extra-hard-link":
            os.link(path, tmp_path / "hard-link")
        else:
            assert mutation == "grow-over-limit"
            with open(path, "ab") as handle:
                handle.write(b"q" * 131_072)

    state = _mutation_recorder(
        monkeypatch,
        hook=hook,
        action=mutate,
        fire_at=fire_at,
    )
    before = _open_descriptor_count()
    exit_code, out, err = _run(
        operator_context,
        tmp_path,
        response_file=path,
        capsys=capsys,
    )
    assert state["fired"] == 1
    assert exit_code == 3
    assert err == "raw_response_file_unstable\n"
    assert out == ""
    assert calls == []
    assert _open_descriptor_count() == before


def test_short_read_is_detected_as_instability(
    operator_context: _OperatorContext,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    calls = _install_recorder(monkeypatch)
    path = _response_file(tmp_path, b"m" * 4_096)
    original_read = cli._os.read
    state = {"calls": 0}

    def truncating_read(descriptor: int, size: int) -> bytes:
        state["calls"] += 1
        if state["calls"] == 1:
            return b""
        return original_read(descriptor, size)

    monkeypatch.setattr(cli._os, "read", truncating_read)
    exit_code, out, err = _run(
        operator_context,
        tmp_path,
        response_file=path,
        capsys=capsys,
    )
    assert exit_code == 3
    assert err == "raw_response_file_unstable\n"
    assert out == ""
    assert calls == []


@pytest.mark.parametrize("failing", ("read", "lseek", "fstat", "close"))
def test_primitive_failures_are_unreadable_and_never_forward(
    operator_context: _OperatorContext,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    failing: str,
) -> None:
    calls = _install_recorder(monkeypatch)
    path = _response_file(tmp_path, b"n" * 1_024)
    original = getattr(cli._os, failing)
    state = {"calls": 0}

    def failer(*args: object, **kwargs: object) -> object:
        state["calls"] += 1
        if state["calls"] == (2 if failing in ("fstat", "close") else 1):
            raise OSError("injected primitive failure")
        return original(*args, **kwargs)

    monkeypatch.setattr(cli._os, failing, failer)
    exit_code, out, err = _run(
        operator_context,
        tmp_path,
        response_file=path,
        capsys=capsys,
    )
    assert exit_code == 3
    assert err.rstrip("\n") in _LOCAL_TOKENS
    assert out == ""
    assert calls == []


def test_no_retry_occurs_after_instability(
    operator_context: _OperatorContext,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    calls = _install_recorder(monkeypatch)
    path = _response_file(tmp_path, b"p" * 2_048)
    opens: list[str] = []
    original_open = cli._os.open

    def counting_open(*args: object, **kwargs: object) -> int:
        if args and args[0] == path.name:
            opens.append(str(args[0]))
        return original_open(*args, **kwargs)

    monkeypatch.setattr(cli._os, "open", counting_open)
    _mutation_recorder(
        monkeypatch,
        hook="between-reads",
        action=lambda: open(path, "ab").write(b"grow"),
    )
    exit_code, _, err = _run(
        operator_context,
        tmp_path,
        response_file=path,
        capsys=capsys,
    )
    assert exit_code == 3
    assert err == "raw_response_file_unstable\n"
    assert len(opens) == 1
    assert calls == []


# ------------------------------------------------------------ WS01d invocation


def test_ws01d_receives_verbatim_selectors_exactly_once(
    operator_context: _OperatorContext,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    calls = _install_recorder(monkeypatch)
    output = _output_root(tmp_path)
    path = _response_file(tmp_path, operator_context.raw_response)
    exit_code, _, _ = _run(
        operator_context,
        tmp_path,
        response_file=path,
        output_root=output,
        capsys=capsys,
    )
    assert exit_code == 0
    assert len(calls) == 1
    call = calls[0]
    assert call["generation_id"] == operator_context.generation_id
    assert type(call["generation_id"]) is str
    assert call["raw_response_bytes"] == path.read_bytes()
    assert call["output_root"] == str(output)
    assert type(call["output_root"]) is str
    assert call["repository_root"] == str(operator_context.repository_root)
    assert type(call["repository_root"]) is str


def test_omitted_repository_root_is_forwarded_as_none(
    operator_context: _OperatorContext,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = _install_recorder(
        monkeypatch,
        result=publisher.publish_weekly_shadow_report(
            "unused",
            raw_response_bytes=b"",
            output_root="/nonexistent-root",
        ),
    )
    path = _response_file(tmp_path, operator_context.raw_response)
    cli.main(
        [
            "publish",
            "--generation-id",
            operator_context.generation_id,
            "--raw-response-file",
            str(path),
            "--output-root",
            str(_output_root(tmp_path)),
        ]
    )
    assert len(calls) == 1
    assert calls[0]["repository_root"] is None


def test_production_source_has_exactly_one_publish_call_site() -> None:
    tree = _cli_tree()
    attribute_calls = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "publish_weekly_shadow_report"
    ]
    assert len(attribute_calls) == 1
    only = attribute_calls[0]
    assert isinstance(only.func.value, ast.Name)
    assert only.func.value.id == "_report_publisher"
    assert not any(
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "publish_weekly_shadow_report"
        for node in ast.walk(tree)
    )
    source = Path(cli.__file__).read_text(encoding="utf-8")
    assert source.count("publish_weekly_shadow_report") == 2


# ------------------------------------------------------- exits and rendering


def test_new_publication_success_rendering_is_exact(
    operator_context: _OperatorContext,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    output = _output_root(tmp_path)
    exit_code, out, err = _run(
        operator_context,
        tmp_path,
        output_root=output,
        capsys=capsys,
    )
    assert exit_code == 0
    assert err == ""
    lines = out.split("\n")
    assert lines[-1] == ""
    fields = lines[:-1]
    assert len(fields) == 5
    keys = [field.split("=", 1)[0] for field in fields]
    assert keys == [
        "publication_reused",
        "report_identity_sha256",
        "run_summary_identity_sha256",
        "publication_relative_path",
        "artifact_filenames",
    ]
    values = dict(field.split("=", 1) for field in fields)
    assert values["publication_reused"] == "false"
    assert re.fullmatch(r"[0-9a-f]{64}", values["report_identity_sha256"])
    assert re.fullmatch(r"[0-9a-f]{64}", values["run_summary_identity_sha256"])
    assert values["publication_relative_path"] == (
        f"reports/{values['report_identity_sha256']}"
    )
    assert values["artifact_filenames"] == (
        f"{_REPORT_FILENAME},{_SUMMARY_FILENAME}"
    )
    assert not values["publication_relative_path"].startswith("/")
    assert str(output) not in out
    published = output / values["publication_relative_path"]
    assert sorted(item.name for item in published.iterdir()) == sorted(
        (_REPORT_FILENAME, _SUMMARY_FILENAME)
    )


def test_verified_reuse_renders_reused_true(
    operator_context: _OperatorContext,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    output = _output_root(tmp_path)
    first_code, first_out, _ = _run(
        operator_context, tmp_path, output_root=output, capsys=capsys
    )
    assert first_code == 0
    assert first_out.startswith("publication_reused=false\n")
    second_code, second_out, second_err = _run(
        operator_context, tmp_path, output_root=output, capsys=capsys
    )
    assert second_code == 0
    assert second_err == ""
    assert second_out.startswith("publication_reused=true\n")
    assert second_out.splitlines()[1:] == first_out.splitlines()[1:]


def test_non_ambiguous_ws01_reason_exits_one(
    operator_context: _OperatorContext,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    exit_code, out, err = _run(
        operator_context,
        tmp_path,
        payload=b"{}",
        capsys=capsys,
    )
    assert exit_code == 1
    assert out == ""
    assert err == "WS01_BR_ARTIFACT_ECHO_INCOMPLETE\n"


def test_publication_ambiguity_exits_four(
    operator_context: _OperatorContext,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    ambiguous = publisher._result_failure("WS01_BR_PUBLICATION_AMBIGUOUS")
    _install_recorder(monkeypatch, result=ambiguous)
    exit_code, out, err = _run(operator_context, tmp_path, capsys=capsys)
    assert exit_code == 4
    assert out == ""
    assert err == "WS01_BR_PUBLICATION_AMBIGUOUS\n"


@pytest.mark.parametrize(
    "reason_code",
    (
        "WS01_BR_PUBLICATION_FAILED",
        "WS01_BR_PUBLICATION_CONFLICT",
        "WS01_BR_IMMUTABLE_VERIFICATION_FAILED",
        "WS01_BR_REPORT_CONSTRUCTION_FAILED",
        "WS01_BR_INTERNAL_INVARIANT_FAILURE",
    ),
)
def test_each_non_ambiguous_frozen_reason_exits_one(
    operator_context: _OperatorContext,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    reason_code: str,
) -> None:
    assert reason_code in publisher._BLOCKING_REASON_CODES
    _install_recorder(monkeypatch, result=publisher._result_failure(reason_code))
    exit_code, out, err = _run(operator_context, tmp_path, capsys=capsys)
    assert exit_code == 1
    assert out == ""
    assert err == f"{reason_code}\n"


class _BrokenEnvelope:
    ok = True
    value = None
    reason_code = None


class _NoAttributes:
    pass


class _BadReceipt:
    ok = True
    reason_code = None

    class value:  # noqa: N801 - deliberately malformed receipt
        report_identity_sha256 = "not-a-digest"
        run_summary_identity_sha256 = "also-not"
        publication_relative_path = "/absolute/leak"
        artifact_filenames = ("only-one",)
        publication_reused = "true"


@pytest.mark.parametrize(
    "envelope",
    (_BrokenEnvelope(), _NoAttributes(), _BadReceipt()),
    ids=("ok-without-value", "missing-attributes", "malformed-receipt"),
)
def test_structurally_unusable_envelope_exits_four(
    operator_context: _OperatorContext,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    envelope: object,
) -> None:
    _install_recorder(monkeypatch, result=envelope)
    exit_code, out, err = _run(operator_context, tmp_path, capsys=capsys)
    assert exit_code == 4
    assert out == ""
    assert err == "WS01_BR_PUBLICATION_AMBIGUOUS\n"


def test_post_invocation_exception_exits_four_without_leakage(
    operator_context: _OperatorContext,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    secret = "POST-CALL-SECRET"
    _install_recorder(monkeypatch, raises=RuntimeError(secret))
    exit_code, out, err = _run(operator_context, tmp_path, capsys=capsys)
    assert exit_code == 4
    assert out == ""
    assert err == "WS01_BR_PUBLICATION_AMBIGUOUS\n"
    assert secret not in err


def test_pre_invocation_unexpected_failure_exits_one(
    operator_context: _OperatorContext,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    calls = _install_recorder(monkeypatch)
    secret = "PRE-CALL-SECRET"

    def explode(_selector: object) -> bytes:
        raise RuntimeError(secret)

    monkeypatch.setattr(cli, "_authenticated_response_bytes", explode)
    exit_code, out, err = _run(operator_context, tmp_path, capsys=capsys)
    assert exit_code == 1
    assert out == ""
    assert err == "WS01_BR_INTERNAL_INVARIANT_FAILURE\n"
    assert secret not in err
    assert calls == []


@pytest.mark.parametrize(
    "exception",
    (KeyboardInterrupt(), SystemExit(7), GeneratorExit()),
    ids=("keyboard-interrupt", "system-exit", "generator-exit"),
)
def test_control_flow_exceptions_propagate(
    operator_context: _OperatorContext,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    exception: BaseException,
) -> None:
    _install_recorder(monkeypatch, raises=exception)
    with pytest.raises(type(exception)):
        _run(operator_context, tmp_path)


def test_no_broad_base_exception_handler_exists() -> None:
    tree = _cli_tree()
    for node in ast.walk(tree):
        if isinstance(node, ast.ExceptHandler):
            assert node.type is not None, "bare except is forbidden"
            names = {
                child.id
                for child in ast.walk(node.type)
                if isinstance(child, ast.Name)
            }
            assert "BaseException" not in names
            assert "KeyboardInterrupt" not in names
            assert "SystemExit" not in names
            assert "GeneratorExit" not in names


def test_local_failure_vocabulary_is_closed_and_clean() -> None:
    assert cli._LOCAL_FAILURE_TOKENS == _LOCAL_TOKENS
    for token in cli._LOCAL_FAILURE_TOKENS:
        assert token.islower()
        assert not token.startswith("WS01_BR_")
        assert "/" not in token
        assert " " not in token
    tree = _cli_tree()
    literals = {
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant) and type(node.value) is str
    }
    assert {
        literal
        for literal in literals
        if literal.startswith("raw_response_file_")
    } == set(_LOCAL_TOKENS)
    assert {
        literal for literal in literals if literal.startswith("WS01_BR_")
    } == {
        "WS01_BR_PUBLICATION_AMBIGUOUS",
        "WS01_BR_INTERNAL_INVARIANT_FAILURE",
    }
    for reason in ("WS01_BR_PUBLICATION_AMBIGUOUS", "WS01_BR_INTERNAL_INVARIANT_FAILURE"):
        assert reason in publisher._BLOCKING_REASON_CODES


def test_failure_output_never_leaks_paths_bytes_or_diagnostics(
    operator_context: _OperatorContext,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    output = _output_root(tmp_path)
    path = _response_file(tmp_path, b"{}")
    exit_code, out, err = _run(
        operator_context,
        tmp_path,
        response_file=path,
        output_root=output,
        capsys=capsys,
    )
    assert exit_code == 1
    assert out == ""
    assert err.count("\n") == 1
    for forbidden in (
        str(path),
        str(output),
        str(operator_context.repository_root),
        "Traceback",
        "Error",
        "errno",
        ".attempt-",
        "{}",
    ):
        assert forbidden not in err


def test_module_smoke_run_via_python_dash_m(
    operator_context: _OperatorContext,
    tmp_path: Path,
) -> None:
    output = _output_root(tmp_path)
    path = _response_file(tmp_path, operator_context.raw_response)
    root = Path(__file__).parents[2]
    environment = dict(os.environ)
    environment["PYTHONPATH"] = str(root / "src")
    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            _CLI_MODULE,
            "publish",
            "--generation-id",
            operator_context.generation_id,
            "--raw-response-file",
            str(path),
            "--output-root",
            str(output),
            "--repository-root",
            str(operator_context.repository_root),
        ],
        capture_output=True,
        text=True,
        env=environment,
        cwd=str(root),
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    assert completed.stdout.startswith("publication_reused=false\n")
    assert completed.stderr == ""
    assert len(completed.stdout.splitlines()) == 5


def test_module_smoke_local_failure_via_python_dash_m(
    operator_context: _OperatorContext,
    tmp_path: Path,
) -> None:
    root = Path(__file__).parents[2]
    environment = dict(os.environ)
    environment["PYTHONPATH"] = str(root / "src")
    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            _CLI_MODULE,
            "publish",
            "--generation-id",
            operator_context.generation_id,
            "--raw-response-file",
            "relative-response.json",
            "--output-root",
            str(_output_root(tmp_path)),
        ],
        capture_output=True,
        text=True,
        env=environment,
        cwd=str(root),
        check=False,
    )
    assert completed.returncode == 3
    assert completed.stdout == ""
    assert completed.stderr == "raw_response_file_not_absolute\n"


def test_cli_import_causes_no_filesystem_or_registration_side_effect() -> None:
    root = Path(__file__).parents[2]
    program = (
        "import sys\n"
        "watch = ('os.mkdir','os.rename','os.remove','os.unlink','os.chmod',\n"
        "         'socket.socket','subprocess.Popen','subprocess.run',\n"
        "         'os.system','atexit.register','urllib.Request')\n"
        "events = []\n"
        "armed = False\n"
        "def hook(name, arguments):\n"
        "    if armed and name.startswith(watch):\n"
        "        events.append(name)\n"
        "sys.addaudithook(hook)\n"
        "import importlib\n"
        "armed = True\n"
        f"importlib.import_module({_CLI_MODULE!r})\n"
        "armed = False\n"
        "print(len(events))\n"
        "print(sorted(m for m in sys.modules if m.startswith('investment_orchestrator')))\n"
    )
    environment = dict(os.environ)
    environment["PYTHONPATH"] = str(root / "src")
    completed = subprocess.run(
        [sys.executable, "-c", program],
        capture_output=True,
        text=True,
        env=environment,
        cwd=str(root),
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    lines = completed.stdout.splitlines()
    assert lines[0] == "0"
    loaded = ast.literal_eval(lines[1])
    assert set(loaded) == {
        "investment_orchestrator",
        "investment_orchestrator.cli",
        _CLI_MODULE,
        "investment_orchestrator.observability",
        "investment_orchestrator.observability.weekly_shadow_01_package_builder",
        "investment_orchestrator.observability.weekly_shadow_01_report_publisher",
        "investment_orchestrator.observability.weekly_shadow_01_response_validator",
        "investment_orchestrator.observability.weekly_shadow_01_source_adapter",
    }


# ------------------------------------------------------------------ boundaries


def _real_sources() -> tuple[Path, dict[str, Any]]:
    root = Path(__file__).parents[2]
    sources: dict[str, Any] = {}
    for path in sorted((root / "src" / "investment_orchestrator").rglob("*.py")):
        relative = path.relative_to(root).as_posix()
        module = gap._module_name_for_path(relative)
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=relative)
        imports, findings, dynamic_imports = gap._imports_in_tree(tree, module)
        sources[module] = gap._ParsedProductionSource(
            relative_path=relative,
            module_name=module,
            tree=tree,
            imports=imports,
            dynamic_imports=dynamic_imports,
            findings=findings,
            report_reader=False,
            policy_reader=False,
            broker_capabilities=(),
        )
    return root, sources


def test_ltetf_external_consumer_inventory_is_exactly_two_declared_paths() -> None:
    root, sources = _real_sources()
    inventory = gap._scan_production_inventory(root)
    assert inventory.observer_external_consumers == (
        _OBSERVER_CLI_RELATIVE_PATH,
        _CLI_RELATIVE_PATH,
    )
    gap._validate_observer_inventory_isolation(inventory)
    assert gap.build_gap_report(root)["authority"] == gap.AUTHORITY_DECLARATION
    assert gap._ws01e_publication_consumer_binding_is_authorized(
        sources[_CLI_MODULE],
        sources=sources,
    )
    relations = gap._classify_consumer_relations(
        sources[_CLI_MODULE],
        sources=sources,
    )
    observability = [
        relation
        for relation in relations
        if gap._is_observer_relation_target(relation.target_module)
    ]
    assert len(observability) == 1
    assert (
        observability[0].category
        is gap._ConsumerRelationCategory.EXTERNAL_OBSERVER_CONSUMER
    )
    assert observability[0].target_module == _PUBLISHER_MODULE
    assert observability[0].importer_relative_path == _CLI_RELATIVE_PATH


def test_real_internal_ws01_relations_remain_exactly_three() -> None:
    _, sources = _real_sources()
    ws01_modules = {
        "investment_orchestrator.observability.weekly_shadow_01_source_adapter",
        "investment_orchestrator.observability.weekly_shadow_01_package_builder",
        "investment_orchestrator.observability.weekly_shadow_01_response_validator",
        _PUBLISHER_MODULE,
    }
    internal = {
        (
            relation.importer_module.rsplit(".", 1)[-1],
            relation.target_module.rsplit(".", 1)[-1],
        )
        for source in sources.values()
        for relation in gap._classify_consumer_relations(source, sources=sources)
        if relation.category
        is gap._ConsumerRelationCategory.INTERNAL_IMPLEMENTATION_EDGE
        and relation.importer_module in ws01_modules
        and relation.target_module in ws01_modules
    }
    assert internal == {
        ("weekly_shadow_01_package_builder", "weekly_shadow_01_source_adapter"),
        ("weekly_shadow_01_response_validator", "weekly_shadow_01_package_builder"),
        ("weekly_shadow_01_report_publisher", "weekly_shadow_01_response_validator"),
    }
    assert _CLI_MODULE not in {
        relation.importer_module
        for source in sources.values()
        for relation in gap._classify_consumer_relations(source, sources=sources)
        if relation.category
        is gap._ConsumerRelationCategory.INTERNAL_IMPLEMENTATION_EDGE
    }


def test_publisher_production_consumer_is_exactly_this_cli() -> None:
    _, sources = _real_sources()
    consumers = {
        relation.importer_relative_path
        for source in sources.values()
        for relation in gap._classify_consumer_relations(source, sources=sources)
        if relation.target_module == _PUBLISHER_MODULE
        and relation.importer_module != _PUBLISHER_MODULE
    }
    assert consumers == {_CLI_RELATIVE_PATH}
    callers = {
        source.relative_path
        for source in sources.values()
        if source.module_name != _CLI_MODULE
        and any(
            isinstance(node, ast.Call)
            and (
                (
                    isinstance(node.func, ast.Name)
                    and node.func.id == "publish_weekly_shadow_report"
                )
                or (
                    isinstance(node.func, ast.Attribute)
                    and node.func.attr == "publish_weekly_shadow_report"
                )
            )
            for node in ast.walk(source.tree)
        )
    }
    assert callers == set()


def test_no_report_reader_pointer_or_capability_consumer_appears() -> None:
    root, sources = _real_sources()
    inventory = gap._scan_production_inventory(root)
    assert inventory.report_artifact_readers == ()
    assert inventory.entry_points == ()
    assert inventory.dynamic_findings == ()
    assert inventory.policy_artifact_consumers == ()
    assert inventory.p4a_runtime_consumers == ()
    assert inventory.broker_capability_imports == ()
    assert inventory.weekly_llm_invocation_markers == ()
    assert inventory.prohibited_observer_capability_imports == ()
    literals = {
        node.value.lower()
        for node in ast.walk(_cli_tree())
        if isinstance(node, ast.Constant) and type(node.value) is str
    }
    assert not literals.intersection(
        {"latest", "current", "active", "pointer", "index", "manifest"}
    )


def test_cli_declares_no_workflow_state_gate_order_or_broker_dependency() -> None:
    _, sources = _real_sources()
    imports = sources[_CLI_MODULE].imports
    forbidden_prefixes = (
        "investment_orchestrator.workflow",
        "investment_orchestrator.state",
        "investment_orchestrator.permissions",
        "investment_orchestrator.orders",
        "investment_orchestrator.broker",
        "investment_orchestrator.market",
        "investment_orchestrator.llm",
        "investment_orchestrator.validators",
        "investment_orchestrator.parsers",
        "investment_orchestrator.research",
        "investment_orchestrator.offline",
        "investment_orchestrator.common",
        "investment_orchestrator.normalizers",
    )
    assert not any(
        imported.startswith(forbidden_prefixes) for imported in imports
    )
    assert sources[_CLI_MODULE].dynamic_imports == ()
    assert sources[_CLI_MODULE].findings == ()


def test_successful_publication_creates_only_report_only_output(
    operator_context: _OperatorContext,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    output = _output_root(tmp_path)
    exit_code, out, _ = _run(
        operator_context, tmp_path, output_root=output, capsys=capsys
    )
    assert exit_code == 0
    top_level = sorted(item.name for item in output.iterdir())
    assert top_level == ["report_attempts", "reports"]
    assert not any(
        item.name.lower()
        in {"latest", "current", "active", "pointer", "index", "manifest"}
        for item in output.rglob("*")
    )
    identity = dict(
        line.split("=", 1) for line in out.splitlines()
    )["report_identity_sha256"]
    generation = output / "reports" / identity
    assert sorted(item.name for item in generation.iterdir()) == sorted(
        (_REPORT_FILENAME, _SUMMARY_FILENAME)
    )
    report = json.loads((generation / _REPORT_FILENAME).read_bytes())
    assert report["negative_authority_profile"] == dict(
        contracts.NEGATIVE_AUTHORITY_PROFILE
    )
    assert stat.S_IMODE(generation.stat().st_mode) == 0o700
