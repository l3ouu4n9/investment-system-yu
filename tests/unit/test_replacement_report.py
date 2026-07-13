"""R2F-1b-b immutable single-file validated-memo publication tests."""

from __future__ import annotations

from datetime import date
import hashlib
import inspect
import json
import multiprocessing
import os
from pathlib import Path
import shutil
import socket
import stat
import subprocess
import sys
from typing import Any

import pytest
import yaml

from investment_orchestrator.cli import run_step1
from investment_orchestrator.research import replacement_generation_reader as reader
from investment_orchestrator.research import replacement_memo_contract as memo
from investment_orchestrator.research import replacement_observation as render
from investment_orchestrator.research import replacement_report as report
from investment_orchestrator.research.research_anchor_approval_manifest import (
    compute_operator_completed_anchor_sha256,
)


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _anchor() -> dict[str, Any]:
    return {
        "anchor_id": "ANCHOR_FIXA",
        "anchor_type": "structural_theme",
        "applicable_tickers": ["FIXA"],
        "anchor_date_et": "2026-07-01",
        "valid_from": "2026-07-01",
        "valid_until": "2026-12-31",
        "source_type": "operator",
        "confidence_floor": "medium",
    }


def _setup_repo(root: Path) -> None:
    anchor = _anchor()
    _write(
        root / "inputs/current/strategy_settings.yaml",
        """as_of: "2026-07-12"
benchmark: "FIXA"
core_universe: [FIXA]
satellite_universe: [FIXB]
user_approved_extended_etf_static_list: [FIXC]
hard_cap_open_orders_budget: 100
target_new_buy_budget_this_run: 10
max_new_tickers_per_week: 0
ticker_role_fallback:
  FIXA: benchmark_carrier_core
  FIXB: sector_alpha_tilt
  FIXC: extended_etf_minority_sleeve
""",
    )
    _write(root / "inputs/current/portfolio_snapshot.txt", "(1) fixture portfolio\n")
    _write(
        root / "inputs/current/research_anchors.yaml",
        yaml.safe_dump(
            {
                "schema_version": "research_anchors_v1",
                "as_of_date": "2026-07-12",
                "is_llm_generated": False,
                "anchors": [],
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
                "approvals": [
                    {
                        "approval_id": "APR-1",
                        "decision": "approve",
                        "operator_completed_anchor": anchor,
                        "operator_completed_anchor_sha256": (
                            compute_operator_completed_anchor_sha256(anchor)
                        ),
                    }
                ],
            },
            sort_keys=False,
        ),
    )
    _write(
        root / "prompts/r2f_analyst_memo_content_v2.txt",
        (Path(__file__).parents[2] / "prompts/r2f_analyst_memo_content_v2.txt").read_text(
            encoding="utf-8"
        ),
    )


