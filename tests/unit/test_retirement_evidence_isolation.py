"""Phase 2A isolation + CLI tests: offline archive tooling has no runtime reach."""

from __future__ import annotations

import ast
import json
from pathlib import Path
from typing import Any

import pytest

from investment_orchestrator.workflow import step1_research
from investment_orchestrator.offline.retirement_evidence import cli

from test_step1a_shadow_run import _read, _settings, _setup_repo  # noqa: F401

_SRC_ROOT = Path(step1_research.__file__).resolve().parents[2]
_PACKAGE_ROOT = _SRC_ROOT / "investment_orchestrator"
_OFFLINE_ROOT = _PACKAGE_ROOT / "offline"


def _module_name(path: Path) -> str:
    rel = path.relative_to(_SRC_ROOT).with_suffix("")
    return ".".join(rel.parts)


def _imports_offline(path: Path) -> bool:
    """True iff the file imports anything under investment_orchestrator.offline."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    package_parts = _module_name(path).split(".")[:-1]
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            if any("offline" in alias.name.split(".") for alias in node.names):
                return True
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            if node.level == 0:
                target = module.split(".")
            else:
                base = package_parts[: len(package_parts) - (node.level - 1)]
                target = base + module.split(".") if module else base
            if "offline" in target:
                return True
            if any(alias.name == "offline" for alias in node.names):
                return True
    return False


def test_no_production_module_imports_the_offline_package() -> None:
    offenders = [
        _module_name(path)
        for path in sorted(_PACKAGE_ROOT.rglob("*.py"))
        if _OFFLINE_ROOT not in path.parents and _imports_offline(path)
    ]
    assert offenders == [], f"production modules import offline tooling: {offenders}"


def test_step1_research_module_does_not_reference_offline_archive() -> None:
    source = Path(step1_research.__file__).read_text(encoding="utf-8")
    assert "offline.retirement_evidence" not in source
    assert "retirement_archive" not in source


def test_step1_parse_creates_no_archive_artifacts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _setup_repo(tmp_path, monkeypatch)
    monkeypatch.setattr(
        step1_research,
        "_resolve_step1a_retirement_observation_code_identity",
        lambda: {
            "git_commit": "1" * 40,
            "git_state": "clean",
            "code_version_usable_for_evidence": True,
        },
    )

    step1_research.parse_step1_output(strategy_settings=_settings())

    # A normal Step 1 parse must never touch/create an archive.
    assert list(tmp_path.rglob("retirement_archive_layout_version")) == []
    archive_dirs = [
        p
        for p in tmp_path.rglob("*")
        if p.is_dir() and p.name in {"accepted", "quarantined", "rejected"}
    ]
    assert archive_dirs == []


# --- CLI ---------------------------------------------------------------------
def _obs_for_cli() -> dict[str, Any]:
    from test_step1a_retirement_observation import _builder_inputs
    from investment_orchestrator.research.step1a_retirement_observation import (
        build_step1a_retirement_observation,
    )

    return build_step1a_retirement_observation(**_builder_inputs())


def _write(tmp_path: Path, payload: Any, name: str = "obs.json") -> Path:
    path = tmp_path / name
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return path


def test_cli_accepted_exit_zero(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    src = _write(tmp_path, _obs_for_cli())
    code = cli.main(["--source", str(src), "--dest", str(tmp_path / "arch")])
    out = json.loads(capsys.readouterr().out)
    assert code == 0
    assert out["decision"] == "accepted"
    assert out["duplicate"] is False


def test_cli_rejected_exit_three(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    src = tmp_path / "bad.json"
    src.write_text("{ not json", encoding="utf-8")
    code = cli.main(["--source", str(src), "--dest", str(tmp_path / "arch")])
    out = json.loads(capsys.readouterr().out)
    assert code == 3
    assert out["decision"] == "rejected"


def test_cli_layout_error_exit_two(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    root = tmp_path / "arch"
    root.mkdir()
    (root / "retirement_archive_layout_version").write_text("retirement_archive_layout_v999\n")
    src = _write(tmp_path, _obs_for_cli())
    code = cli.main(["--source", str(src), "--dest", str(root)])
    err = json.loads(capsys.readouterr().err)
    assert code == 2
    assert err["error"] == "archive_layout_error"


def test_cli_has_no_tool_version_option() -> None:
    parser = cli.build_parser()
    options = {action.dest for action in parser._actions}
    assert "tool_version" not in options
    assert "provenance" in options
