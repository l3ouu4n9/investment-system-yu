"""Closed-contract tests for Step 1 render-source commitment + continuity report.

The producer records, at render time, the SHA-256 of the exact strategy and
portfolio buffers it compiled into ``prompt.txt``. The consumer runs first in
every parse and reports whether the two fixed CURRENT sources still hash to
those values. Both files are report-only: nothing reads them as authority.

These tests own the closed contract (schema, reason vocabulary, ordering,
persistence ordering, and failure semantics). They deliberately do not
re-prove the generic temp/fsync/replace internals of ``atomic_write_text``,
which ``tests/unit/test_common_io.py`` already owns.
"""

from __future__ import annotations

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


COMMITMENT_FILENAME = "render_source_commitment.json"
REPORT_FILENAME = "render_source_continuity_report.json"
COMMITMENT_SCHEMA_VERSION = "step1_render_source_commitment_v1"
REPORT_SCHEMA_VERSION = "step1_render_source_continuity_report_v1"

COMPLETE_MATCH = "SOURCE_ENDPOINT_COMPLETE_MATCH"
UNVERIFIED = "SOURCE_ENDPOINT_UNVERIFIED"

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
) -> Path:
    """Point Step 1 at an isolated repo root holding both fixed CURRENT sources."""
    monkeypatch.setattr(step1_research, "repo_root", lambda: tmp_path)
    _write(tmp_path / "inputs" / "current" / "strategy_settings.yaml", strategy_text)
    _write(tmp_path / "inputs" / "current" / "portfolio_snapshot.txt", portfolio_text)
    return tmp_path


def _artifact_dir(tmp_path: Path) -> Path:
    return tmp_path / "artifacts" / "current" / "step1_research"


def _commitment_path(tmp_path: Path) -> Path:
    return _artifact_dir(tmp_path) / COMMITMENT_FILENAME


def _report_path(tmp_path: Path) -> Path:
    return _artifact_dir(tmp_path) / REPORT_FILENAME


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
    }
    payload.update(overrides)
    return json.dumps(payload, ensure_ascii=False, indent=2) + "\n"


def _plant_commitment(tmp_path: Path, text: str) -> Path:
    return _write(_commitment_path(tmp_path), text)


def _reason_codes(tmp_path: Path) -> list[str]:
    """Evaluate continuity through the owning report writer and read the result."""
    step1_research._write_step1_render_source_continuity_report()  # noqa: SLF001
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


# ---------------------------------------------------------------------------
# Commitment contract (§ closed schema / validation precedence)
# ---------------------------------------------------------------------------