def _render_generation(
    root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[str, Path]:
    monkeypatch.setattr(render, "repo_root", lambda: root)
    monkeypatch.setattr(render, "_today", lambda: date(2026, 7, 12))
    result = render.replacement_render()
    return result["generation_id"], Path(result["generation_path"])


def _memo_payload(
    mode: str = "NO_TRADE",
    *,
    rationale: str = "Bounded interpretation.",
) -> dict[str, Any]:
    observations: list[dict[str, Any]] = []
    if mode == "OBSERVATION_ONLY":
        observations = [
            {
                "instrument_id": "FIXA",
                "research_view": "PREFER",
                "rationale": rationale,
                "evidence_references": [
                    {"namespace": "ACTIVE_ANCHOR", "evidence_id": "ANCHOR_FIXA"}
                ],
            }
        ]
    return {
        "schema_version": "r2f_analyst_memo_content_v2",
        "memo_result": mode,
        "confidence": "LOW",
        "instrument_observations": observations,
    }


def _set_memo(generation: Path, payload: dict[str, Any]) -> bytes:
    value = json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
    (generation / reader.MEMO_RAW_FILENAME).write_bytes(value)
    return value


@pytest.fixture
def report_context(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[Path, str, Path]:
    root = tmp_path / "repo"
    _setup_repo(root)
    generation_id, generation = _render_generation(root, monkeypatch)
    _set_memo(generation, _memo_payload())
    return root, generation_id, generation


def _publish(root: Path, generation_id: str) -> dict[str, str]:
    return report._replacement_report_at_root_for_tests(generation_id, root)


def _reports_root(root: Path) -> Path:
    return root.joinpath(*report.R2F_ROOT_PARTS, report.REPORTS_DIRECTORY)


def _attempts_root(root: Path) -> Path:
    return root.joinpath(*report.R2F_ROOT_PARTS, report.ATTEMPTS_DIRECTORY)


def _report_path(root: Path, report_id: str) -> Path:
    return _reports_root(root) / f"{report_id}.json"


def _final_report_files(root: Path) -> list[Path]:
    reports = _reports_root(root)
    if not reports.exists():
        return []
    return sorted(
        path
        for path in reports.iterdir()
        if report._FINAL_FILENAME_RE.fullmatch(path.name) is not None
    )


def _attempt_files(root: Path) -> list[Path]:
    attempts = _attempts_root(root)
    if not attempts.exists():
        return []
    return sorted(
        path
        for path in attempts.iterdir()
        if path.name.startswith(report.ATTEMPT_FILENAME_PREFIX)
        and path.name.endswith(report.ATTEMPT_FILENAME_SUFFIX)
    )


def _canonical_json_bytes(value: dict[str, Any]) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _independent_identity(envelope_bytes: bytes) -> tuple[dict[str, Any], bytes, str]:
    envelope = json.loads(envelope_bytes)
    source = envelope["source_binding"]
    memo_input = envelope["memo_input"]
    identity = {
        "schema_version": "r2f_validated_memo_report_identity_v2",
        "publication_profile": "r2f_single_file_validated_memo_report_v1",
        "source_generation_profile": source["generation_profile"],
        "source_generation_id": source["generation_id"],
        "validated_envelope_schema_version": envelope["schema_version"],
        "validated_envelope_canonical_sha256": hashlib.sha256(envelope_bytes).hexdigest(),
        "prompt_contract_canonical_sha256": source[
            "prompt_contract_canonical_sha256"
        ],
        "raw_memo_file_sha256": memo_input["file_sha256"],
        "normalized_memo_text_sha256": memo_input["normalized_text_sha256"],
        "authority_markers": {
            "report_only": True,
            "runtime_consumed": False,
            "permission_effect": "NONE",
            "not_authorization": True,
            "order_authorization": False,
            "broker_authorization": False,
        },
    }
    identity_bytes = _canonical_json_bytes(identity)
    return identity, identity_bytes, hashlib.sha256(identity_bytes).hexdigest()


def _prepared(root: Path, generation_id: str) -> report._PreparedReport:
    validated = memo._validate_generation_memo_at_root_for_tests(generation_id, root)
    return report._prepare_report(generation_id, validated)


def _replace_with_v1_generation(generation: Path) -> tuple[str, Path]:
    manifest = json.loads((generation / reader.MANIFEST_FILENAME).read_bytes())
    manifest.pop("prompt_contract")
    manifest["schema_version"] = reader.V1_MANIFEST_SCHEMA_VERSION
    manifest["compatibility_profile"] = reader.V1_COMPATIBILITY_PROFILE
    manifest_bytes = reader._json_file_bytes(manifest)
    evidence_bytes = (generation / reader.EVIDENCE_FILENAME).read_bytes()
    evidence = json.loads(evidence_bytes)
    prompt_bytes = b"legacy analyst_memo_v1 prompt\n"
    generation_id = reader._canonical_sha256(reader._semantic_generation_identity(manifest))
    binding = {
        "schema_version": reader.V1_RENDER_BINDING_SCHEMA_VERSION,
        "compatibility_profile": reader.V1_COMPATIBILITY_PROFILE,
        "generation_id": generation_id,
        "scope": "IMMUTABLE_RENDER_ARTIFACTS_AND_INITIAL_BLANK_MEMO_ONLY",
        "render_complete": True,
        "immutable_render_artifacts": {
            reader.MANIFEST_FILENAME: {
                "schema_version": reader.V1_MANIFEST_SCHEMA_VERSION,
                "file_sha256": reader._sha256(manifest_bytes),
                "canonical_content_sha256": reader._canonical_sha256(manifest),
                "mutable_after_render": False,
            },
            reader.EVIDENCE_FILENAME: {
                "schema_version": reader.EVIDENCE_PACKET_SCHEMA_VERSION,
                "file_sha256": reader._sha256(evidence_bytes),
                "canonical_content_sha256": reader._canonical_sha256(evidence),
                "mutable_after_render": False,
            },
            reader.PROMPT_FILENAME: {
                "media_type": "text/plain; charset=utf-8",
                "file_sha256": reader._sha256(prompt_bytes),
                "mutable_after_render": False,
            },
        },
        "operator_editable_inputs": {
            reader.MEMO_RAW_FILENAME: {
                "media_type": "text/plain; charset=utf-8",
                "initial_file_sha256": reader._sha256(b""),
                "initial_state": "BLANK",
                "operator_editable_after_render": True,
                "render_witness_attests_initial_bytes_only": True,
            }
        },
        **reader.AUTHORITY_MARKERS,
    }
    v1_generation = generation.parent / generation_id
    generation.rename(v1_generation)
    (v1_generation / reader.MANIFEST_FILENAME).write_bytes(manifest_bytes)
    (v1_generation / reader.PROMPT_FILENAME).write_bytes(prompt_bytes)
    (v1_generation / reader.RENDER_BINDING_FILENAME).write_bytes(
        reader._json_file_bytes(binding)
    )
    return generation_id, v1_generation


def _process_publish(
    root: str,
    generation_id: str,
    barrier: Any,
    output: Any,
) -> None:
    try:
        barrier.wait(timeout=20)
        result = report._replacement_report_at_root_for_tests(
            generation_id,
            Path(root),
        )
        output.put(("success", result))
    except BaseException as error:  # pragma: no cover - asserted in parent
        output.put(("error", type(error).__name__, str(error)))


def _run_process_publications(
    root: Path,
    generation_ids: list[str],
) -> list[tuple[Any, ...]]:
    if "fork" not in multiprocessing.get_all_start_methods():
        pytest.skip("controlled descriptor-race regression requires fork")
    context = multiprocessing.get_context("fork")
    barrier = context.Barrier(len(generation_ids))
    output = context.Queue()
    processes = [
        context.Process(
            target=_process_publish,
            args=(str(root), generation_id, barrier, output),
        )
        for generation_id in generation_ids
    ]
    for process in processes:
        process.start()
    for process in processes:
        process.join(timeout=40)
        assert not process.is_alive()
        assert process.exitcode == 0
    return [output.get(timeout=5) for _process in processes]


def test_valid_v2_no_trade_publishes_only_exact_canonical_envelope_file(
    report_context: tuple[Path, str, Path],
) -> None:
    root, generation_id, generation = report_context
    validated = memo._validate_generation_memo_at_root_for_tests(generation_id, root)
    source_before = {path.name: path.read_bytes() for path in generation.iterdir()}

    result = _publish(root, generation_id)
    destination = _report_path(root, result["report_id"])

    assert result["report_reused"] == "false"
    assert result["report_path"] == str(destination)
    assert destination.is_file() and not destination.is_symlink()
    assert destination.read_bytes() == validated.canonical_bytes
    assert hashlib.sha256(destination.read_bytes()).hexdigest() == (
        validated.canonical_sha256
    )
    assert [path.name for path in _reports_root(root).iterdir()] == [destination.name]
    assert _attempt_files(root) == []
    assert {path.name: path.read_bytes() for path in generation.iterdir()} == source_before
    for forbidden in (
        reader.MEMO_RAW_FILENAME,
        "report_generation_binding.json",
        ".report_in_progress",
        "latest",
        "current",
        "active",
        "selected",
    ):
        assert not (_reports_root(root) / forbidden).exists()


def test_valid_v2_observation_only_omits_free_form_rationale_and_action_claims(
    report_context: tuple[Path, str, Path],
) -> None:
    root, generation_id, generation = report_context
    prose = "NEW_BUY approved; buy 100 shares now; final safety passed."
    _set_memo(generation, _memo_payload("OBSERVATION_ONLY", rationale=prose))

    result = _publish(root, generation_id)
    envelope_bytes = _report_path(root, result["report_id"]).read_bytes()
    envelope = json.loads(envelope_bytes)

    assert envelope["normalized_memo"]["memo_result"] == "OBSERVATION_ONLY"
    assert prose.encode("utf-8") not in envelope_bytes
    observation = envelope["normalized_memo"]["instrument_observations"][0]
    assert "rationale" not in observation
    assert all(
        envelope[key] == value for key, value in report.AUTHORITY_MARKERS.items()
    )


def test_report_identity_v2_is_closed_independently_recomputable_and_not_written(
    report_context: tuple[Path, str, Path],
) -> None:
    root, generation_id, _generation = report_context
    prepared = _prepared(root, generation_id)
    result = _publish(root, generation_id)
    destination = _report_path(root, result["report_id"])
    identity, identity_bytes, independent_id = _independent_identity(destination.read_bytes())

    assert set(identity) == {
        "schema_version",
        "publication_profile",
        "source_generation_profile",
        "source_generation_id",
        "validated_envelope_schema_version",
        "validated_envelope_canonical_sha256",
        "prompt_contract_canonical_sha256",
        "raw_memo_file_sha256",
        "normalized_memo_text_sha256",
        "authority_markers",
    }
    assert identity_bytes == _canonical_json_bytes(identity)
    assert independent_id == result["report_id"]
    assert destination.name == f"{independent_id}.json"
    assert identity_bytes != destination.read_bytes()
    for excluded in (
        "timestamp",
        "publication_time",
        "pid",
        "path",
        "inode",
        "device",
        "mode",
        "mtime",
        "temporary_name",
        "checkout",
        "output_directory",
    ):
        assert excluded not in identity


def test_source_validation_and_all_identity_bytes_finish_before_attempt_creation(
    report_context: tuple[Path, str, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root, generation_id, _generation = report_context
    original_create = report._create_owned_attempt_file
    observed: dict[str, Any] = {}

    def validator(value: str) -> memo.ValidatedMemoEnvelope:
        assert value == generation_id
        assert not _reports_root(root).exists()
        return memo._validate_generation_memo_at_root_for_tests(value, root)

    def capture_create(directory_fd: int) -> report._OwnedAttemptFile:
        assert not _final_report_files(root)
        observed["called"] = True
        return original_create(directory_fd)

    monkeypatch.setattr(report, "_create_owned_attempt_file", capture_create)
    result = report._replacement_report_operation(generation_id, root, validator)
    assert observed == {"called": True}
    assert _report_path(root, result["report_id"]).is_file()


def test_attempt_file_is_atomically_created_and_exact_opened_fd_is_retained(
    report_context: tuple[Path, str, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root, generation_id, _generation = report_context
    original_open = report.os.open
    original_prepare = report._write_and_verify_owned_attempt
    opened: dict[str, tuple[int, int, int]] = {}

    def tracked_open(
        path: str,
        flags: int,
        mode: int = 0o777,
        *,
        dir_fd: int | None = None,
    ) -> int:
        descriptor = original_open(path, flags, mode, dir_fd=dir_fd)
        if path.startswith(report.ATTEMPT_FILENAME_PREFIX):
            opened[path] = (descriptor, flags, mode)
        return descriptor

    def inspect_owned(
        attempts_fd: int,
        owned: report._OwnedAttemptFile,
        prepared: report._PreparedReport,
    ) -> None:
        descriptor, flags, mode = opened[owned.name]
        assert descriptor == owned.descriptor
        assert flags & os.O_RDWR
        assert flags & os.O_CREAT
        assert flags & os.O_EXCL
        assert flags & os.O_NOFOLLOW
        assert mode == 0o600
        entry = os.stat(owned.name, dir_fd=attempts_fd, follow_symlinks=False)
        opened_state = os.fstat(owned.descriptor)
        assert (entry.st_dev, entry.st_ino) == (opened_state.st_dev, opened_state.st_ino)
        assert entry.st_nlink == opened_state.st_nlink == 1
        original_prepare(attempts_fd, owned, prepared)

    monkeypatch.setattr(report.os, "open", tracked_open)
    monkeypatch.setattr(report, "_require_descriptor_primitives", lambda: None)
    monkeypatch.setattr(reader, "_require_descriptor_primitives", lambda: None)
    monkeypatch.setattr(report, "_write_and_verify_owned_attempt", inspect_owned)
    _publish(root, generation_id)
    assert len(opened) == 1


def test_no_report_directory_mkdir_open_ownership_gap_exists(
    report_context: tuple[Path, str, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root, generation_id, _generation = report_context
    original_mkdir = report.os.mkdir
    created: list[str] = []

    def tracked_mkdir(
        path: str,
        mode: int = 0o777,
        *,
        dir_fd: int | None = None,
    ) -> None:
        created.append(path)
        original_mkdir(path, mode, dir_fd=dir_fd)

    monkeypatch.setattr(report.os, "mkdir", tracked_mkdir)
    monkeypatch.setattr(report, "_require_descriptor_primitives", lambda: None)
    monkeypatch.setattr(reader, "_require_descriptor_primitives", lambda: None)
    result = _publish(root, generation_id)

    assert all(report._FINAL_FILENAME_RE.fullmatch(name) is None for name in created)
    assert _report_path(root, result["report_id"]).is_file()
    assert not any(path.is_dir() for path in _reports_root(root).iterdir())


def test_identical_publication_reuses_without_any_file_mutation(
    report_context: tuple[Path, str, Path],
) -> None:
    root, generation_id, _generation = report_context
    first = _publish(root, generation_id)
    destination = _report_path(root, first["report_id"])
    before = destination.stat()
    before_bytes = destination.read_bytes()

    second = _publish(root, generation_id)
    after = destination.stat()

    assert second["report_id"] == first["report_id"]
    assert second["report_reused"] == "true"
    assert destination.read_bytes() == before_bytes
    assert (after.st_dev, after.st_ino, after.st_mode, after.st_mtime_ns) == (
        before.st_dev,
        before.st_ino,
        before.st_mode,
        before.st_mtime_ns,
    )


def test_memo_edit_changes_report_id_without_mutating_prior_report(
    report_context: tuple[Path, str, Path],
) -> None:
    root, generation_id, generation = report_context
    first = _publish(root, generation_id)
    first_path = _report_path(root, first["report_id"])
    first_bytes = first_path.read_bytes()

    changed = _memo_payload()
    changed["confidence"] = "HIGH"
    _set_memo(generation, changed)
    second = _publish(root, generation_id)

    assert second["report_id"] != first["report_id"]
    assert first_path.read_bytes() == first_bytes
    assert {path.name for path in _final_report_files(root)} == {
        f"{first['report_id']}.json",
        f"{second['report_id']}.json",
    }


def test_equivalent_checkouts_produce_identical_report_id_and_file_bytes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    observed: list[tuple[str, bytes]] = []
    for name in ("checkout-a", "checkout-b"):
        root = tmp_path / name
        _setup_repo(root)
        generation_id, generation = _render_generation(root, monkeypatch)
        _set_memo(generation, _memo_payload("OBSERVATION_ONLY"))
        result = _publish(root, generation_id)
        observed.append(
            (result["report_id"], _report_path(root, result["report_id"]).read_bytes())
        )
    assert observed[0] == observed[1]


def test_rename_takes_effect_then_wrapper_raises_is_committed_success(
    report_context: tuple[Path, str, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root, generation_id, _generation = report_context
    original = report._rename_attempt_to_final_noreplace

    def rename_then_raise(*args: Any) -> report._RenameOutcome:
        assert original(*args) is report._RenameOutcome.SUCCESS
        raise RuntimeError("private injected exception")

    monkeypatch.setattr(report, "_rename_attempt_to_final_noreplace", rename_then_raise)
    result = _publish(root, generation_id)

    destination = _report_path(root, result["report_id"])
    assert result["report_reused"] == "false"
    assert destination.read_bytes() == _prepared(root, generation_id).envelope_bytes
    assert _attempt_files(root) == []
    assert _publish(root, generation_id)["report_reused"] == "true"


@pytest.mark.parametrize("component", ["r2f_root", "attempts", "reports"])
@pytest.mark.parametrize("timing", ["before_attempt_create", "after_attempt_create"])
def test_canonical_parent_substitution_around_attempt_creation_fails_prerename(
    report_context: tuple[Path, str, Path],
    monkeypatch: pytest.MonkeyPatch,
    component: str,
    timing: str,
) -> None:
    root, generation_id, _generation = report_context
    prepared = _prepared(root, generation_id)
    original = report._create_owned_attempt_file
    r2f_root = root.joinpath(*report.R2F_ROOT_PARTS)
    target = {
        "r2f_root": r2f_root,
        "attempts": _attempts_root(root),
        "reports": _reports_root(root),
    }[component]
    displaced = target.with_name(f"{target.name}-displaced-{timing}")

    def replace_parent() -> None:
        target.rename(displaced)
        target.mkdir(mode=0o700)

    def substituted_create(attempts_fd: int) -> report._OwnedAttemptFile:
        if timing == "before_attempt_create":
            replace_parent()
        owned = original(attempts_fd)
        if timing == "after_attempt_create":
            replace_parent()
        return owned

    monkeypatch.setattr(report, "_create_owned_attempt_file", substituted_create)
    with pytest.raises(report.ReplacementReportError):
        _publish(root, generation_id)

    assert list(root.rglob(".attempt-*.tmp"))
    assert not list(root.rglob(prepared.final_filename))


@pytest.mark.parametrize("component", ["r2f_root", "attempts", "reports"])
@pytest.mark.parametrize("timing", ["before_rename", "after_rename"])
def test_canonical_parent_substitution_around_rename_fails_without_rollback(
    report_context: tuple[Path, str, Path],
    monkeypatch: pytest.MonkeyPatch,
    component: str,
    timing: str,
) -> None:
    root, generation_id, _generation = report_context
    prepared = _prepared(root, generation_id)
    original = report._rename_attempt_to_final_noreplace
    r2f_root = root.joinpath(*report.R2F_ROOT_PARTS)
    target = {
        "r2f_root": r2f_root,
        "attempts": _attempts_root(root),
        "reports": _reports_root(root),
    }[component]
    displaced = target.with_name(f"{target.name}-displaced-{timing}")

    def replace_parent() -> None:
        target.rename(displaced)
        target.mkdir(mode=0o700)

    def substituted_rename(*args: Any) -> report._RenameOutcome:
        if timing == "before_rename":
            replace_parent()
        outcome = original(*args)
        assert outcome is report._RenameOutcome.SUCCESS
        if timing == "after_rename":
            replace_parent()
        return outcome

    monkeypatch.setattr(
        report,
        "_rename_attempt_to_final_noreplace",
        substituted_rename,
    )
    with pytest.raises(
        report.ReplacementReportError,
        match="POST_RENAME_VERIFICATION_FAILURE",
    ):
        _publish(root, generation_id)

    preserved = list(root.rglob(prepared.final_filename))
    assert len(preserved) == 1
    assert preserved[0].read_bytes() == prepared.envelope_bytes


def test_byte_identical_different_inode_after_rename_and_raise_is_not_reuse(
    report_context: tuple[Path, str, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root, generation_id, _generation = report_context
    prepared = _prepared(root, generation_id)
    original = report._rename_attempt_to_final_noreplace
    displaced_name = ".displaced-owned-final.tmp"

    def substitute_then_raise(
        attempts_fd: int,
        attempt_name: str,
        reports_fd: int,
        final_name: str,
    ) -> report._RenameOutcome:
        assert original(
            attempts_fd,
            attempt_name,
            reports_fd,
            final_name,
        ) is report._RenameOutcome.SUCCESS
        os.rename(
            final_name,
            displaced_name,
            src_dir_fd=reports_fd,
            dst_dir_fd=reports_fd,
        )
        descriptor = os.open(
            final_name,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
            0o600,
            dir_fd=reports_fd,
        )
        try:
            assert os.write(descriptor, prepared.envelope_bytes) == len(
                prepared.envelope_bytes
            )
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        raise RuntimeError("private post-rename exception")

    monkeypatch.setattr(
        report,
        "_rename_attempt_to_final_noreplace",
        substitute_then_raise,
    )
    with pytest.raises(
        report.ReplacementReportError,
        match="RENAME_RAISED_AMBIGUOUS_EXCEPTION",
    ):
        _publish(root, generation_id)

    substitute = _report_path(root, prepared.report_id)
    displaced = _reports_root(root) / displaced_name
    assert substitute.read_bytes() == displaced.read_bytes() == prepared.envelope_bytes
    assert substitute.stat().st_ino != displaced.stat().st_ino


def test_post_success_verification_failure_never_enters_existing_reuse(
    report_context: tuple[Path, str, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root, generation_id, _generation = report_context
    prepared = _prepared(root, generation_id)
    monkeypatch.setattr(
        report,
        "_verify_owned_final_after_rename",
        lambda **_kwargs: (_ for _ in ()).throw(RuntimeError("verification boundary")),
    )
    monkeypatch.setattr(
        report,
        "_verify_existing_final_report",
        lambda **_kwargs: pytest.fail("post-success failure entered reuse"),
    )

    with pytest.raises(
        report.ReplacementReportError,
        match="POST_RENAME_VERIFICATION_FAILURE",
    ):
        _publish(root, generation_id)
    assert _report_path(root, prepared.report_id).read_bytes() == prepared.envelope_bytes
    assert _attempt_files(root) == []


def test_genuine_kernel_eexist_reuses_exact_final_and_preserves_attempt(
    report_context: tuple[Path, str, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root, generation_id, _generation = report_context
    original = report._rename_attempt_to_final_noreplace

    def create_final_then_real_rename(
        attempts_fd: int,
        attempt_name: str,
        reports_fd: int,
        final_name: str,
    ) -> report._RenameOutcome:
        source = os.open(
            attempt_name,
            os.O_RDONLY | os.O_NOFOLLOW,
            dir_fd=attempts_fd,
        )
        try:
            chunks: list[bytes] = []
            while chunk := os.read(source, 65_536):
                chunks.append(chunk)
        finally:
            os.close(source)
        value = b"".join(chunks)
        destination = os.open(
            final_name,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
            0o600,
            dir_fd=reports_fd,
        )
        try:
            assert os.write(destination, value) == len(value)
            os.fsync(destination)
        finally:
            os.close(destination)
        outcome = original(attempts_fd, attempt_name, reports_fd, final_name)
        assert outcome is report._RenameOutcome.EEXIST
        return outcome

    monkeypatch.setattr(
        report,
        "_rename_attempt_to_final_noreplace",
        create_final_then_real_rename,
    )
    result = _publish(root, generation_id)
    assert result["report_reused"] == "true"
    assert len(_attempt_files(root)) == 1
    assert _report_path(root, result["report_id"]).read_bytes() == (
        _attempt_files(root)[0].read_bytes()
    )


def test_substituted_attempt_name_survives_ambiguous_failure_without_deletion(
    report_context: tuple[Path, str, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root, generation_id, _generation = report_context
    replacement_bytes = b"unrelated substituted attempt"
    observed: dict[str, str] = {}

    def substitute_attempt(
        attempts_fd: int,
        attempt_name: str,
        _reports_fd: int,
        _final_name: str,
    ) -> report._RenameOutcome:
        displaced_name = f".attempt-displaced-{os.getpid()}.tmp"
        os.rename(
            attempt_name,
            displaced_name,
            src_dir_fd=attempts_fd,
            dst_dir_fd=attempts_fd,
        )
        descriptor = os.open(
            attempt_name,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
            0o600,
            dir_fd=attempts_fd,
        )
        try:
            assert os.write(descriptor, replacement_bytes) == len(replacement_bytes)
        finally:
            os.close(descriptor)
        observed.update(attempt=attempt_name, displaced=displaced_name)
        raise OSError("rename did not take effect")

    monkeypatch.setattr(
        report,
        "_rename_attempt_to_final_noreplace",
        substitute_attempt,
    )
    with pytest.raises(
        report.ReplacementReportError,
        match="RENAME_RAISED_AMBIGUOUS_EXCEPTION",
    ):
        _publish(root, generation_id)

    assert (_attempts_root(root) / observed["attempt"]).read_bytes() == replacement_bytes
    assert (_attempts_root(root) / observed["displaced"]).is_file()
    assert _final_report_files(root) == []


def test_unrelated_existing_final_file_is_never_repaired_or_deleted(
    report_context: tuple[Path, str, Path],
) -> None:
    root, generation_id, _generation = report_context
    prepared = _prepared(root, generation_id)
    target = _report_path(root, prepared.report_id)
    target.parent.mkdir(parents=True)
    unrelated = b"unrelated existing final"
    target.write_bytes(unrelated)

    with pytest.raises(report.ReplacementReportError):
        _publish(root, generation_id)

    assert target.read_bytes() == unrelated
    assert _attempt_files(root) == []


def test_production_has_no_attempt_or_final_name_deletion_path() -> None:
    source = Path(report.__file__).read_text(encoding="utf-8")
    for forbidden in (
        "_cleanup_owned_temp_noexcept",
        "_cleanup_owned_attempt",
        "_unlink_owned_entry_noexcept",
        ".unlink(",
        "os.remove(",
        "os.removedirs(",
        "os.rename(",
        "os.replace(",
        "os.link(",
        "shutil.rmtree(",
    ):
        assert forbidden not in source


@pytest.mark.parametrize(
    "failure_point",
    [
        "write",
        "file_fsync",
        "descriptor_read",
        "envelope_verification",
        "attempt_entry_verification",
        "attempts_directory_fsync",
        "canonical_prerename",
        "rename_no_effect",
        "primitive_unavailable",
    ],
)
def test_every_prerename_boundary_has_no_final_and_preserves_attempt(
    report_context: tuple[Path, str, Path],
    monkeypatch: pytest.MonkeyPatch,
    failure_point: str,
) -> None:
    root, generation_id, _generation = report_context
    if failure_point == "write":
        monkeypatch.setattr(
            report.os,
            "write",
            lambda *_args: (_ for _ in ()).throw(OSError("write boundary")),
        )
    elif failure_point == "file_fsync":
        original_fsync = report.os.fsync

        def fail_file_fsync(descriptor: int) -> None:
            if stat.S_ISREG(os.fstat(descriptor).st_mode):
                raise OSError("file fsync boundary")
            original_fsync(descriptor)

        monkeypatch.setattr(report.os, "fsync", fail_file_fsync)
    elif failure_point == "descriptor_read":
        monkeypatch.setattr(
            report,
            "_read_stable_descriptor",
            lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("read boundary")),
        )
    elif failure_point == "envelope_verification":
        monkeypatch.setattr(
            report,
            "_verify_envelope_bytes",
            lambda *_args: (_ for _ in ()).throw(RuntimeError("verify boundary")),
        )
    elif failure_point == "attempt_entry_verification":
        original_verify = report._verify_owned_attempt_entry
        calls = 0

        def fail_second_check(*args: Any, **kwargs: Any) -> None:
            nonlocal calls
            calls += 1
            if calls == 2:
                raise RuntimeError("attempt identity boundary")
            original_verify(*args, **kwargs)

        monkeypatch.setattr(report, "_verify_owned_attempt_entry", fail_second_check)
    elif failure_point == "attempts_directory_fsync":
        original_directory_fsync = report._fsync_directory

        def fail_attempt_parent(descriptor: int, error_code: str) -> None:
            if error_code == "REPORT_ATTEMPTS_DURABILITY_FAILURE":
                raise RuntimeError("attempts fsync boundary")
            original_directory_fsync(descriptor, error_code)

        monkeypatch.setattr(report, "_fsync_directory", fail_attempt_parent)
    elif failure_point == "canonical_prerename":
        original_canonical = report._verify_canonical_output_directories
        calls = 0

        def fail_second_canonical(*args: Any, **kwargs: Any) -> None:
            nonlocal calls
            calls += 1
            if calls == 2:
                raise RuntimeError("canonical boundary")
            original_canonical(*args, **kwargs)

        monkeypatch.setattr(
            report,
            "_verify_canonical_output_directories",
            fail_second_canonical,
        )
    elif failure_point == "rename_no_effect":
        monkeypatch.setattr(
            report,
            "_rename_attempt_to_final_noreplace",
            lambda *_args: (_ for _ in ()).throw(OSError("rename boundary")),
        )
    else:
        monkeypatch.setattr(
            report,
            "_rename_attempt_to_final_noreplace",
            lambda *_args: (_ for _ in ()).throw(report._RenamePrimitiveUnavailable()),
        )

    with pytest.raises(report.ReplacementReportError):
        _publish(root, generation_id)

    assert _final_report_files(root) == []
    assert len(_attempt_files(root)) == 1


def test_post_rename_durability_failure_leaves_final_and_never_reuses(
    report_context: tuple[Path, str, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root, generation_id, _generation = report_context
    prepared = _prepared(root, generation_id)
    original = report._fsync_directory

    def fail_final_fsync(descriptor: int, error_code: str) -> None:
        if error_code == "REPORT_FINAL_DURABILITY_FAILURE":
            raise RuntimeError("final durability boundary")
        original(descriptor, error_code)

    monkeypatch.setattr(report, "_fsync_directory", fail_final_fsync)
    monkeypatch.setattr(
        report,
        "_verify_existing_final_report",
        lambda **_kwargs: pytest.fail("post-success failure entered reuse"),
    )
    with pytest.raises(
        report.ReplacementReportError,
        match="POST_RENAME_VERIFICATION_FAILURE",
    ):
        _publish(root, generation_id)
    assert _report_path(root, prepared.report_id).read_bytes() == prepared.envelope_bytes
    assert _attempt_files(root) == []


@pytest.mark.parametrize("mutation", ["bytes", "authority", "symlink", "fifo", "directory"])
def test_existing_invalid_or_nonregular_final_fails_closed_without_mutation(
    report_context: tuple[Path, str, Path],
    mutation: str,
) -> None:
    if mutation == "fifo" and not hasattr(os, "mkfifo"):
        pytest.skip("FIFO unavailable")
    root, generation_id, _generation = report_context
    prepared = _prepared(root, generation_id)
    target = _report_path(root, prepared.report_id)
    target.parent.mkdir(parents=True)
    if mutation == "bytes":
        target.write_bytes(b"{}")
    elif mutation == "authority":
        payload = json.loads(prepared.envelope_bytes)
        payload["report_only"] = False
        target.write_bytes(_canonical_json_bytes(payload))
    elif mutation == "symlink":
        other = target.parent / "unrelated"
        other.write_bytes(prepared.envelope_bytes)
        target.symlink_to(other)
    elif mutation == "fifo":
        os.mkfifo(target)
    else:
        target.mkdir()

    before = os.lstat(target)
    with pytest.raises(report.ReplacementReportError):
        _publish(root, generation_id)
    after = os.lstat(target)
    assert (after.st_dev, after.st_ino, after.st_mode) == (
        before.st_dev,
        before.st_ino,
        before.st_mode,
    )


def test_existing_socket_final_is_rejected_where_supported(
    report_context: tuple[Path, str, Path],
) -> None:
    root, generation_id, _generation = report_context
    prepared = _prepared(root, generation_id)
    reports = _reports_root(root)
    reports.mkdir(parents=True)
    endpoint = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    previous = Path.cwd()
    try:
        os.chdir(reports)
        try:
            endpoint.bind(prepared.final_filename)
        except OSError as error:
            pytest.skip(f"Unix socket construction unavailable: {error}")
    finally:
        os.chdir(previous)
    try:
        with pytest.raises(report.ReplacementReportError):
            _publish(root, generation_id)
        assert stat.S_ISSOCK(os.lstat(_report_path(root, prepared.report_id)).st_mode)
    finally:
        endpoint.close()


def test_existing_device_final_is_rejected_where_supported(
    report_context: tuple[Path, str, Path],
) -> None:
    if not hasattr(os, "mknod") or not hasattr(os, "makedev"):
        pytest.skip("device-node construction unavailable")
    root, generation_id, _generation = report_context
    prepared = _prepared(root, generation_id)
    target = _report_path(root, prepared.report_id)
    target.parent.mkdir(parents=True)
    try:
        os.mknod(target, stat.S_IFCHR | 0o600, os.makedev(1, 3))
    except (OSError, PermissionError) as error:
        pytest.skip(f"device-node construction unavailable: {error}")
    with pytest.raises(report.ReplacementReportError):
        _publish(root, generation_id)
    assert stat.S_ISCHR(os.lstat(target).st_mode)


def test_unrelated_attempt_entry_does_not_select_or_invalidate_exact_final_reuse(
    report_context: tuple[Path, str, Path],
) -> None:
    root, generation_id, _generation = report_context
    first = _publish(root, generation_id)
    unrelated = _attempts_root(root) / ".attempt-unrelated.tmp"
    unrelated.parent.mkdir(parents=True, exist_ok=True)
    unrelated.write_bytes(b"unrelated")

    second = _publish(root, generation_id)

    assert second["report_reused"] == "true"
    assert unrelated.read_bytes() == b"unrelated"
    assert len(_final_report_files(root)) == 1


def test_symlinked_report_root_or_reports_parent_is_rejected_without_external_write(
    report_context: tuple[Path, str, Path],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root, generation_id, _generation = report_context
    external = tmp_path / "external"
    external.mkdir()
    reports = _reports_root(root)
    reports.parent.mkdir(parents=True, exist_ok=True)
    reports.symlink_to(external, target_is_directory=True)

    with pytest.raises(report.ReplacementReportError):
        _publish(root, generation_id)
    assert list(external.iterdir()) == []

    reports.unlink()
    artifacts = root / "artifacts"
    displaced = root / "artifacts-real"
    artifacts.rename(displaced)
    artifacts.symlink_to(external, target_is_directory=True)
    with pytest.raises((report.ReplacementReportError, reader.ReplacementGenerationReaderError)):
        _publish(root, generation_id)
    assert list(external.iterdir()) == []


def test_concurrent_same_id_processes_create_one_final_and_both_verify_success(
    report_context: tuple[Path, str, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root, generation_id, _generation = report_context
    if "fork" not in multiprocessing.get_all_start_methods():
        pytest.skip("controlled rename race requires fork")
    rename_barrier = multiprocessing.get_context("fork").Barrier(2)
    original = report._rename_attempt_to_final_noreplace

    def synchronized_rename(*args: Any) -> report._RenameOutcome:
        rename_barrier.wait(timeout=20)
        return original(*args)

    monkeypatch.setattr(
        report,
        "_rename_attempt_to_final_noreplace",
        synchronized_rename,
    )
    observed = _run_process_publications(root, [generation_id, generation_id])

    assert all(item[0] == "success" for item in observed), observed
    results = [item[1] for item in observed]
    assert len({value["report_id"] for value in results}) == 1
    assert sorted(value["report_reused"] for value in results) == ["false", "true"]
    assert len(_final_report_files(root)) == 1
    assert len(_attempt_files(root)) == 1


def test_concurrent_different_memos_publish_distinct_independent_final_files(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "repo"
    _setup_repo(root)
    first_id, first_generation = _render_generation(root, monkeypatch)
    _set_memo(first_generation, _memo_payload("NO_TRADE"))
    snapshot = root / "inputs/current/portfolio_snapshot.txt"
    snapshot.write_text("(2) changed fixture portfolio\n", encoding="utf-8")
    second_id, second_generation = _render_generation(root, monkeypatch)
    _set_memo(second_generation, _memo_payload("OBSERVATION_ONLY"))
    assert first_id != second_id

    if "fork" not in multiprocessing.get_all_start_methods():
        pytest.skip("controlled rename race requires fork")
    rename_barrier = multiprocessing.get_context("fork").Barrier(2)
    original = report._rename_attempt_to_final_noreplace

    def synchronized_rename(*args: Any) -> report._RenameOutcome:
        rename_barrier.wait(timeout=20)
        return original(*args)

    monkeypatch.setattr(
        report,
        "_rename_attempt_to_final_noreplace",
        synchronized_rename,
    )

    observed = _run_process_publications(root, [first_id, second_id])

    assert all(item[0] == "success" for item in observed), observed
    results = [item[1] for item in observed]
    assert len({value["report_id"] for value in results}) == 2
    assert len(_final_report_files(root)) == 2
    assert _attempt_files(root) == []


def test_v1_generation_fails_closed_before_any_report_entry(
    report_context: tuple[Path, str, Path],
) -> None:
    root, _v2_generation_id, generation = report_context
    v1_generation_id, _v1_generation = _replace_with_v1_generation(generation)

    with pytest.raises(
        reader.ReplacementGenerationReaderError,
        match="MEMO_PROMPT_PROFILE_UNSUPPORTED",
    ):
        _publish(root, v1_generation_id)
    assert not _reports_root(root).exists()


@pytest.mark.parametrize(
    "failure_kind",
    [
        "unknown_generation",
        "incomplete_generation",
        "tampered_binding",
        "malformed_memo",
        "prompt_mismatch",
        "unknown_instrument",
        "invalid_anchor",
        "validator_cleanup_failure",
        "memo_instability",
    ],
)
def test_invalid_inputs_have_bounded_failure_no_final_and_no_source_mutation(
    report_context: tuple[Path, str, Path],
    failure_kind: str,
) -> None:
    root, generation_id, generation = report_context
    requested_id = generation_id
    validator: Any = lambda value: memo._validate_generation_memo_at_root_for_tests(
        value,
        root,
    )
    if failure_kind == "unknown_generation":
        requested_id = "0" * 64
    elif failure_kind == "incomplete_generation":
        (generation / reader.IN_PROGRESS_FILENAME).write_bytes(b"")
    elif failure_kind == "tampered_binding":
        binding = generation / reader.RENDER_BINDING_FILENAME
        binding.write_bytes(binding.read_bytes() + b" ")
    elif failure_kind == "malformed_memo":
        (generation / reader.MEMO_RAW_FILENAME).write_bytes(b"not-json")
    elif failure_kind == "prompt_mismatch":
        template = root / "prompts" / reader.PROMPT_TEMPLATE_FILENAME
        template.write_bytes(template.read_bytes() + b"changed")
    elif failure_kind == "unknown_instrument":
        payload = _memo_payload("OBSERVATION_ONLY")
        payload["instrument_observations"][0]["instrument_id"] = "UNKNOWN"
        _set_memo(generation, payload)
    elif failure_kind == "invalid_anchor":
        payload = _memo_payload("OBSERVATION_ONLY")
        payload["instrument_observations"][0]["evidence_references"][0][
            "evidence_id"
        ] = "ANCHOR_UNKNOWN"
        _set_memo(generation, payload)
    elif failure_kind == "validator_cleanup_failure":
        validator = lambda _value: (_ for _ in ()).throw(
            memo.ReplacementMemoContractError("SOURCE_GENERATION_CLEANUP_FAILED")
        )
    else:
        validator = lambda _value: (_ for _ in ()).throw(
            memo.ReplacementMemoContractError("MEMO_SOURCE_UNSTABLE")
        )
    source_before = {
        path.name: path.read_bytes() for path in generation.iterdir() if path.is_file()
    }

    with pytest.raises((memo.ReplacementMemoContractError, reader.ReplacementGenerationReaderError)):
        report._replacement_report_operation(requested_id, root, validator)

    assert _final_report_files(root) == []
    assert _attempt_files(root) == []
    assert {
        path.name: path.read_bytes() for path in generation.iterdir() if path.is_file()
    } == source_before


def test_post_commit_descriptor_close_exceptions_are_no_throw(
    report_context: tuple[Path, str, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root, generation_id, _generation = report_context
    original_close = report._close_directory_chain_noexcept

    def close_then_raise(*args: Any) -> None:
        original_close(*args)
        raise RuntimeError("descriptor close failure")

    monkeypatch.setattr(report, "_close_directory_chain_noexcept", close_then_raise)
    result = _publish(root, generation_id)
    assert _report_path(root, result["report_id"]).is_file()
    assert _publish(root, generation_id)["report_reused"] == "true"


def test_public_api_and_cli_have_no_root_default_scan_or_selector() -> None:
    assert tuple(inspect.signature(report.replacement_report).parameters) == ("generation_id",)
    parser = run_step1.build_parser()
    help_text = parser.format_help()
    assert "replacement-report" in help_text
    normalized_help = " ".join(help_text.split()).replace("- ", "-")
    assert "manual immutable single-file report-only validated-memo publication" in (
        normalized_help
    )
    source = inspect.getsource(run_step1)
    assert source.count("replacement_report(args.generation_id)") == 1
    assert "--output" not in inspect.getsource(run_step1.build_parser)


@pytest.mark.parametrize(
    "value",
    ["A" * 64, "0" * 63, "0" * 65, "g" * 64, "../" + "0" * 64],
)
def test_cli_rejects_uppercase_short_long_nonhex_and_path_ids(value: str) -> None:
    with pytest.raises(SystemExit) as caught:
        run_step1.build_parser().parse_args(
            ["replacement-report", "--generation-id", value]
        )
    assert caught.value.code == 2


def test_cli_requires_generation_id_and_help_is_nonactionable(
    capsys: pytest.CaptureFixture[str],
) -> None:
    with pytest.raises(SystemExit) as missing:
        run_step1.build_parser().parse_args(["replacement-report"])
    assert missing.value.code == 2
    capsys.readouterr()
    with pytest.raises(SystemExit) as helped:
        run_step1.build_parser().parse_args(["replacement-report", "--help"])
    assert helped.value.code == 0
    text = capsys.readouterr().out.lower()
    assert "manual immutable single-file report-only validated-memo publication" in text
    for forbidden in (
        "candidate selection",
        "readiness",
        "approval",
        "order preparation",
        "execution",
    ):
        assert forbidden not in text


def _patch_cli_report_root(root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(report, "repo_root", lambda: root)
    monkeypatch.setattr(
        report,
        "validate_generation_memo",
        lambda generation_id: memo._validate_generation_memo_at_root_for_tests(
            generation_id,
            root,
        ),
    )


def test_cli_displays_bounded_id_and_relative_single_json_path(
    report_context: tuple[Path, str, Path],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    root, generation_id, _generation = report_context
    _patch_cli_report_root(root, monkeypatch)
    monkeypatch.setattr(
        sys,
        "argv",
        ["run_step1", "replacement-report", "--generation-id", generation_id],
    )

    assert run_step1.main() == 0
    report_id, relative_path = capsys.readouterr().out.strip().split(" ", 1)
    assert relative_path.endswith(f"reports/{report_id}.json")
    assert not Path(relative_path).is_absolute()


def test_committed_display_failure_returns_private_sentinel_and_file_survives(
    report_context: tuple[Path, str, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root, generation_id, _generation = report_context
    _patch_cli_report_root(root, monkeypatch)

    class FailedStdout:
        def write(self, _value: str) -> int:
            raise BrokenPipeError("display")

        def flush(self) -> None:
            raise BrokenPipeError("display")

    monkeypatch.setattr(sys, "stdout", FailedStdout())
    monkeypatch.setattr(
        sys,
        "argv",
        ["run_step1", "replacement-report", "--generation-id", generation_id],
    )
    monkeypatch.setattr(
        run_step1.os,
        "_exit",
        lambda _status: pytest.fail("programmatic main used process exit"),
    )

    assert run_step1.main() is run_step1._COMMITTED_DISPLAY_FAILURE
    final = _final_report_files(root)
    assert len(final) == 1 and final[0].is_file()
    assert _publish(root, generation_id)["report_reused"] == "true"


def test_invalid_precommit_cli_failure_never_calls_committed_display(
    report_context: tuple[Path, str, Path],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    root, generation_id, generation = report_context
    (generation / reader.MEMO_RAW_FILENAME).write_bytes(b"bad")
    _patch_cli_report_root(root, monkeypatch)
    monkeypatch.setattr(
        run_step1,
        "_display_committed_replacement_result_noexcept",
        lambda _value: pytest.fail("precommit failure used committed display"),
    )
    monkeypatch.setattr(
        sys,
        "argv",
        ["run_step1", "replacement-report", "--generation-id", generation_id],
    )

    assert run_step1.main() == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == "MEMO_JSON_INVALID\n"
    assert _final_report_files(root) == []


def test_real_entrypoint_converts_only_committed_display_sentinel_to_zero(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class ProcessExit(BaseException):
        def __init__(self, status: int) -> None:
            self.status = status

    monkeypatch.setattr(run_step1, "main", lambda: run_step1._COMMITTED_DISPLAY_FAILURE)
    monkeypatch.setattr(
        run_step1.os,
        "_exit",
        lambda status: (_ for _ in ()).throw(ProcessExit(status)),
    )
    with pytest.raises(ProcessExit) as caught:
        run_step1._run_process_entrypoint()
    assert caught.value.status == 0


def test_legacy_cli_branch_retains_normal_programmatic_behavior(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(
        run_step1,
        "render_step1_prompt",
        lambda: {"prompt_path": "legacy-prompt.txt"},
    )
    monkeypatch.setattr(sys, "argv", ["run_step1", "render"])
    monkeypatch.setattr(
        run_step1.os,
        "_exit",
        lambda _status: pytest.fail("legacy command used immediate process exit"),
    )
    assert run_step1.main() == 0
    assert capsys.readouterr().out == "legacy-prompt.txt\n"


def _copy_cli_repository(root: Path) -> None:
    source_root = Path(__file__).resolve().parents[2] / "src" / "investment_orchestrator"
    shutil.copytree(
        source_root,
        root / "src" / "investment_orchestrator",
        ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
    )
    _setup_repo(root)


def _cli_environment(root: Path) -> dict[str, str]:
    return {
        **os.environ,
        "PYTHONPATH": str(root / "src"),
        "PYTHONDONTWRITEBYTECODE": "1",
    }


def _prepare_cli_generation(root: Path) -> str:
    command = [
        sys.executable,
        "-m",
        "investment_orchestrator.cli.run_step1",
        "replacement-render",
    ]
    process = subprocess.run(
        command,
        cwd=root,
        capture_output=True,
        env=_cli_environment(root),
        timeout=30,
        check=False,
    )
    assert process.returncode == 0, process.stderr.decode(errors="replace")
    generation = Path(process.stdout.decode().strip()).parent
    generation_id = generation.name
    _set_memo(generation, _memo_payload())
    return generation_id


def _assert_cli_report(root: Path) -> Path:
    finals = _final_report_files(root)
    assert len(finals) == 1
    payload = json.loads(finals[0].read_bytes())
    assert payload["report_only"] is True
    assert payload["runtime_consumed"] is False
    return finals[0]


def test_replacement_report_real_normal_stdout_reports_single_json(
    tmp_path: Path,
) -> None:
    root = tmp_path / "normal-stdout"
    _copy_cli_repository(root)
    generation_id = _prepare_cli_generation(root)
    process = subprocess.run(
        [
            sys.executable,
            "-m",
            "investment_orchestrator.cli.run_step1",
            "replacement-report",
            "--generation-id",
            generation_id,
        ],
        cwd=root,
        capture_output=True,
        env=_cli_environment(root),
        timeout=30,
        check=False,
    )
    assert process.returncode == 0 and process.stderr == b""
    report_id, relative_path = process.stdout.decode().strip().split(" ", 1)
    assert relative_path == (
        f"artifacts/current/step1_research/r2f_report_only/reports/{report_id}.json"
    )
    assert _assert_cli_report(root).name == f"{report_id}.json"


def test_replacement_report_real_broken_pipe_keeps_final_file_reusable(
    tmp_path: Path,
) -> None:
    root = tmp_path / "broken-pipe"
    _copy_cli_repository(root)
    generation_id = _prepare_cli_generation(root)
    command = [
        sys.executable,
        "-m",
        "investment_orchestrator.cli.run_step1",
        "replacement-report",
        "--generation-id",
        generation_id,
    ]
    read_fd, write_fd = os.pipe()
    os.close(read_fd)
    try:
        process = subprocess.Popen(
            command,
            cwd=root,
            stdout=write_fd,
            stderr=subprocess.PIPE,
            env=_cli_environment(root),
        )
    finally:
        os.close(write_fd)
    stderr = process.communicate(timeout=30)[1]
    assert process.returncode == 0, stderr.decode(errors="replace")
    assert stderr == b""
    _assert_cli_report(root)
    rerun = subprocess.run(
        command,
        cwd=root,
        capture_output=True,
        env=_cli_environment(root),
        timeout=30,
        check=False,
    )
    assert rerun.returncode == 0 and rerun.stderr == b""
    _assert_cli_report(root)


def test_replacement_report_real_persistent_stdout_failure_hard_exits_successfully(
    tmp_path: Path,
) -> None:
    root = tmp_path / "persistent-stdout"
    _copy_cli_repository(root)
    generation_id = _prepare_cli_generation(root)
    launcher = f'''\
import runpy
import sys

class PersistentBrokenStdout:
    def write(self, value):
        return len(value)
    def flush(self):
        raise BrokenPipeError("persistent display failure")

sys.stdout = PersistentBrokenStdout()
sys.argv = ["run_step1", "replacement-report", "--generation-id", "{generation_id}"]
runpy.run_module("investment_orchestrator.cli.run_step1", run_name="__main__")
'''
    process = subprocess.run(
        [sys.executable, "-c", launcher],
        cwd=root,
        capture_output=True,
        env=_cli_environment(root),
        timeout=30,
        check=False,
    )
    assert process.returncode == 0, process.stderr.decode(errors="replace")
    assert process.stderr == b""
    _assert_cli_report(root)


def test_runtime_non_consumption_and_manual_dispatch_allowlist_are_exact() -> None:
    source_root = Path(__file__).resolve().parents[2] / "src/investment_orchestrator"
    report_path = source_root / "research/replacement_report.py"
    cli_path = source_root / "cli/run_step1.py"
    permitted = {report_path, cli_path}
    consumers: list[Path] = []
    for path in source_root.rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        if (
            "replacement_report" in text
            or "r2f_single_file_validated_memo_report_v1" in text
        ) and path not in permitted:
            consumers.append(path)
    assert consumers == []

    cli_source = cli_path.read_text(encoding="utf-8")
    assert cli_source.count(
        "from investment_orchestrator.research.replacement_report import replacement_report"
    ) == 1
    assert cli_source.count("if args.command == \"replacement-report\"") == 1
    report_source = report_path.read_text(encoding="utf-8")
    for forbidden in (
        "evaluate_research_availability",
        "research_degraded_mode_decision",
        "run_weekly",
        "step2_",
        "step3_",
        "step4_",
        "final_execution_safety",
        "compile_research_handoff",
        "submit_order",
        "broker_client",
        "quarantine",
        "candidate_observation_report",
        "order candidate",
        "actionable preview",
        "NEW_BUY",
        "SELL",
        "ORDER_COMPILATION",
        "listdir(",
        "scandir(",
        ".iterdir(",
        ".glob(",
        ".rglob(",
    ):
        assert forbidden not in report_source


def test_directory_marker_and_binding_protocol_is_completely_removed() -> None:
    source = Path(report.__file__).read_text(encoding="utf-8")
    for obsolete in (
        ".report_in_progress",
        "report_generation_binding.json",
        "validated_memo_envelope.json",
        "REPORT_BINDING",
        "IN_PROGRESS",
        "remove_in_progress_marker",
        "create_report_directory",
    ):
        assert obsolete not in source


def test_documentation_freezes_single_file_no_replace_rename_contract() -> None:
    document = Path(__file__).resolve().parents[2] / "docs/r2f1b_step1_replacement_report.md"
    text = " ".join(document.read_text(encoding="utf-8").split())
    for required in (
        "manual immutable single-file report-only validated-memo publication",
        "--generation-id <64-lowercase-hex>",
        "<report-id>.json",
        "report_attempts/",
        ".attempt-<unguessable>.tmp",
        "r2f_validated_memo_report_identity_v2",
        "r2f_single_file_validated_memo_report_v1",
        "renameat2(RENAME_NOREPLACE)",
        "no fallback to ordinary `rename()` or `os.replace()`",
        "Production performs no name-based cleanup or rollback deletion",
        "byte-identical different-inode final is not reuse",
        "Automatic cleanup is intentionally out of scope",
        "does not scan directories",
        "does not set weekly state",
        "creates no `NEW_BUY`, `SELL`, or `ORDER_COMPILATION` permission",
    ):
        assert required in text
    for obsolete in (
        "report_generation_binding.json",
        ".report_in_progress",
        "exact two-regular-file inventory",
        "r2f_validated_memo_report_identity_v1",
        "atomic descriptor-relative no-replace hard link",
    ):
        assert obsolete not in text


def test_state_action_matrix_remains_unchanged_and_nonactionable() -> None:
    from investment_orchestrator.state.research_availability import (
        STRICT_FRESH_GROUNDED_MEMO_NON_ACTIONABLE,
        _ALLOWED_ACTIONS_BY_STATE,
    )

    assert _ALLOWED_ACTIONS_BY_STATE[STRICT_FRESH_GROUNDED_MEMO_NON_ACTIONABLE] == (
        "HOLD",
        "NO_TRADE",
    )
