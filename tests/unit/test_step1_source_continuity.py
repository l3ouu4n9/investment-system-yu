"""Closed-contract tests for the Step 1 render commitment v2 + continuity report.

The producer records, at render time, the SHA-256 of the exact strategy and
portfolio buffers it compiled and of the exact prompt text it handed to the
writer. The consumer runs first in every parse and reports whether all three
CURRENT endpoints still hash to those values. Both files are report-only:
nothing reads them as authority.

The retired S1P-1 leaves (``render_source_commitment.json`` /
``render_source_continuity_report.json``, schema ``*_v1``) keep their original
source-only meaning forever. v2 retires them rather than redefining them, so
archived v1 trees stay readable as v1; the only thing current code does with
those names is delete them so a pre-upgrade tree cannot leave stale success
evidence beside fresh v2 evidence.

These tests own the closed contract (schema, reason vocabulary, ordering,
persistence ordering, migration, and failure semantics). They deliberately do
not re-prove the generic temp/fsync/replace internals of ``atomic_write_text``,
which ``tests/unit/test_atomic_write.py`` already owns.
"""

from __future__ import annotations

import ast
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from investment_orchestrator.common.artifact_management import (
    archive_current_artifacts,
    clear_current_artifacts,
)
from investment_orchestrator.common.paths import repo_root
from investment_orchestrator.workflow import step1_research


COMMITMENT_FILENAME = "render_commitment.json"
REPORT_FILENAME = "render_continuity_report.json"
COMMITMENT_SCHEMA_VERSION = "step1_render_commitment_v2"
REPORT_SCHEMA_VERSION = "step1_render_continuity_report_v2"

# Retired S1P-1 identities. Deleted by current code, never produced or read.
RETIRED_COMMITMENT_FILENAME = "render_source_commitment.json"
RETIRED_REPORT_FILENAME = "render_source_continuity_report.json"
RETIRED_COMMITMENT_SCHEMA_VERSION = "step1_render_source_commitment_v1"

COMPLETE_MATCH = "RENDER_ENDPOINT_COMPLETE_MATCH"
UNVERIFIED = "RENDER_ENDPOINT_UNVERIFIED"

FIXTURE_DIR = Path(__file__).resolve().parents[1] / "fixtures" / "step1_contract_failures"

STRATEGY_TEXT = (
    "strategy_marker: S1P_STRATEGY_ONE\n"
    "core_universe:\n"
    "  - QQQ\n"
    "  - VOO\n"
    "satellite_universe:\n"
    "  - SMH\n"
    "user_approved_extended_etf_static_list:\n"
    "  - TICKER_ONE\n"
)
STRATEGY_TEXT_EDITED = STRATEGY_TEXT.replace("S1P_STRATEGY_ONE", "S1P_STRATEGY_EDITED")
PORTFOLIO_TEXT = "portfolio_marker: S1P_PORTFOLIO_ONE\n"
PORTFOLIO_TEXT_EDITED = "portfolio_marker: S1P_PORTFOLIO_EDITED\n"
# Stand-in prompt bytes for validator-focused cases that never invoke a render.
PROMPT_TEXT = "prompt_marker: S1P_PROMPT_ONE\n"
PROMPT_TEXT_EDITED = "prompt_marker: S1P_PROMPT_EDITED\n"


# ---------------------------------------------------------------------------
# Harness
# ---------------------------------------------------------------------------


def _write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def _setup_repo(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    strategy_text: str = STRATEGY_TEXT,
    portfolio_text: str = PORTFOLIO_TEXT,
    prompt_text: str = PROMPT_TEXT,
) -> Path:
    """Point Step 1 at an isolated repo root holding all three CURRENT endpoints.

    The stand-in ``prompt.txt`` lets validator-focused cases exercise the
    commitment contract without paying for a full render; cases that render for
    real simply overwrite it.
    """
    monkeypatch.setattr(step1_research, "repo_root", lambda: tmp_path)
    _write(tmp_path / "inputs" / "current" / "strategy_settings.yaml", strategy_text)
    _write(tmp_path / "inputs" / "current" / "portfolio_snapshot.txt", portfolio_text)
    _write(_artifact_dir(tmp_path) / "prompt.txt", prompt_text)
    return tmp_path


def _artifact_dir(tmp_path: Path) -> Path:
    return tmp_path / "artifacts" / "current" / "step1_research"


def _commitment_path(tmp_path: Path) -> Path:
    return _artifact_dir(tmp_path) / COMMITMENT_FILENAME


def _report_path(tmp_path: Path) -> Path:
    return _artifact_dir(tmp_path) / REPORT_FILENAME


def _retired_commitment_path(tmp_path: Path) -> Path:
    return _artifact_dir(tmp_path) / RETIRED_COMMITMENT_FILENAME


def _retired_report_path(tmp_path: Path) -> Path:
    return _artifact_dir(tmp_path) / RETIRED_REPORT_FILENAME


def _json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert isinstance(payload, dict)
    return payload


def _sha(text: str) -> str:
    """Independent digest oracle: hashes the literal text, never a production read."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _commitment_text(**overrides: Any) -> str:
    payload: dict[str, Any] = {
        "schema_version": COMMITMENT_SCHEMA_VERSION,
        "strategy_settings_sha256": _sha(STRATEGY_TEXT),
        "portfolio_snapshot_sha256": _sha(PORTFOLIO_TEXT),
        "prompt_sha256": _sha(PROMPT_TEXT),
    }
    payload.update(overrides)
    return json.dumps(payload, ensure_ascii=False, indent=2) + "\n"


def _plant_commitment(tmp_path: Path, text: str) -> Path:
    return _write(_commitment_path(tmp_path), text)


def _reason_codes(tmp_path: Path) -> list[str]:
    """Evaluate continuity through the owning report writer and read the result."""
    step1_research._write_step1_render_continuity_report()  # noqa: SLF001
    report = _json(_report_path(tmp_path))
    assert set(report) == {
        "schema_version",
        "status",
        "reason_codes",
        "authority_effect",
    }
    assert report["schema_version"] == REPORT_SCHEMA_VERSION
    assert report["authority_effect"] == "NONE"
    reason_codes = report["reason_codes"]
    assert isinstance(reason_codes, list)
    assert report["status"] == (COMPLETE_MATCH if not reason_codes else UNVERIFIED)
    return reason_codes


def _fail_unlink_of(monkeypatch: pytest.MonkeyPatch, filename: str, error: OSError) -> None:
    """Make deletion of exactly one leaf fail, leaving every other unlink real."""
    real_unlink = Path.unlink

    def fake_unlink(self: Path, *args: Any, **kwargs: Any) -> None:
        if self.name == filename:
            raise error
        return real_unlink(self, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", fake_unlink)


def _raise_oserror(*_args: Any, **_kwargs: Any) -> Any:
    raise OSError("injected persistence failure")


def _capture_writer_prompt_text(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Record the exact prompt text each render hands to the writer.

    This is the only authorized origin for ``prompt_sha256``, so tests compare
    the committed digest against an independent hash of THIS captured text
    rather than against anything read back from disk.
    """
    captured: list[str] = []
    real_write_rendered_prompt = step1_research.write_rendered_prompt

    def capturing_write_rendered_prompt(path: Any, text: str) -> Path:
        captured.append(text)
        return real_write_rendered_prompt(path, text)

    monkeypatch.setattr(
        step1_research, "write_rendered_prompt", capturing_write_rendered_prompt
    )
    return captured