def test_producer_writes_exactly_the_closed_commitment_contract(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _setup_repo(tmp_path, monkeypatch)

    step1_research.render_step1_prompt()

    raw = _commitment_path(tmp_path).read_text(encoding="utf-8")
    commitment = json.loads(raw)
    assert set(commitment) == {
        "schema_version",
        "strategy_settings_sha256",
        "portfolio_snapshot_sha256",
    }
    assert commitment["schema_version"] == COMMITMENT_SCHEMA_VERSION
    assert commitment["strategy_settings_sha256"] == _sha(STRATEGY_TEXT)
    assert commitment["portfolio_snapshot_sha256"] == _sha(PORTFOLIO_TEXT)
    for digest in (
        commitment["strategy_settings_sha256"],
        commitment["portfolio_snapshot_sha256"],
    ):
        assert len(digest) == 64
        assert digest == digest.lower()
        assert set(digest) <= set("0123456789abcdef")
    # Deterministic readable serialization, terminal newline, no ASCII escaping.
    assert raw == json.dumps(commitment, ensure_ascii=False, indent=2) + "\n"


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

    assert _reason_codes(tmp_path) == ["SOURCE_COMMITMENT_INVALID_CONTRACT"]


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

    assert _reason_codes(tmp_path) == ["SOURCE_COMMITMENT_INVALID_JSON"]


def test_wrong_string_schema_version_is_classified_as_unsupported(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _setup_repo(tmp_path, monkeypatch)
    _plant_commitment(
        tmp_path, _commitment_text(schema_version="step1_render_source_commitment_v2")
    )

    assert _reason_codes(tmp_path) == ["SOURCE_COMMITMENT_UNSUPPORTED_SCHEMA_VERSION"]


def test_unsupported_version_is_not_swallowed_by_v1_field_validation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A future version legitimately carries a different field set.

    Reporting that as a generic contract violation would hide the real reason
    an operator must act on, so the v1 field rules only run once the version is
    confirmed to be v1.
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

    assert _reason_codes(tmp_path) == ["SOURCE_COMMITMENT_UNSUPPORTED_SCHEMA_VERSION"]


def test_absent_commitment_is_missing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _setup_repo(tmp_path, monkeypatch)

    assert _reason_codes(tmp_path) == ["SOURCE_COMMITMENT_MISSING"]


def test_broken_commitment_symlink_is_missing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _setup_repo(tmp_path, monkeypatch)
    _artifact_dir(tmp_path).mkdir(parents=True, exist_ok=True)
    _commitment_path(tmp_path).symlink_to(tmp_path / "nonexistent-commitment.json")

    assert _reason_codes(tmp_path) == ["SOURCE_COMMITMENT_MISSING"]


def test_directory_in_place_of_commitment_is_unreadable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _setup_repo(tmp_path, monkeypatch)
    _commitment_path(tmp_path).mkdir(parents=True)

    assert _reason_codes(tmp_path) == ["SOURCE_COMMITMENT_UNREADABLE"]


def test_invalid_utf8_commitment_is_unreadable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _setup_repo(tmp_path, monkeypatch)
    _artifact_dir(tmp_path).mkdir(parents=True, exist_ok=True)
    _commitment_path(tmp_path).write_bytes(b'{"schema_version": "\xff"}')

    assert _reason_codes(tmp_path) == ["SOURCE_COMMITMENT_UNREADABLE"]


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
    log = _record_render_persistence(monkeypatch)

    step1_research.render_step1_prompt()

    assert log == [
        f"unlink:{REPORT_FILENAME}",
        f"unlink:{COMMITMENT_FILENAME}",
        "write_prompt:prompt.txt",
        "write_text:raw_output.txt",
        "ensure_meta:raw_output.txt",
        f"atomic_write:{COMMITMENT_FILENAME}",
    ]


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
    stale_commitment = _commitment_path(tmp_path).read_text(encoding="utf-8")

    _write(tmp_path / "inputs" / "current" / "strategy_settings.yaml", STRATEGY_TEXT_EDITED)
    _write(tmp_path / "inputs" / "current" / "portfolio_snapshot.txt", PORTFOLIO_TEXT_EDITED)
    step1_research.render_step1_prompt()

    assert not stale_report.exists()
    refreshed = _json(_commitment_path(tmp_path))
    assert _commitment_path(tmp_path).read_text(encoding="utf-8") != stale_commitment
    assert refreshed["strategy_settings_sha256"] == _sha(STRATEGY_TEXT_EDITED)
    assert refreshed["portfolio_snapshot_sha256"] == _sha(PORTFOLIO_TEXT_EDITED)


def test_render_f1_report_deletion_failure_preserves_the_whole_previous_run(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _setup_repo(tmp_path, monkeypatch)
    step1_research.render_step1_prompt()
    old_prompt = (_artifact_dir(tmp_path) / "prompt.txt").read_text(encoding="utf-8")
    old_commitment = _commitment_path(tmp_path).read_text(encoding="utf-8")
    old_report = _write(_report_path(tmp_path), '{"previous": true}\n').read_text(
        encoding="utf-8"
    )

    _write(tmp_path / "inputs" / "current" / "strategy_settings.yaml", STRATEGY_TEXT_EDITED)
    _fail_unlink_of(monkeypatch, REPORT_FILENAME, PermissionError("report locked"))

    with pytest.raises(PermissionError):
        step1_research.render_step1_prompt()

    assert (_artifact_dir(tmp_path) / "prompt.txt").read_text(encoding="utf-8") == old_prompt
    assert _commitment_path(tmp_path).read_text(encoding="utf-8") == old_commitment
    assert _report_path(tmp_path).read_text(encoding="utf-8") == old_report


def test_render_f2_commitment_deletion_failure_leaves_the_old_prompt_and_commitment(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _setup_repo(tmp_path, monkeypatch)
    step1_research.render_step1_prompt()
    old_prompt = (_artifact_dir(tmp_path) / "prompt.txt").read_text(encoding="utf-8")
    old_commitment = _commitment_path(tmp_path).read_text(encoding="utf-8")
    _write(_report_path(tmp_path), '{"previous": true}\n')

    _write(tmp_path / "inputs" / "current" / "strategy_settings.yaml", STRATEGY_TEXT_EDITED)
    _fail_unlink_of(monkeypatch, COMMITMENT_FILENAME, PermissionError("commitment locked"))

    with pytest.raises(PermissionError):
        step1_research.render_step1_prompt()

    # The report deletion already succeeded, so no stale evidence survives.
    assert not _report_path(tmp_path).exists()
    assert (_artifact_dir(tmp_path) / "prompt.txt").read_text(encoding="utf-8") == old_prompt
    assert _commitment_path(tmp_path).read_text(encoding="utf-8") == old_commitment


def test_render_f3_prompt_write_failure_leaves_no_commitment_to_claim_the_prompt(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _setup_repo(tmp_path, monkeypatch)
    step1_research.render_step1_prompt()
    _write(_report_path(tmp_path), '{"previous": true}\n')

    _write(tmp_path / "inputs" / "current" / "strategy_settings.yaml", STRATEGY_TEXT_EDITED)
    monkeypatch.setattr(step1_research, "write_rendered_prompt", _raise_oserror)

    with pytest.raises(OSError):
        step1_research.render_step1_prompt()

    assert not _report_path(tmp_path).exists()
    assert not _commitment_path(tmp_path).exists()


def test_render_f4_metadata_failure_leaves_no_commitment_and_keeps_raw_policy(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
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


def test_render_f5_commitment_write_failure_leaves_no_commitment(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _setup_repo(tmp_path, monkeypatch)
    step1_research.render_step1_prompt()

    _write(tmp_path / "inputs" / "current" / "strategy_settings.yaml", STRATEGY_TEXT_EDITED)
    monkeypatch.setattr(step1_research, "atomic_write_text", _raise_oserror)

    with pytest.raises(OSError):
        step1_research.render_step1_prompt()

    assert not _commitment_path(tmp_path).exists()
    assert "S1P_STRATEGY_EDITED" in (_artifact_dir(tmp_path) / "prompt.txt").read_text(
        encoding="utf-8"
    )
    assert not (_artifact_dir(tmp_path) / f".{COMMITMENT_FILENAME}.tmp.{os.getpid()}").exists()


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


def test_a_commitment_failure_is_a_singleton_and_suppresses_source_reads(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _setup_repo(tmp_path, monkeypatch, strategy_text=STRATEGY_TEXT_EDITED)
    (tmp_path / "inputs" / "current" / "portfolio_snapshot.txt").unlink()
    _plant_commitment(tmp_path, "{not json")

    real_read_bytes = Path.read_bytes
    source_reads: list[Path] = []

    def counting_read_bytes(self: Path) -> bytes:
        if self.parent == tmp_path / "inputs" / "current":
            source_reads.append(self)
        return real_read_bytes(self)

    monkeypatch.setattr(Path, "read_bytes", counting_read_bytes)

    assert _reason_codes(tmp_path) == ["SOURCE_COMMITMENT_INVALID_JSON"]
    assert source_reads == []


def test_each_valid_evaluation_reads_each_fixed_source_exactly_once(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _rendered_then_edited(tmp_path, monkeypatch)
    real_read_bytes = Path.read_bytes
    source_reads: list[Path] = []

    def counting_read_bytes(self: Path) -> bytes:
        if self.parent == tmp_path / "inputs" / "current":
            source_reads.append(self)
        return real_read_bytes(self)

    monkeypatch.setattr(Path, "read_bytes", counting_read_bytes)

    assert _reason_codes(tmp_path) == []
    assert source_reads == [
        tmp_path / "inputs" / "current" / "strategy_settings.yaml",
        tmp_path / "inputs" / "current" / "portfolio_snapshot.txt",
    ]


def test_report_shape_and_authority_effect_are_exact_in_both_statuses(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _rendered_then_edited(tmp_path, monkeypatch)
    step1_research._write_step1_render_source_continuity_report()  # noqa: SLF001
    matched = _json(_report_path(tmp_path))

    _write(tmp_path / "inputs" / "current" / "strategy_settings.yaml", STRATEGY_TEXT_EDITED)
    step1_research._write_step1_render_source_continuity_report()  # noqa: SLF001
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
    assert report["reason_codes"] == ["SOURCE_COMMITMENT_MISSING"]
    assert report["authority_effect"] == "NONE"


def test_stale_report_deletion_failure_propagates_before_any_legacy_parse(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Continuing with a previous parse's report still on disk is not allowed."""
    _seed_parseable_run(tmp_path, monkeypatch)
    stale = _write(_report_path(tmp_path), '{"stale": true}\n')
    research_output = _artifact_dir(tmp_path) / "research_output.json"
    assert not research_output.exists()
    _fail_unlink_of(monkeypatch, REPORT_FILENAME, PermissionError("report locked"))

    with pytest.raises(PermissionError):
        step1_research.parse_step1_output()

    assert stale.read_text(encoding="utf-8") == '{"stale": true}\n'
    assert not research_output.exists()


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
        step1_research, "_read_current_source_bytes_for_continuity", broken_source_read
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
step1_research._write_step1_render_source_continuity_report()
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


def test_step1_research_is_the_only_production_module_naming_either_leaf() -> None:
    """Nothing may consume either file for availability, gates, or any decision."""
    production_root = repo_root() / "src" / "investment_orchestrator"
    owner = production_root / "workflow" / "step1_research.py"
    consumers = sorted(
        str(path.relative_to(repo_root()))
        for path in production_root.rglob("*.py")
        if path != owner
        and any(
            leaf in path.read_text(encoding="utf-8")
            for leaf in ("render_source_commitment", "render_source_continuity_report")
        )
    )

    assert consumers == []


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
    step1_research._write_step1_render_source_continuity_report()  # noqa: SLF001
    assert _commitment_path(tmp_path).exists()
    assert _report_path(tmp_path).exists()

    archived = archive_current_artifacts(root=tmp_path, label="s1p_archive")

    assert archived is not None
    assert (archived / "step1_research" / COMMITMENT_FILENAME).exists()
    assert (archived / "step1_research" / REPORT_FILENAME).exists()
    assert not _commitment_path(tmp_path).exists()
    assert not _report_path(tmp_path).exists()

    step1_research.render_step1_prompt()
    step1_research._write_step1_render_source_continuity_report()  # noqa: SLF001
    clear_current_artifacts(root=tmp_path)

    assert not _commitment_path(tmp_path).exists()
    assert not _report_path(tmp_path).exists()
    assert (archived / "step1_research" / COMMITMENT_FILENAME).exists()
