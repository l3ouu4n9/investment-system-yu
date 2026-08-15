from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from investment_orchestrator.cli import run_lh2_manual_capture
from investment_orchestrator.mmi import source_capture


def _install_lh2_checkout(root: Path, raw: bytes) -> Path:
    """Install a trusted checkout whose long-horizon research leaf holds ``raw``."""
    (root / "pyproject.toml").write_bytes(
        b'[project]\nname = "investment-orchestrator"\n'
    )
    package_init = root / "src/investment_orchestrator/__init__.py"
    package_init.parent.mkdir(parents=True, exist_ok=True)
    package_init.write_bytes(b'"""investment_orchestrator package."""\n')
    module = root / "src/investment_orchestrator/mmi/source_capture.py"
    module.parent.mkdir(parents=True, exist_ok=True)
    module.write_bytes(
        b"_PRODUCTION_MODULE_SUFFIX = ()\n"
        b"def capture_current_mmi_source(\n"
        b"    role, *, expected_source_sha256\n"
        b"):\n"
        b"    raise NotImplementedError\n"
    )
    leaf = root / "inputs/current/long_horizon_research.json"
    leaf.parent.mkdir(parents=True, exist_ok=True)
    leaf.write_bytes(raw)
    return module


def _bind_checkout(
    monkeypatch: pytest.MonkeyPatch,
    root: Path,
    module: Path,
) -> None:
    """Point both the capture locator and the receipt root at one checkout."""
    monkeypatch.setattr(
        source_capture,
        "_PRODUCTION_MODULE_FILE",
        str(module),
    )
    monkeypatch.setattr(run_lh2_manual_capture, "repo_root", lambda: root)


def _receipt_path(root: Path) -> Path:
    return root / "inputs/current/lh2_manual_capture_receipt.json"


def _tree(root: Path) -> dict[str, bytes | None]:
    return {
        str(item.relative_to(root)): (
            item.read_bytes() if item.is_file() else None
        )
        for item in root.rglob("*")
    }


def test_cli_exposes_no_authority_bearing_argument(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Invocation itself is the capture event; stray operator input is refused."""
    help_text = run_lh2_manual_capture._parser().format_help()
    for forbidden in (
        "--source-path",
        "--output-path",
        "--expected-sha256",
        "--max-age",
        "--now-date",
        "--role",
        "--permission",
    ):
        assert forbidden not in help_text

    for stray in (
        ["--source-path", "/tmp/elsewhere.json"],
        ["--output-path", "/tmp/elsewhere.json"],
        ["--expected-sha256", "0" * 64],
        ["--max-age", "180"],
        ["--role", "STRATEGY_SETTINGS"],
        ["unexpected-positional"],
    ):
        with pytest.raises(SystemExit) as caught:
            run_lh2_manual_capture.main(stray)
        assert caught.value.code == 2
        capsys.readouterr()


def test_cli_writes_the_fixed_receipt_with_the_exact_captured_digest(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A successful capture persists exactly the four-field receipt.

    The payload is deliberately not valid V2 content: the receipt asserts only
    that these exact bytes were captured.
    """
    raw = b'{"not": "a valid v2 payload"}\n'
    module = _install_lh2_checkout(tmp_path, raw)
    _bind_checkout(monkeypatch, tmp_path, module)

    assert run_lh2_manual_capture.main([]) == 0

    receipt = json.loads(_receipt_path(tmp_path).read_text(encoding="utf-8"))
    assert receipt == {
        "schema_version": "lh2_manual_capture_receipt_v1",
        "source_role": "LONG_HORIZON_RESEARCH",
        "observed_sha256": hashlib.sha256(raw).hexdigest(),
        "observed_size_bytes": len(raw),
    }
    captured = capsys.readouterr()
    assert captured.out == (
        f"observed_sha256={hashlib.sha256(raw).hexdigest()}\n"
        f"observed_size_bytes={len(raw)}\n"
    )
    assert captured.err == ""


def test_cli_writes_no_receipt_and_no_other_artifact_when_capture_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A provenance failure persists nothing at all.

    Complete tree equality proves no receipt, and no state, permission, gate,
    publication, or order artifact, is written on the failing path.
    """
    module = _install_lh2_checkout(tmp_path, b'{"present": true}\n')
    _bind_checkout(monkeypatch, tmp_path, module)
    (tmp_path / "inputs/current/long_horizon_research.json").unlink()
    before = _tree(tmp_path)

    assert run_lh2_manual_capture.main([]) != 0

    assert not _receipt_path(tmp_path).exists()
    assert _tree(tmp_path) == before
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "MMI_SOURCE_MISSING" in captured.err


def test_cli_recapture_replaces_only_the_receipt_and_survives_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Recapture replaces the fixed receipt; a failed recapture preserves it."""
    first_raw = b'{"generation": "first"}\n'
    module = _install_lh2_checkout(tmp_path, first_raw)
    _bind_checkout(monkeypatch, tmp_path, module)
    leaf = tmp_path / "inputs/current/long_horizon_research.json"

    assert run_lh2_manual_capture.main([]) == 0
    first_receipt = _receipt_path(tmp_path).read_bytes()
    assert json.loads(first_receipt)["observed_sha256"] == (
        hashlib.sha256(first_raw).hexdigest()
    )

    # A failed recapture leaves the previous complete receipt untouched.
    leaf.unlink()
    tree_before_failure = _tree(tmp_path)
    assert run_lh2_manual_capture.main([]) != 0
    assert _receipt_path(tmp_path).read_bytes() == first_receipt
    assert _tree(tmp_path) == tree_before_failure

    # A successful recapture atomically replaces it, leaving no temp file.
    second_raw = b'{"generation": "second"}\n'
    leaf.write_bytes(second_raw)
    assert run_lh2_manual_capture.main([]) == 0

    second_receipt = json.loads(
        _receipt_path(tmp_path).read_text(encoding="utf-8")
    )
    assert second_receipt["observed_sha256"] == (
        hashlib.sha256(second_raw).hexdigest()
    )
    assert second_receipt["observed_size_bytes"] == len(second_raw)
    assert _receipt_path(tmp_path).read_bytes() != first_receipt
    assert sorted(
        item.name for item in _receipt_path(tmp_path).parent.iterdir()
    ) == [
        "lh2_manual_capture_receipt.json",
        "long_horizon_research.json",
    ]
    capsys.readouterr()