def _plant_retired_v1_pair(tmp_path: Path) -> tuple[Path, Path]:
    """Plant a pre-upgrade tree: a v1 commitment and a v1 COMPLETE_MATCH report."""
    commitment = _write(
        _retired_commitment_path(tmp_path),
        json.dumps(
            {
                "schema_version": RETIRED_COMMITMENT_SCHEMA_VERSION,
                "strategy_settings_sha256": _sha(STRATEGY_TEXT),
                "portfolio_snapshot_sha256": _sha(PORTFOLIO_TEXT),
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
    )
    report = _write(
        _retired_report_path(tmp_path),
        json.dumps(
            {
                "schema_version": "step1_render_source_continuity_report_v1",
                "status": "SOURCE_ENDPOINT_COMPLETE_MATCH",
                "reason_codes": [],
                "authority_effect": "NONE",
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
    )
    return commitment, report


def _canonical_endpoint_paths(tmp_path: Path) -> list[Path]:
    """The three CURRENT endpoints in the closed canonical evaluation order."""
    return [
        tmp_path / "inputs" / "current" / "strategy_settings.yaml",
        tmp_path / "inputs" / "current" / "portfolio_snapshot.txt",
        _artifact_dir(tmp_path) / "prompt.txt",
    ]


def _record_endpoint_reads(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> list[Path]:
    """Record byte reads of the three endpoints only, in call order.

    Reads of the commitment itself are deliberately excluded: this oracle is
    about which ENDPOINTS the evaluation touches, and how many times.
    """
    endpoints = set(_canonical_endpoint_paths(tmp_path))
    real_read_bytes = Path.read_bytes
    reads: list[Path] = []

    def counting_read_bytes(self: Path) -> bytes:
        if self in endpoints:
            reads.append(self)
        return real_read_bytes(self)

    monkeypatch.setattr(Path, "read_bytes", counting_read_bytes)
    return reads


# ---------------------------------------------------------------------------
# Commitment contract (§ closed schema / validation precedence)
# ---------------------------------------------------------------------------


def test_producer_writes_exactly_the_closed_commitment_contract(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _setup_repo(tmp_path, monkeypatch)
    captured = _capture_writer_prompt_text(monkeypatch)

    step1_research.render_step1_prompt()

    raw = _commitment_path(tmp_path).read_text(encoding="utf-8")
    commitment = json.loads(raw)
    assert set(commitment) == {
        "schema_version",
        "strategy_settings_sha256",
        "portfolio_snapshot_sha256",
        "prompt_sha256",
    }
    assert commitment["schema_version"] == COMMITMENT_SCHEMA_VERSION
    assert commitment["strategy_settings_sha256"] == _sha(STRATEGY_TEXT)
    assert commitment["portfolio_snapshot_sha256"] == _sha(PORTFOLIO_TEXT)
    assert len(captured) == 1
    assert commitment["prompt_sha256"] == _sha(captured[0])
    for digest in (
        commitment["strategy_settings_sha256"],
        commitment["portfolio_snapshot_sha256"],
        commitment["prompt_sha256"],
    ):
        assert len(digest) == 64
        assert digest == digest.lower()
        assert set(digest) <= set("0123456789abcdef")
    # Deterministic readable serialization, terminal newline, no ASCII escaping.
    assert raw == json.dumps(commitment, ensure_ascii=False, indent=2) + "\n"


def test_v1_names_are_never_produced_by_the_current_render_or_parse(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """v1 is retired, not redefined: current code writes neither v1 leaf ever."""
    _setup_repo(tmp_path, monkeypatch)

    step1_research.render_step1_prompt()
    step1_research._write_step1_render_continuity_report()  # noqa: SLF001

    assert not _retired_commitment_path(tmp_path).exists()
    assert not _retired_report_path(tmp_path).exists()
    # And the only schema this producer emits is v2.
    assert (
        _json(_commitment_path(tmp_path))["schema_version"] == COMMITMENT_SCHEMA_VERSION
    )
    assert _json(_report_path(tmp_path))["schema_version"] == REPORT_SCHEMA_VERSION


def test_valid_commitment_matching_both_sources_yields_no_reason_codes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _setup_repo(tmp_path, monkeypatch)
    _plant_commitment(tmp_path, _commitment_text())

    assert _reason_codes(tmp_path) == []


@pytest.mark.parametrize(
    "commitment_text",
    [
        pytest.param(
            json.dumps(
                {
                    "schema_version": COMMITMENT_SCHEMA_VERSION,
                    "strategy_settings_sha256": _sha(STRATEGY_TEXT),
                }
            ),
            id="missing-portfolio-digest",
        ),
        pytest.param(
            _commitment_text(rendered_at="2026-08-10T00:00:00Z"),
            id="unknown-field",
        ),
        pytest.param('{"a": 1, "a": 2}', id="duplicate-member-non-contract-keys"),
        pytest.param(
            '{\n'
            f'  "schema_version": "{COMMITMENT_SCHEMA_VERSION}",\n'
            f'  "strategy_settings_sha256": "{_sha(STRATEGY_TEXT)}",\n'
            f'  "strategy_settings_sha256": "{_sha(STRATEGY_TEXT)}",\n'
            f'  "portfolio_snapshot_sha256": "{_sha(PORTFOLIO_TEXT)}"\n'
            '}\n',
            id="duplicate-member-exact-contract-keys",
        ),
        pytest.param(_commitment_text(strategy_settings_sha256=None), id="null-digest"),
        pytest.param(_commitment_text(portfolio_snapshot_sha256=12345), id="int-digest"),
        pytest.param(
            _commitment_text(strategy_settings_sha256=_sha(STRATEGY_TEXT).upper()),
            id="uppercase-digest",
        ),
        pytest.param(
            _commitment_text(portfolio_snapshot_sha256=_sha(PORTFOLIO_TEXT)[:63]),
            id="short-digest",
        ),
        pytest.param(
            _commitment_text(portfolio_snapshot_sha256=_sha(PORTFOLIO_TEXT) + "a"),
            id="long-digest",
        ),
        pytest.param(
            _commitment_text(strategy_settings_sha256="z" * 64),
            id="non-hex-digest",
        ),
        pytest.param(json.dumps({"schema_version": 1}), id="non-string-version"),
        pytest.param(json.dumps({"strategy_settings_sha256": "a" * 64}), id="absent-version"),
        pytest.param(json.dumps([COMMITMENT_SCHEMA_VERSION]), id="top-level-array"),
        pytest.param('"just-a-string"', id="top-level-string"),
    ],
)
def test_shape_violations_are_invalid_contract(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    commitment_text: str,
) -> None:
    _setup_repo(tmp_path, monkeypatch)
    _plant_commitment(tmp_path, commitment_text)

    assert _reason_codes(tmp_path) == ["RENDER_COMMITMENT_INVALID_CONTRACT"]


@pytest.mark.parametrize(
    "commitment_text",
    [
        pytest.param("{", id="truncated-object"),
        pytest.param("", id="empty-file"),
        pytest.param("{'schema_version': 'x'}", id="single-quoted"),
        pytest.param(_commitment_text() + _commitment_text(), id="two-concatenated-objects"),
    ],
)
def test_json_syntax_errors_are_invalid_json(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    commitment_text: str,
) -> None:
    _setup_repo(tmp_path, monkeypatch)
    _plant_commitment(tmp_path, commitment_text)

    assert _reason_codes(tmp_path) == ["RENDER_COMMITMENT_INVALID_JSON"]


def test_wrong_string_schema_version_is_classified_as_unsupported(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _setup_repo(tmp_path, monkeypatch)
    _plant_commitment(
        tmp_path, _commitment_text(schema_version="step1_render_source_commitment_v2")
    )

    assert _reason_codes(tmp_path) == ["RENDER_COMMITMENT_UNSUPPORTED_SCHEMA_VERSION"]


def test_unsupported_version_is_not_swallowed_by_v2_field_validation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An unsupported version legitimately carries a different field set.

    Reporting that as a generic contract violation would hide the real reason
    an operator must act on, so the v2 field rules only run once the version is
    confirmed to be v2. ``step1_render_source_commitment_v2`` is retained here
    as a deliberate negative fixture: it is not, and must never become, a
    supported version string.
    """
    _setup_repo(tmp_path, monkeypatch)
    _plant_commitment(
        tmp_path,
        json.dumps(
            {
                "schema_version": "step1_render_source_commitment_v2",
                "sources": {"strategy": "NOT-A-DIGEST"},
            }
        ),
    )

    assert _reason_codes(tmp_path) == ["RENDER_COMMITMENT_UNSUPPORTED_SCHEMA_VERSION"]


def test_absent_commitment_is_missing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _setup_repo(tmp_path, monkeypatch)

    assert _reason_codes(tmp_path) == ["RENDER_COMMITMENT_MISSING"]


def test_broken_commitment_symlink_is_missing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _setup_repo(tmp_path, monkeypatch)
    _artifact_dir(tmp_path).mkdir(parents=True, exist_ok=True)
    _commitment_path(tmp_path).symlink_to(tmp_path / "nonexistent-commitment.json")

    assert _reason_codes(tmp_path) == ["RENDER_COMMITMENT_MISSING"]


def test_directory_in_place_of_commitment_is_unreadable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _setup_repo(tmp_path, monkeypatch)
    _commitment_path(tmp_path).mkdir(parents=True)

    assert _reason_codes(tmp_path) == ["RENDER_COMMITMENT_UNREADABLE"]


def test_invalid_utf8_commitment_is_unreadable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _setup_repo(tmp_path, monkeypatch)
    _artifact_dir(tmp_path).mkdir(parents=True, exist_ok=True)
    _commitment_path(tmp_path).write_bytes(b'{"schema_version": "\xff"}')

    assert _reason_codes(tmp_path) == ["RENDER_COMMITMENT_UNREADABLE"]


# ---------------------------------------------------------------------------
# Render persistence and invalidation ordering
# ---------------------------------------------------------------------------


def _record_render_persistence(
    monkeypatch: pytest.MonkeyPatch,
) -> list[str]:
    """Log the render's mutating filesystem steps in call order."""
    log: list[str] = []
    real_unlink = Path.unlink
    real_write_rendered_prompt = step1_research.write_rendered_prompt
    real_write_text = step1_research.write_text
    real_ensure_meta = step1_research.ensure_manual_output_metadata_template
    real_atomic_write_text = step1_research.atomic_write_text

    def fake_unlink(self: Path, *args: Any, **kwargs: Any) -> None:
        log.append(f"unlink:{self.name}")
        return real_unlink(self, *args, **kwargs)

    def fake_write_rendered_prompt(path: Any, text: str) -> Path:
        log.append(f"write_prompt:{Path(path).name}")
        return real_write_rendered_prompt(path, text)

    def fake_write_text(path: Any, text: str) -> Path:
        log.append(f"write_text:{Path(path).name}")
        return real_write_text(path, text)

    def fake_ensure_meta(path: Any, **kwargs: Any) -> Path:
        log.append(f"ensure_meta:{Path(path).name}")
        return real_ensure_meta(path, **kwargs)

    def fake_atomic_write_text(path: Any, text: str) -> Path:
        log.append(f"atomic_write:{Path(path).name}")
        return real_atomic_write_text(path, text)

    monkeypatch.setattr(Path, "unlink", fake_unlink)
    monkeypatch.setattr(step1_research, "write_rendered_prompt", fake_write_rendered_prompt)
    monkeypatch.setattr(step1_research, "write_text", fake_write_text)
    monkeypatch.setattr(
        step1_research, "ensure_manual_output_metadata_template", fake_ensure_meta
    )
    monkeypatch.setattr(step1_research, "atomic_write_text", fake_atomic_write_text)
    return log


def test_render_writes_the_commitment_last_in_the_exact_invalidation_order(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _setup_repo(tmp_path, monkeypatch)
    retired_commitment, retired_report = _plant_retired_v1_pair(tmp_path)
    _write(_report_path(tmp_path), '{"previous": true}\n')
    _plant_commitment(tmp_path, _commitment_text())
    log = _record_render_persistence(monkeypatch)

    step1_research.render_step1_prompt()

    assert log == [
        f"unlink:{RETIRED_REPORT_FILENAME}",
        f"unlink:{REPORT_FILENAME}",
        f"unlink:{RETIRED_COMMITMENT_FILENAME}",
        f"unlink:{COMMITMENT_FILENAME}",
        "write_prompt:prompt.txt",
        "write_text:raw_output.txt",
        "ensure_meta:raw_output.txt",
        f"atomic_write:{COMMITMENT_FILENAME}",
    ]
    # Every invalidation was a real deletion, and only the v2 leaf came back.
    assert not retired_commitment.exists()
    assert not retired_report.exists()
    assert not _report_path(tmp_path).exists()
    assert _commitment_path(tmp_path).exists()


def test_commitment_publication_observes_every_earlier_step_already_complete(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The durable completion point must not be reachable before the rest landed."""
    _setup_repo(tmp_path, monkeypatch)
    real_atomic_write_text = step1_research.atomic_write_text
    observed: dict[str, bool] = {}

    def observing_atomic_write_text(path: Any, text: str) -> Path:
        artifact_dir = _artifact_dir(tmp_path)
        observed["prompt"] = (artifact_dir / "prompt.txt").exists()
        observed["raw_output"] = (artifact_dir / "raw_output.txt").exists()
        observed["meta"] = (artifact_dir / "raw_output.meta.json").exists()
        return real_atomic_write_text(path, text)

    monkeypatch.setattr(step1_research, "atomic_write_text", observing_atomic_write_text)

    step1_research.render_step1_prompt()

    assert observed == {"prompt": True, "raw_output": True, "meta": True}


def test_rerender_invalidates_the_previous_commitment_and_report(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _setup_repo(tmp_path, monkeypatch)
    step1_research.render_step1_prompt()
    stale_report = _write(_report_path(tmp_path), '{"stale": true}\n')
    stale_commitment = _json(_commitment_path(tmp_path))

    _write(tmp_path / "inputs" / "current" / "strategy_settings.yaml", STRATEGY_TEXT_EDITED)
    _write(tmp_path / "inputs" / "current" / "portfolio_snapshot.txt", PORTFOLIO_TEXT_EDITED)
    captured = _capture_writer_prompt_text(monkeypatch)
    step1_research.render_step1_prompt()

    assert not stale_report.exists()
    refreshed = _json(_commitment_path(tmp_path))
    assert refreshed["strategy_settings_sha256"] == _sha(STRATEGY_TEXT_EDITED)
    assert refreshed["portfolio_snapshot_sha256"] == _sha(PORTFOLIO_TEXT_EDITED)
    # The prompt digest is re-derived from the NEW writer input, not carried over.
    assert len(captured) == 1
    assert refreshed["prompt_sha256"] == _sha(captured[0])
    assert refreshed["prompt_sha256"] != stale_commitment["prompt_sha256"]


# ---------------------------------------------------------------------------
# Migration failure states M1-M6
#
# Each starts from a complete prior run that ALSO carries the retired v1 pair
# (the pre-upgrade tree the migration must clean up), then edits a source so a
# successful rerender would necessarily change the prompt bytes. The shared
# invariant is at the bottom: no failure state may leave a valid commitment
# describing prompt bytes other than the ones actually on disk.
# ---------------------------------------------------------------------------


class _PriorRun:
    """The exact prior-run bytes an M-state must be judged against."""

    def __init__(self, *, prompt: str, commitment: str, retired_commitment: str) -> None:
        self.prompt = prompt
        self.commitment = commitment
        self.retired_commitment = retired_commitment


def _seed_prior_run_then_edit_source(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> _PriorRun:
    """Complete a v2 render over a pre-upgrade tree, then dirty a fixed source."""
    _setup_repo(tmp_path, monkeypatch)
    step1_research.render_step1_prompt()
    _write(_report_path(tmp_path), '{"previous": true}\n')
    retired_commitment, _retired_report = _plant_retired_v1_pair(tmp_path)
    prior = _PriorRun(
        prompt=(_artifact_dir(tmp_path) / "prompt.txt").read_text(encoding="utf-8"),
        commitment=_commitment_path(tmp_path).read_text(encoding="utf-8"),
        retired_commitment=retired_commitment.read_text(encoding="utf-8"),
    )
    _write(tmp_path / "inputs" / "current" / "strategy_settings.yaml", STRATEGY_TEXT_EDITED)
    return prior


def _assert_no_commitment_falsely_describes_the_prompt(tmp_path: Path) -> None:
    """The load-bearing migration invariant, asserted from disk state alone.

    Either no v2 commitment survives, or the one that does still describes the
    exact prompt bytes actually persisted. Nothing in between is admissible.
    """
    commitment_path = _commitment_path(tmp_path)
    if not commitment_path.exists():
        return
    persisted_prompt = (_artifact_dir(tmp_path) / "prompt.txt").read_bytes()
    assert (
        _json(commitment_path)["prompt_sha256"]
        == hashlib.sha256(persisted_prompt).hexdigest()
    )


def test_render_m1_retired_report_deletion_failure_mutates_nothing_afterward(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    prior = _seed_prior_run_then_edit_source(tmp_path, monkeypatch)
    _fail_unlink_of(monkeypatch, RETIRED_REPORT_FILENAME, PermissionError("v1 report locked"))

    with pytest.raises(PermissionError):
        step1_research.render_step1_prompt()

    # Nothing after the first deletion ran at all.
    assert _retired_report_path(tmp_path).exists()
    assert _report_path(tmp_path).exists()
    assert _retired_commitment_path(tmp_path).read_text(encoding="utf-8") == (
        prior.retired_commitment
    )
    assert _commitment_path(tmp_path).read_text(encoding="utf-8") == prior.commitment
    assert (_artifact_dir(tmp_path) / "prompt.txt").read_text(
        encoding="utf-8"
    ) == prior.prompt
    _assert_no_commitment_falsely_describes_the_prompt(tmp_path)


def test_render_m2_report_deletion_failure_leaves_prompt_and_commitments_intact(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    prior = _seed_prior_run_then_edit_source(tmp_path, monkeypatch)
    _fail_unlink_of(monkeypatch, REPORT_FILENAME, PermissionError("v2 report locked"))

    with pytest.raises(PermissionError):
        step1_research.render_step1_prompt()

    assert not _retired_report_path(tmp_path).exists()
    assert _retired_commitment_path(tmp_path).read_text(encoding="utf-8") == (
        prior.retired_commitment
    )
    assert _commitment_path(tmp_path).read_text(encoding="utf-8") == prior.commitment
    assert (_artifact_dir(tmp_path) / "prompt.txt").read_text(
        encoding="utf-8"
    ) == prior.prompt
    _assert_no_commitment_falsely_describes_the_prompt(tmp_path)


def test_render_m3_retired_commitment_deletion_failure_keeps_the_prompt(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    prior = _seed_prior_run_then_edit_source(tmp_path, monkeypatch)
    _fail_unlink_of(
        monkeypatch, RETIRED_COMMITMENT_FILENAME, PermissionError("v1 commitment locked")
    )

    with pytest.raises(PermissionError):
        step1_research.render_step1_prompt()

    assert not _retired_report_path(tmp_path).exists()
    assert not _report_path(tmp_path).exists()
    assert _commitment_path(tmp_path).read_text(encoding="utf-8") == prior.commitment
    assert (_artifact_dir(tmp_path) / "prompt.txt").read_text(
        encoding="utf-8"
    ) == prior.prompt
    _assert_no_commitment_falsely_describes_the_prompt(tmp_path)


def test_render_m4_commitment_deletion_failure_is_safe_because_the_prompt_is_unchanged(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    prior = _seed_prior_run_then_edit_source(tmp_path, monkeypatch)
    _fail_unlink_of(monkeypatch, COMMITMENT_FILENAME, PermissionError("v2 commitment locked"))

    with pytest.raises(PermissionError):
        step1_research.render_step1_prompt()

    assert not _retired_report_path(tmp_path).exists()
    assert not _report_path(tmp_path).exists()
    assert not _retired_commitment_path(tmp_path).exists()
    # The surviving commitment still describes the prompt that is still on disk.
    assert _commitment_path(tmp_path).read_text(encoding="utf-8") == prior.commitment
    assert (_artifact_dir(tmp_path) / "prompt.txt").read_text(
        encoding="utf-8"
    ) == prior.prompt
    _assert_no_commitment_falsely_describes_the_prompt(tmp_path)


def test_render_m5_prompt_write_failure_leaves_no_commitment_at_all(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _seed_prior_run_then_edit_source(tmp_path, monkeypatch)
    monkeypatch.setattr(step1_research, "write_rendered_prompt", _raise_oserror)

    with pytest.raises(OSError):
        step1_research.render_step1_prompt()

    assert not _retired_report_path(tmp_path).exists()
    assert not _report_path(tmp_path).exists()
    assert not _retired_commitment_path(tmp_path).exists()
    assert not _commitment_path(tmp_path).exists()
    _assert_no_commitment_falsely_describes_the_prompt(tmp_path)


def test_render_m6_commitment_publication_failure_publishes_no_commitment(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _seed_prior_run_then_edit_source(tmp_path, monkeypatch)
    monkeypatch.setattr(step1_research, "atomic_write_text", _raise_oserror)

    with pytest.raises(OSError):
        step1_research.render_step1_prompt()

    # The prompt may legally reflect the attempted render; the claim may not.
    assert "S1P_STRATEGY_EDITED" in (_artifact_dir(tmp_path) / "prompt.txt").read_text(
        encoding="utf-8"
    )
    assert not _commitment_path(tmp_path).exists()
    assert not (_artifact_dir(tmp_path) / f".{COMMITMENT_FILENAME}.tmp.{os.getpid()}").exists()
    _assert_no_commitment_falsely_describes_the_prompt(tmp_path)


def test_render_metadata_failure_leaves_no_commitment_and_keeps_raw_policy(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A post-invalidation failure between prompt and commitment behaves like M5/M6."""
    _setup_repo(tmp_path, monkeypatch)
    step1_research.render_step1_prompt()
    operator_raw = "OPERATOR_PASTED_RESPONSE\n"
    _write(_artifact_dir(tmp_path) / "raw_output.txt", operator_raw)

    _write(tmp_path / "inputs" / "current" / "strategy_settings.yaml", STRATEGY_TEXT_EDITED)
    monkeypatch.setattr(
        step1_research, "ensure_manual_output_metadata_template", _raise_oserror
    )

    with pytest.raises(OSError):
        step1_research.render_step1_prompt()

    assert not _commitment_path(tmp_path).exists()
    # The prompt may already be new; raw-output retention policy is untouched.
    assert "S1P_STRATEGY_EDITED" in (_artifact_dir(tmp_path) / "prompt.txt").read_text(
        encoding="utf-8"
    )
    assert (_artifact_dir(tmp_path) / "raw_output.txt").read_text(
        encoding="utf-8"
    ) == operator_raw
    _assert_no_commitment_falsely_describes_the_prompt(tmp_path)


def test_render_preserves_existing_raw_output_and_metadata_behavior(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _setup_repo(tmp_path, monkeypatch)

    first = step1_research.render_step1_prompt()

    assert set(first) == {
        "artifact_dir",
        "prompt_path",
        "raw_output_path",
        "raw_output_metadata_path",
        "prompt_template_path",
    }
    raw_output_path = Path(first["raw_output_path"])
    metadata_path = Path(first["raw_output_metadata_path"])
    assert raw_output_path.read_text(encoding="utf-8") == ""
    assert _json(metadata_path)["schema_version"] == "1.0"

    _write(raw_output_path, "OPERATOR_PASTED_RESPONSE\n")
    _write(metadata_path, json.dumps({"schema_version": "1.0", "model": "operator-filled"}))

    second = step1_research.render_step1_prompt()

    assert second == first
    assert raw_output_path.read_text(encoding="utf-8") == "OPERATOR_PASTED_RESPONSE\n"
    assert _json(metadata_path)["model"] == "operator-filled"


# ---------------------------------------------------------------------------
# Parse-adjacent continuity outcomes
# ---------------------------------------------------------------------------


def _rendered_then_edited(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    strategy_text: str | None = None,
    portfolio_text: str | None = None,
) -> None:
    """Render a commitment, then optionally replace either fixed CURRENT source."""
    _setup_repo(tmp_path, monkeypatch)
    step1_research.render_step1_prompt()
    if strategy_text is not None:
        _write(tmp_path / "inputs" / "current" / "strategy_settings.yaml", strategy_text)
    if portfolio_text is not None:
        _write(tmp_path / "inputs" / "current" / "portfolio_snapshot.txt", portfolio_text)


def test_unedited_sources_after_render_report_complete_match(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _rendered_then_edited(tmp_path, monkeypatch)

    assert _reason_codes(tmp_path) == []


def test_edited_strategy_source_reports_only_the_strategy_mismatch(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _rendered_then_edited(tmp_path, monkeypatch, strategy_text=STRATEGY_TEXT_EDITED)

    assert _reason_codes(tmp_path) == ["STRATEGY_SHA256_MISMATCH"]


def test_edited_portfolio_source_reports_only_the_portfolio_mismatch(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _rendered_then_edited(tmp_path, monkeypatch, portfolio_text=PORTFOLIO_TEXT_EDITED)

    assert _reason_codes(tmp_path) == ["PORTFOLIO_SHA256_MISMATCH"]


def test_both_sources_edited_report_both_mismatches_in_canonical_order(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _rendered_then_edited(
        tmp_path,
        monkeypatch,
        strategy_text=STRATEGY_TEXT_EDITED,
        portfolio_text=PORTFOLIO_TEXT_EDITED,
    )

    assert _reason_codes(tmp_path) == [
        "STRATEGY_SHA256_MISMATCH",
        "PORTFOLIO_SHA256_MISMATCH",
    ]


def test_a_whitespace_only_source_edit_is_still_a_mismatch(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Endpoint equality is byte equality, not semantic equivalence."""
    _rendered_then_edited(
        tmp_path, monkeypatch, portfolio_text=PORTFOLIO_TEXT.replace("\n", "\n\n")
    )

    assert _reason_codes(tmp_path) == ["PORTFOLIO_SHA256_MISMATCH"]


def test_unreadable_strategy_source_reports_a_read_failure_without_a_mismatch(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _rendered_then_edited(tmp_path, monkeypatch)
    strategy_path = tmp_path / "inputs" / "current" / "strategy_settings.yaml"
    strategy_path.unlink()
    strategy_path.mkdir()

    assert _reason_codes(tmp_path) == ["STRATEGY_CURRENT_SOURCE_READ_FAILED"]


def test_missing_portfolio_source_reports_only_a_read_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _rendered_then_edited(tmp_path, monkeypatch)
    (tmp_path / "inputs" / "current" / "portfolio_snapshot.txt").unlink()

    assert _reason_codes(tmp_path) == ["PORTFOLIO_CURRENT_SOURCE_READ_FAILED"]


def test_both_roles_are_attempted_independently_in_canonical_order(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An unreadable strategy source must not hide a readable portfolio mismatch."""
    _rendered_then_edited(tmp_path, monkeypatch, portfolio_text=PORTFOLIO_TEXT_EDITED)
    (tmp_path / "inputs" / "current" / "strategy_settings.yaml").unlink()

    assert _reason_codes(tmp_path) == [
        "STRATEGY_CURRENT_SOURCE_READ_FAILED",
        "PORTFOLIO_SHA256_MISMATCH",
    ]


def test_invalid_utf8_source_bytes_are_hashable_and_not_a_read_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _setup_repo(tmp_path, monkeypatch)
    portfolio_path = tmp_path / "inputs" / "current" / "portfolio_snapshot.txt"
    portfolio_path.write_bytes(b"\xff\xfe not utf-8 \x00")
    _plant_commitment(
        tmp_path,
        _commitment_text(
            portfolio_snapshot_sha256=hashlib.sha256(
                b"\xff\xfe not utf-8 \x00"
            ).hexdigest()
        ),
    )

    assert _reason_codes(tmp_path) == []


def test_prompt_endpoint_matches_the_committed_writer_input_after_render(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """On this platform the persisted prompt bytes equal the committed input."""
    _setup_repo(tmp_path, monkeypatch)
    captured = _capture_writer_prompt_text(monkeypatch)
    step1_research.render_step1_prompt()

    persisted = (_artifact_dir(tmp_path) / "prompt.txt").read_bytes()
    assert persisted == captured[0].encode("utf-8")
    assert _reason_codes(tmp_path) == []


def test_edited_prompt_artifact_reports_only_the_prompt_mismatch(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _rendered_then_edited(tmp_path, monkeypatch)
    _write(_artifact_dir(tmp_path) / "prompt.txt", PROMPT_TEXT_EDITED)

    assert _reason_codes(tmp_path) == ["PROMPT_SHA256_MISMATCH"]


def test_a_single_appended_byte_in_the_prompt_is_a_mismatch(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Prompt equality is byte equality; the sources are untouched."""
    _rendered_then_edited(tmp_path, monkeypatch)
    prompt_path = _artifact_dir(tmp_path) / "prompt.txt"
    prompt_path.write_bytes(prompt_path.read_bytes() + b"\n")

    assert _reason_codes(tmp_path) == ["PROMPT_SHA256_MISMATCH"]


def test_missing_prompt_artifact_reports_a_read_failure_without_a_mismatch(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _rendered_then_edited(tmp_path, monkeypatch)
    (_artifact_dir(tmp_path) / "prompt.txt").unlink()

    assert _reason_codes(tmp_path) == ["PROMPT_CURRENT_ARTIFACT_READ_FAILED"]


def test_unreadable_prompt_artifact_reports_a_read_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _rendered_then_edited(tmp_path, monkeypatch)
    prompt_path = _artifact_dir(tmp_path) / "prompt.txt"
    prompt_path.unlink()
    prompt_path.mkdir()

    assert _reason_codes(tmp_path) == ["PROMPT_CURRENT_ARTIFACT_READ_FAILED"]


def test_all_three_endpoints_failing_report_reads_before_mismatches(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Canonical order is the three read failures, then the three mismatches."""
    _rendered_then_edited(tmp_path, monkeypatch)
    (tmp_path / "inputs" / "current" / "strategy_settings.yaml").unlink()
    (tmp_path / "inputs" / "current" / "portfolio_snapshot.txt").unlink()
    (_artifact_dir(tmp_path) / "prompt.txt").unlink()

    assert _reason_codes(tmp_path) == [
        "STRATEGY_CURRENT_SOURCE_READ_FAILED",
        "PORTFOLIO_CURRENT_SOURCE_READ_FAILED",
        "PROMPT_CURRENT_ARTIFACT_READ_FAILED",
    ]


def test_all_three_endpoints_changed_report_mismatches_in_canonical_order(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _rendered_then_edited(
        tmp_path,
        monkeypatch,
        strategy_text=STRATEGY_TEXT_EDITED,
        portfolio_text=PORTFOLIO_TEXT_EDITED,
    )
    _write(_artifact_dir(tmp_path) / "prompt.txt", PROMPT_TEXT_EDITED)

    assert _reason_codes(tmp_path) == [
        "STRATEGY_SHA256_MISMATCH",
        "PORTFOLIO_SHA256_MISMATCH",
        "PROMPT_SHA256_MISMATCH",
    ]


def test_read_failures_and_mismatches_interleave_in_the_closed_six_role_order(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A read failure in one role never suppresses another role's mismatch."""
    _rendered_then_edited(tmp_path, monkeypatch, portfolio_text=PORTFOLIO_TEXT_EDITED)
    (tmp_path / "inputs" / "current" / "strategy_settings.yaml").unlink()
    _write(_artifact_dir(tmp_path) / "prompt.txt", PROMPT_TEXT_EDITED)

    assert _reason_codes(tmp_path) == [
        "STRATEGY_CURRENT_SOURCE_READ_FAILED",
        "PORTFOLIO_SHA256_MISMATCH",
        "PROMPT_SHA256_MISMATCH",
    ]


def test_a_commitment_failure_is_a_singleton_and_suppresses_source_reads(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _setup_repo(tmp_path, monkeypatch, strategy_text=STRATEGY_TEXT_EDITED)
    (tmp_path / "inputs" / "current" / "portfolio_snapshot.txt").unlink()
    _plant_commitment(tmp_path, "{not json")
    endpoint_reads = _record_endpoint_reads(tmp_path, monkeypatch)

    assert _reason_codes(tmp_path) == ["RENDER_COMMITMENT_INVALID_JSON"]
    # No endpoint is read at all — including the prompt.
    assert endpoint_reads == []


def test_each_valid_evaluation_reads_each_fixed_source_exactly_once(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _rendered_then_edited(tmp_path, monkeypatch)
    endpoint_reads = _record_endpoint_reads(tmp_path, monkeypatch)

    assert _reason_codes(tmp_path) == []
    assert endpoint_reads == _canonical_endpoint_paths(tmp_path)


def test_report_shape_and_authority_effect_are_exact_in_both_statuses(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _rendered_then_edited(tmp_path, monkeypatch)
    step1_research._write_step1_render_continuity_report()  # noqa: SLF001
    matched = _json(_report_path(tmp_path))

    _write(tmp_path / "inputs" / "current" / "strategy_settings.yaml", STRATEGY_TEXT_EDITED)
    step1_research._write_step1_render_continuity_report()  # noqa: SLF001
    unverified = _json(_report_path(tmp_path))

    assert matched == {
        "schema_version": REPORT_SCHEMA_VERSION,
        "status": COMPLETE_MATCH,
        "reason_codes": [],
        "authority_effect": "NONE",
    }
    assert unverified == {
        "schema_version": REPORT_SCHEMA_VERSION,
        "status": UNVERIFIED,
        "reason_codes": ["STRATEGY_SHA256_MISMATCH"],
        "authority_effect": "NONE",
    }
    assert _report_path(tmp_path).read_text(encoding="utf-8").endswith("}\n")


# ---------------------------------------------------------------------------
# Parse integration and failure policy
# ---------------------------------------------------------------------------


def _seed_parseable_run(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _setup_repo(tmp_path, monkeypatch)
    step1_research.render_step1_prompt()
    _write(
        _artifact_dir(tmp_path) / "raw_output.txt",
        (FIXTURE_DIR / "current_step1_raw_output_minimal.txt").read_text(encoding="utf-8"),
    )


def test_parse_refreshes_the_continuity_report_and_still_parses(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _seed_parseable_run(tmp_path, monkeypatch)

    result = step1_research.parse_step1_output()

    assert Path(result["research_output_path"]).exists()
    assert _json(_report_path(tmp_path)) == {
        "schema_version": REPORT_SCHEMA_VERSION,
        "status": COMPLETE_MATCH,
        "reason_codes": [],
        "authority_effect": "NONE",
    }


def test_continuity_report_is_written_before_the_legacy_parse_begins(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Failing the first Legacy parse step must not prevent fresh evidence."""
    _seed_parseable_run(tmp_path, monkeypatch)
    monkeypatch.setattr(
        step1_research,
        "load_strategy_settings_for_handoff_validation",
        lambda: (_ for _ in ()).throw(RuntimeError("legacy parse reached")),
    )

    with pytest.raises(RuntimeError, match="legacy parse reached"):
        step1_research.parse_step1_output()

    assert _json(_report_path(tmp_path))["status"] == COMPLETE_MATCH


def test_expected_continuity_failure_does_not_suppress_the_legacy_parse(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _seed_parseable_run(tmp_path, monkeypatch)
    _commitment_path(tmp_path).unlink()

    result = step1_research.parse_step1_output()

    assert Path(result["research_output_path"]).exists()
    report = _json(_report_path(tmp_path))
    assert report["status"] == UNVERIFIED
    assert report["reason_codes"] == ["RENDER_COMMITMENT_MISSING"]
    assert report["authority_effect"] == "NONE"


@pytest.mark.parametrize(
    "locked_filename",
    [
        pytest.param(RETIRED_REPORT_FILENAME, id="retired-v1-report"),
        pytest.param(REPORT_FILENAME, id="current-v2-report"),
    ],
)
def test_stale_report_deletion_failure_propagates_before_any_legacy_parse(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    locked_filename: str,
) -> None:
    """Continuing while ANY previous success report can survive is not allowed."""
    _seed_parseable_run(tmp_path, monkeypatch)
    _plant_retired_v1_pair(tmp_path)
    _write(_report_path(tmp_path), '{"stale": true}\n')
    research_output = _artifact_dir(tmp_path) / "research_output.json"
    assert not research_output.exists()
    _fail_unlink_of(monkeypatch, locked_filename, PermissionError("report locked"))

    with pytest.raises(PermissionError):
        step1_research.parse_step1_output()

    assert (_artifact_dir(tmp_path) / locked_filename).exists()
    assert not research_output.exists()


def test_parse_deletes_exactly_both_reports_in_the_closed_order(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Retired v1 report first, current v2 report second — and no commitment."""
    _seed_parseable_run(tmp_path, monkeypatch)
    retired_commitment, retired_report = _plant_retired_v1_pair(tmp_path)
    _write(_report_path(tmp_path), '{"stale": true}\n')

    real_unlink = Path.unlink
    watched = {
        RETIRED_REPORT_FILENAME,
        REPORT_FILENAME,
        RETIRED_COMMITMENT_FILENAME,
        COMMITMENT_FILENAME,
    }
    unlinked: list[str] = []

    def logging_unlink(self: Path, *args: Any, **kwargs: Any) -> None:
        if self.name in watched:
            unlinked.append(self.name)
        return real_unlink(self, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", logging_unlink)

    step1_research.parse_step1_output()

    assert unlinked == [RETIRED_REPORT_FILENAME, REPORT_FILENAME]
    assert not retired_report.exists()
    # v1 commitment cleanup belongs to render alone; parse must not touch it.
    assert retired_commitment.exists()
    assert _commitment_path(tmp_path).exists()


def test_upgrade_before_first_v2_render_reports_missing_and_keeps_parsing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A pre-upgrade tree holds only a v1 commitment, which is never consumed.

    The v1 artifact records source identity alone, so inferring prompt identity
    from it is impossible; the only honest outcome is an absent v2 commitment.
    """
    _seed_parseable_run(tmp_path, monkeypatch)
    _commitment_path(tmp_path).unlink()
    retired_commitment, retired_report = _plant_retired_v1_pair(tmp_path)

    result = step1_research.parse_step1_output()

    assert Path(result["research_output_path"]).exists()
    assert _json(_report_path(tmp_path)) == {
        "schema_version": REPORT_SCHEMA_VERSION,
        "status": UNVERIFIED,
        "reason_codes": ["RENDER_COMMITMENT_MISSING"],
        "authority_effect": "NONE",
    }
    # The stale v1 success report is gone; the v1 commitment is left for render.
    assert not retired_report.exists()
    assert retired_commitment.read_text(encoding="utf-8").count(
        RETIRED_COMMITMENT_SCHEMA_VERSION
    ) == 1


def test_a_v1_commitment_planted_at_the_v2_path_is_unsupported_not_consumed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Even at the v2 filename, a v1 payload is a version failure, not a match."""
    _setup_repo(tmp_path, monkeypatch)
    _plant_commitment(
        tmp_path,
        json.dumps(
            {
                "schema_version": RETIRED_COMMITMENT_SCHEMA_VERSION,
                "strategy_settings_sha256": _sha(STRATEGY_TEXT),
                "portfolio_snapshot_sha256": _sha(PORTFOLIO_TEXT),
            }
        ),
    )

    assert _reason_codes(tmp_path) == ["RENDER_COMMITMENT_UNSUPPORTED_SCHEMA_VERSION"]


def test_report_write_oserror_continues_the_legacy_parse_without_stale_evidence(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _seed_parseable_run(tmp_path, monkeypatch)
    _write(_report_path(tmp_path), '{"stale": true}\n')
    monkeypatch.setattr(step1_research, "atomic_write_text", _raise_oserror)

    result = step1_research.parse_step1_output()

    assert Path(result["research_output_path"]).exists()
    assert not _report_path(tmp_path).exists()


def test_unexpected_checker_error_propagates_instead_of_being_reported(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Report-only status is not permission to hide a true code defect."""
    _seed_parseable_run(tmp_path, monkeypatch)

    def broken_source_read(_path: Path) -> bytes:
        raise TypeError("checker programming bug")

    monkeypatch.setattr(
        step1_research, "_read_current_endpoint_bytes_for_continuity", broken_source_read
    )
    research_output = _artifact_dir(tmp_path) / "research_output.json"

    with pytest.raises(TypeError, match="checker programming bug"):
        step1_research.parse_step1_output()

    assert not research_output.exists()
    assert not _report_path(tmp_path).exists()


# ---------------------------------------------------------------------------
# Fresh-process behavior
# ---------------------------------------------------------------------------


_FRESH_PROCESS_DRIVER = """\
import sys
from pathlib import Path

from investment_orchestrator.workflow import step1_research

step1_research.repo_root = lambda: Path(sys.argv[1])
step1_research._write_step1_render_continuity_report()
"""


def _run_continuity_checker_in_fresh_process(tmp_path: Path) -> None:
    src_root = Path(step1_research.__file__).resolve().parents[2]
    completed = subprocess.run(
        [sys.executable, "-c", _FRESH_PROCESS_DRIVER, str(tmp_path)],
        env={**os.environ, "PYTHONPATH": str(src_root)},
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, completed.stderr


def test_a_fresh_process_verifies_continuity_from_the_filesystem_alone(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The checker needs no render-process object, global context, or cache.

    The render happens in this process; the verification happens in a brand-new
    interpreter whose only link to it is the committed file on disk.
    """
    _setup_repo(tmp_path, monkeypatch)
    step1_research.render_step1_prompt()
    _report_path(tmp_path).unlink(missing_ok=True)

    _run_continuity_checker_in_fresh_process(tmp_path)

    assert _json(_report_path(tmp_path)) == {
        "schema_version": REPORT_SCHEMA_VERSION,
        "status": COMPLETE_MATCH,
        "reason_codes": [],
        "authority_effect": "NONE",
    }

    _write(tmp_path / "inputs" / "current" / "portfolio_snapshot.txt", PORTFOLIO_TEXT_EDITED)
    _run_continuity_checker_in_fresh_process(tmp_path)

    assert _json(_report_path(tmp_path)) == {
        "schema_version": REPORT_SCHEMA_VERSION,
        "status": UNVERIFIED,
        "reason_codes": ["PORTFOLIO_SHA256_MISMATCH"],
        "authority_effect": "NONE",
    }


# ---------------------------------------------------------------------------
# Authority isolation and artifact lifecycle
# ---------------------------------------------------------------------------


def test_step1_research_is_the_only_production_module_naming_any_leaf() -> None:
    """Nothing may consume any leaf for availability, gates, or any decision.

    The retired v1 names are scanned alongside the current v2 names so the
    migration cannot quietly leave a second module still reading either one.
    """
    production_root = repo_root() / "src" / "investment_orchestrator"
    owner = production_root / "workflow" / "step1_research.py"
    leaves = (
        "render_commitment.json",
        "render_continuity_report.json",
        "render_source_commitment.json",
        "render_source_continuity_report.json",
    )
    consumers = sorted(
        str(path.relative_to(repo_root()))
        for path in production_root.rglob("*.py")
        if path != owner
        and "step2" not in path.name
        and any(leaf in path.read_text(encoding="utf-8") for leaf in leaves)
    )

    assert consumers == []


def test_the_owning_module_only_ever_deletes_the_retired_v1_leaves() -> None:
    """The v1 names must survive in production as cleanup targets, nothing more.

    A v1 path helper feeding anything except ``unlink`` would mean current code
    had started reading or writing a retired artifact again.
    """
    owner = repo_root() / "src" / "investment_orchestrator" / "workflow" / "step1_research.py"
    tree = ast.parse(owner.read_text(encoding="utf-8"))
    retired_helpers = {
        "_step1_retired_render_source_commitment_path",
        "_step1_retired_render_source_continuity_report_path",
    }
    retired_locals: set[str] = set()
    uses: list[str] = []

    for node in ast.walk(tree):
        # `x = _step1_retired_..._path()` binds a local we then follow.
        if isinstance(node, ast.Assign) and isinstance(node.value, ast.Call):
            func = node.value.func
            if isinstance(func, ast.Name) and func.id in retired_helpers:
                for target in node.targets:
                    if isinstance(target, ast.Name):
                        retired_locals.add(target.id)
        # Every attribute reached through a retired local must be `unlink`.
        if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name):
            if node.value.id in retired_locals:
                uses.append(node.attr)

    assert retired_locals  # the helpers are actually exercised
    assert set(uses) == {"unlink"}, uses


def test_parse_result_and_render_result_do_not_expose_either_leaf(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _seed_parseable_run(tmp_path, monkeypatch)
    render_result = step1_research.render_step1_prompt()
    _write(
        _artifact_dir(tmp_path) / "raw_output.txt",
        (FIXTURE_DIR / "current_step1_raw_output_minimal.txt").read_text(encoding="utf-8"),
    )
    parse_result = step1_research.parse_step1_output()

    for value in (*render_result.values(), *parse_result.values()):
        assert COMMITMENT_FILENAME not in value
        assert REPORT_FILENAME not in value


def test_existing_whole_tree_archive_and_clear_carry_both_leaves_naturally(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No artifact-management inventory needs to learn about these files."""
    _setup_repo(tmp_path, monkeypatch)
    step1_research.render_step1_prompt()
    step1_research._write_step1_render_continuity_report()  # noqa: SLF001
    # A pre-upgrade pair that arrived after the render archives verbatim too.
    retired_commitment, retired_report = _plant_retired_v1_pair(tmp_path)
    assert _commitment_path(tmp_path).exists()
    assert _report_path(tmp_path).exists()
    retired_commitment_text = retired_commitment.read_text(encoding="utf-8")

    archived = archive_current_artifacts(root=tmp_path, label="s1p_archive")

    assert archived is not None
    assert (archived / "step1_research" / COMMITMENT_FILENAME).exists()
    assert (archived / "step1_research" / REPORT_FILENAME).exists()
    # Archived v1 artifacts are preserved as v1 — never migrated or rewritten.
    assert (archived / "step1_research" / RETIRED_COMMITMENT_FILENAME).read_text(
        encoding="utf-8"
    ) == retired_commitment_text
    assert (archived / "step1_research" / RETIRED_REPORT_FILENAME).exists()
    assert not retired_commitment.exists()
    assert not retired_report.exists()
    assert not _commitment_path(tmp_path).exists()
    assert not _report_path(tmp_path).exists()

    step1_research.render_step1_prompt()
    step1_research._write_step1_render_continuity_report()  # noqa: SLF001
    clear_current_artifacts(root=tmp_path)

    assert not _commitment_path(tmp_path).exists()
    assert not _report_path(tmp_path).exists()
    assert (archived / "step1_research" / COMMITMENT_FILENAME).exists()
