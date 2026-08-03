"""Phase 2A isolation + CLI tests: offline archive tooling has no runtime reach."""

from __future__ import annotations

import ast
import json
from pathlib import Path
from typing import Any

import pytest

from investment_orchestrator.workflow import step1_research
from investment_orchestrator.offline.retirement_evidence import cli
from investment_orchestrator.offline.retirement_evidence import archive_coordination as coord

from test_step1a_shadow_run import _read, _settings, _setup_repo  # noqa: F401

_SRC_ROOT = Path(step1_research.__file__).resolve().parents[2]
_PACKAGE_ROOT = _SRC_ROOT / "investment_orchestrator"
_OFFLINE_ROOT = _PACKAGE_ROOT / "offline"


def _module_name(path: Path) -> str:
    rel = path.relative_to(_SRC_ROOT).with_suffix("")
    return ".".join(rel.parts)


def _offline_target(parts: list[str]) -> str:
    """Collapse a dotted import path down to its offline module, e.g. a.offline.b.c -> a.offline.b."""
    idx = parts.index("offline")
    end = idx + 2 if len(parts) > idx + 1 else idx + 1
    return ".".join(parts[:end])


def _offline_import_edges(path: Path) -> set[tuple[str, str]]:
    """Return (source_module, offline_target_module) edges for offline imports in path."""
    source = _module_name(path)
    tree = ast.parse(path.read_text(encoding="utf-8"))
    package_parts = source.split(".")[:-1]
    edges: set[tuple[str, str]] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                parts = alias.name.split(".")
                if "offline" in parts:
                    edges.add((source, _offline_target(parts)))
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            if node.level == 0:
                target = module.split(".") if module else []
            else:
                base = package_parts[: len(package_parts) - (node.level - 1)]
                target = base + module.split(".") if module else base
            if "offline" in target:
                edges.add((source, _offline_target(target)))
            elif any(alias.name == "offline" for alias in node.names):
                edges.add((source, ".".join(target + ["offline"])))
    return edges


_ALLOWED_NON_OFFLINE_TO_OFFLINE_IMPORT_EDGES = frozenset({
    (
        "investment_orchestrator.cli.run_mmi_h2c_capture",
        "investment_orchestrator.offline.mmi_h2c_manual_capture_session",
    ),
})


def test_only_h2c_capture_cli_imports_the_offline_package() -> None:
    actual_non_offline_to_offline_import_edges = {
        edge
        for path in sorted(_PACKAGE_ROOT.rglob("*.py"))
        if _OFFLINE_ROOT not in path.parents
        for edge in _offline_import_edges(path)
    }
    unexpected = (
        actual_non_offline_to_offline_import_edges
        - _ALLOWED_NON_OFFLINE_TO_OFFLINE_IMPORT_EDGES
    )
    assert not unexpected, f"unapproved production->offline import edges: {sorted(unexpected)}"
    assert (
        actual_non_offline_to_offline_import_edges
        == _ALLOWED_NON_OFFLINE_TO_OFFLINE_IMPORT_EDGES
    ), (
        "approved edge is missing: "
        f"{sorted(_ALLOWED_NON_OFFLINE_TO_OFFLINE_IMPORT_EDGES - actual_non_offline_to_offline_import_edges)}"
    )


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


def _coordination(tmp_path: Path) -> Path:
    anchor = tmp_path / "retirement-archive-coordination.anchor"
    anchor.write_bytes(coord.COORDINATION_ANCHOR_BYTES)
    return anchor


def test_cli_accepted_exit_zero(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    src = _write(tmp_path, _obs_for_cli())
    code = cli.main([
        "--source", str(src), "--dest", str(tmp_path / "arch"),
        "--coordination-file", str(_coordination(tmp_path)),
    ])
    out = json.loads(capsys.readouterr().out)
    assert code == 0
    assert out["decision"] == "accepted"
    assert out["duplicate"] is False


def test_cli_rejected_exit_three(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    src = tmp_path / "bad.json"
    src.write_text("{ not json", encoding="utf-8")
    code = cli.main([
        "--source", str(src), "--dest", str(tmp_path / "arch"),
        "--coordination-file", str(_coordination(tmp_path)),
    ])
    out = json.loads(capsys.readouterr().out)
    assert code == 3
    assert out["decision"] == "rejected"


def test_cli_layout_error_exit_two(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    root = tmp_path / "arch"
    root.mkdir()
    (root / "retirement_archive_layout_version").write_text("retirement_archive_layout_v999\n")
    src = _write(tmp_path, _obs_for_cli())
    code = cli.main([
        "--source", str(src), "--dest", str(root),
        "--coordination-file", str(_coordination(tmp_path)),
    ])
    err = json.loads(capsys.readouterr().err)
    assert code == 2
    assert err["error"] == "archive_layout_error"


def test_cli_has_no_tool_version_option() -> None:
    parser = cli.build_parser()
    options = {action.dest for action in parser._actions}
    assert "tool_version" not in options
    assert "provenance" in options
    assert "coordination_file" in options
