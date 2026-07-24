"""WS01d deterministic report construction and atomic publication tests."""

from __future__ import annotations

import ast
import base64
from collections.abc import Mapping
import copy
from dataclasses import FrozenInstanceError, dataclass, fields
from datetime import date
import hashlib
import inspect
import json
import os
from pathlib import Path
import pickle
import re
import stat
from concurrent.futures import ThreadPoolExecutor
from types import MappingProxyType
from typing import Any

from jsonschema import Draft202012Validator
import pytest
import yaml

from investment_orchestrator.observability import (
    ltetf_target_architecture_gap_report as gap,
)
from investment_orchestrator.observability import weekly_shadow_01_contracts as contracts
from investment_orchestrator.observability import weekly_shadow_01_package_builder as builder
from investment_orchestrator.observability import weekly_shadow_01_report_publisher as publisher
from investment_orchestrator.observability import weekly_shadow_01_response_validator as validator
from investment_orchestrator.research import replacement_observation as r2f


_REPORT_FILENAME = "weekly_shadow_01_analyst_report.json"
_SUMMARY_FILENAME = "weekly_shadow_01_run_summary.json"
_FILENAMES = (_REPORT_FILENAME, _SUMMARY_FILENAME)
_NEGATIVE_AUTHORITY = {
    "authority_effect": "none",
    "permission_effect": "none",
    "approval_eligible": False,
    "precompile_eligible": False,
    "order_eligible": False,
    "portfolio_effect": "none",
    "order_path_effect": "none",
    "execution_authority": False,
}


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


@dataclass(frozen=True)
class _ResponseContext:
    root: Path
    generation_id: str
    response: dict[str, Any]
    raw_response: bytes


@pytest.fixture(scope="module")
def response_context(tmp_path_factory: pytest.TempPathFactory) -> _ResponseContext:
    root = tmp_path_factory.mktemp("ws01d-repo")
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
    raw = _canonical(response)
    return _ResponseContext(root, generation_id, response, raw)


def _canonical(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def _identity(domain: bytes, value: object) -> str:
    return hashlib.sha256(domain + _canonical(value)).hexdigest()


def _output_root(tmp_path: Path) -> Path:
    output = tmp_path / "output"
    output.mkdir(mode=0o700)
    return output


def _call(
    context: _ResponseContext,
    output_root: object,
    *,
    raw: object | None = None,
    repository_root: object | None = None,
) -> object:
    return publisher.publish_weekly_shadow_report(
        context.generation_id,
        raw_response_bytes=context.raw_response if raw is None else raw,
        output_root=output_root,
        repository_root=context.root if repository_root is None else repository_root,
    )


def _expect_success(result: object, *, reused: bool) -> object:
    assert type(result) is publisher._WS01dResult
    assert result.ok is True
    assert result.reason_code is None
    assert type(result.value) is publisher._PublicationReceipt
    assert result.value.publication_reused is reused
    return result.value


def _expect_failure(result: object, reason_code: str) -> None:
    assert type(result) is publisher._WS01dResult
    assert result.ok is False
    assert result.value is None
    assert result.reason_code == reason_code
    assert type(result).__slots__ == ("ok", "value", "reason_code")
    assert not hasattr(result, "__dict__")


def _artifact_paths(output: Path, receipt: object) -> tuple[Path, Path, Path]:
    final = output / receipt.publication_relative_path
    return final, final / _REPORT_FILENAME, final / _SUMMARY_FILENAME


def _published_values(
    output: Path,
    receipt: object,
) -> tuple[dict[str, Any], dict[str, Any], bytes, bytes]:
    _, report_path, summary_path = _artifact_paths(output, receipt)
    report_raw = report_path.read_bytes()
    summary_raw = summary_path.read_bytes()
    return (
        json.loads(report_raw),
        json.loads(summary_raw),
        report_raw,
        summary_raw,
    )


def _create_relative_file(
    directory_descriptor: int,
    name: str,
    payload: bytes,
) -> None:
    descriptor = os.open(
        name,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
        0o600,
        dir_fd=directory_descriptor,
    )
    try:
        os.fchmod(descriptor, 0o600)
        offset = 0
        while offset < len(payload):
            written = os.write(descriptor, payload[offset:])
            assert written > 0
            offset += written
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _replace_relative_file(
    directory_descriptor: int,
    name: str,
    *,
    size: int,
) -> None:
    os.unlink(name, dir_fd=directory_descriptor)
    _create_relative_file(
        directory_descriptor,
        name,
        b"!" * size,
    )


def _only_final_directory(output: Path) -> Path:
    generations = tuple((output / "reports").iterdir())
    assert len(generations) == 1
    return generations[0]


def _read_relative_file(
    directory_descriptor: int,
    name: str,
) -> bytes:
    descriptor = os.open(
        name,
        os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC,
        dir_fd=directory_descriptor,
    )
    try:
        chunks: list[bytes] = []
        while True:
            chunk = os.read(descriptor, 65_536)
            if not chunk:
                return b"".join(chunks)
            chunks.append(chunk)
    finally:
        os.close(descriptor)


def _materialize_identical_collision_generation(
    source_parent_descriptor: int,
    source_name: str,
    destination_parent_descriptor: int,
    destination_name: str,
) -> None:
    source_descriptor = os.open(
        source_name,
        os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC,
        dir_fd=source_parent_descriptor,
    )
    destination_descriptor = -1
    try:
        os.mkdir(
            destination_name,
            0o700,
            dir_fd=destination_parent_descriptor,
        )
        destination_descriptor = os.open(
            destination_name,
            os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC,
            dir_fd=destination_parent_descriptor,
        )
        os.fchmod(destination_descriptor, 0o700)
        for artifact_name in _FILENAMES:
            _create_relative_file(
                destination_descriptor,
                artifact_name,
                _read_relative_file(source_descriptor, artifact_name),
            )
        os.fsync(destination_descriptor)
        os.fsync(destination_parent_descriptor)
    finally:
        if destination_descriptor >= 0:
            os.close(destination_descriptor)
        os.close(source_descriptor)


def _install_real_eexist_collision(
    monkeypatch: pytest.MonkeyPatch,
) -> list[bool]:
    original = publisher._rename_attempt_to_final_noreplace
    collision_observed: list[bool] = []

    def collide(
        source_parent_descriptor: int,
        source_name: str,
        destination_parent_descriptor: int,
        destination_name: str,
    ) -> bool:
        _materialize_identical_collision_generation(
            source_parent_descriptor,
            source_name,
            destination_parent_descriptor,
            destination_name,
        )
        committed = original(
            source_parent_descriptor,
            source_name,
            destination_parent_descriptor,
            destination_name,
        )
        assert committed is False
        collision_observed.append(True)
        return committed

    monkeypatch.setattr(
        publisher,
        "_rename_attempt_to_final_noreplace",
        collide,
    )
    return collision_observed


def _displace_and_replace_directory_entry(
    path: Path,
    *,
    displaced: Path,
    replacement: str,
) -> None:
    path.rename(displaced)
    if replacement == "directory":
        path.mkdir(mode=0o700)
    elif replacement == "symlink":
        path.symlink_to(displaced.name, target_is_directory=True)
    elif replacement == "file":
        path.write_bytes(b"x")
        path.chmod(0o600)
    else:
        assert replacement == "missing"


def _assert_replacement_kind(path: Path, replacement: str) -> None:
    if replacement == "missing":
        assert not path.exists()
        assert not path.is_symlink()
    elif replacement == "directory":
        assert path.is_dir()
        assert not path.is_symlink()
    elif replacement == "symlink":
        assert path.is_symlink()
    else:
        assert replacement == "file"
        assert path.is_file()
        assert not path.is_symlink()


def test_public_namespace_signature_and_annotations_are_exact() -> None:
    assert publisher.__all__ == ("publish_weekly_shadow_report",)
    assert {
        name for name in vars(publisher) if not name.startswith("_")
    } == set(publisher.__all__)
    signature = inspect.signature(publisher.publish_weekly_shadow_report)
    assert tuple(signature.parameters) == (
        "generation_id",
        "raw_response_bytes",
        "output_root",
        "repository_root",
    )
    assert signature.parameters["generation_id"].kind is inspect.Parameter.POSITIONAL_OR_KEYWORD
    for name in ("raw_response_bytes", "output_root", "repository_root"):
        assert signature.parameters[name].kind is inspect.Parameter.KEYWORD_ONLY
    assert signature.parameters["repository_root"].default is None
    assert signature.return_annotation == "_WS01dResult"


def test_static_import_boundary_is_exact_and_module_binding_is_used() -> None:
    path = Path(publisher.__file__)
    tree = ast.parse(path.read_text(encoding="utf-8"))
    bindings = [
        (node.module, tuple((alias.name, alias.asname) for alias in node.names))
        for node in tree.body
        if isinstance(node, ast.ImportFrom)
        and node.module == "investment_orchestrator.observability"
    ]
    assert bindings == [
        (
            "investment_orchestrator.observability",
            (("weekly_shadow_01_response_validator", "_response_validator"),),
        )
    ]
    source = path.read_text(encoding="utf-8")
    assert "_response_validator._validate_analyst_response_for_downstream" in source
    assert "weekly_shadow_01_package_builder" not in source
    assert "weekly_shadow_01_source_adapter" not in source
    assert "weekly_shadow_01_contracts" not in source
    assert "importlib" not in source


@pytest.mark.parametrize(
    "keyword",
    (
        "snapshot",
        "package",
        "prompt",
        "render_result",
        "validated_response",
        "downstream_context",
        "capture",
        "validation_record",
        "report",
        "run_summary",
        "publication_manifest",
        "pointer_target",
        "writer",
        "callback",
        "descriptor",
        "dependency",
    ),
)
def test_public_api_rejects_detached_authority_keywords(
    response_context: _ResponseContext,
    tmp_path: Path,
    keyword: str,
) -> None:
    output = _output_root(tmp_path)
    arguments = {
        "raw_response_bytes": response_context.raw_response,
        "output_root": output,
        "repository_root": response_context.root,
        keyword: object(),
    }
    with pytest.raises(TypeError):
        publisher.publish_weekly_shadow_report(
            response_context.generation_id,
            **arguments,
        )
    assert list(output.iterdir()) == []


def test_public_api_rejects_extra_positional_arguments(
    response_context: _ResponseContext,
    tmp_path: Path,
) -> None:
    output = _output_root(tmp_path)
    with pytest.raises(TypeError):
        publisher.publish_weekly_shadow_report(
            response_context.generation_id,
            response_context.raw_response,
            output,
        )
    assert list(output.iterdir()) == []


class _OneShotPath:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.calls = 0

    def __fspath__(self) -> str:
        self.calls += 1
        if self.calls > 1:
            raise AssertionError("path selector normalized more than once")
        return os.fspath(self.path)


def test_repository_and_output_pathlikes_are_each_normalized_exactly_once(
    response_context: _ResponseContext,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository = _OneShotPath(response_context.root)
    output_path = _output_root(tmp_path)
    output = _OneShotPath(output_path)
    observed_roots: list[object] = []
    original = validator._validate_analyst_response_for_downstream

    def observe_root(*args: object, **kwargs: object) -> object:
        observed_roots.append(kwargs["repository_root"])
        return original(*args, **kwargs)

    monkeypatch.setattr(
        publisher._response_validator,
        "_validate_analyst_response_for_downstream",
        observe_root,
    )
    receipt = _expect_success(
        _call(
            response_context,
            output,
            repository_root=repository,
        ),
        reused=False,
    )
    assert repository.calls == 1
    assert output.calls == 1
    assert len(observed_roots) == 1
    assert type(observed_roots[0]) is type(Path())
    assert observed_roots[0] == response_context.root
    assert receipt.publication_relative_path.startswith("reports/")


def test_one_private_ws01c_pipeline_call_and_exact_path_handoff(
    response_context: _ResponseContext,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    output = _output_root(tmp_path)
    calls: list[tuple[object, object, object]] = []
    original = validator._validate_analyst_response_for_downstream

    def count(
        generation_id: object,
        *,
        raw_response_bytes: object,
        repository_root: object,
    ) -> object:
        calls.append((generation_id, raw_response_bytes, repository_root))
        return original(
            generation_id,
            raw_response_bytes=raw_response_bytes,
            repository_root=repository_root,
        )

    monkeypatch.setattr(
        publisher._response_validator,
        "_validate_analyst_response_for_downstream",
        count,
    )
    _expect_success(_call(response_context, output), reused=False)
    assert calls == [
        (
            response_context.generation_id,
            response_context.raw_response,
            response_context.root,
        )
    ]
    assert type(calls[0][2]) is type(Path())


def test_success_constructs_exact_valid_canonical_artifacts_and_receipt(
    response_context: _ResponseContext,
    tmp_path: Path,
) -> None:
    output = _output_root(tmp_path)
    receipt = _expect_success(_call(response_context, output), reused=False)
    final, report_path, summary_path = _artifact_paths(output, receipt)
    report, summary, report_raw, summary_raw = _published_values(output, receipt)

    assert receipt.artifact_filenames == _FILENAMES
    assert receipt.publication_relative_path == (
        f"reports/{receipt.report_identity_sha256}"
    )
    assert re.fullmatch(r"[0-9a-f]{64}", final.name)
    assert set(path.name for path in final.iterdir()) == set(_FILENAMES)
    assert set(report) == {
        "schema_version",
        "run_id",
        "input_package_identity_sha256",
        "response_capture_identity_sha256",
        "validation_identity_sha256",
        "code_owned_status",
        "validated_analyst_content",
        "negative_authority_profile",
        "report_identity_sha256",
    }
    assert set(summary) == {
        "schema_version",
        "run_id",
        "run_status",
        "validation_status",
        "publication_status",
        "blocking_reason_codes",
        "report_identity_sha256",
        "negative_authority_profile",
        "run_summary_identity_sha256",
    }
    assert report["code_owned_status"] == {
        "run_status": "ANALYSIS_COMPLETE",
        "validation_status": "VALID",
        "publication_status": "PUBLISHED",
        "blocking_reason_codes": [],
    }
    assert summary["run_status"] == "ANALYSIS_COMPLETE"
    assert summary["validation_status"] == "VALID"
    assert summary["publication_status"] == "PUBLISHED"
    assert summary["blocking_reason_codes"] == []
    assert report["validated_analyst_content"] == {
        name: response_context.response[name]
        for name in (
            "analyst_conclusion",
            "analyst_confidence",
            "analytical_sections",
            "analyst_limitation_codes",
        )
    }
    assert report["negative_authority_profile"] == _NEGATIVE_AUTHORITY
    assert summary["negative_authority_profile"] == _NEGATIVE_AUTHORITY
    assert report_raw == _canonical(report)
    assert summary_raw == _canonical(summary)
    report_unsigned = {
        key: value
        for key, value in report.items()
        if key != "report_identity_sha256"
    }
    summary_unsigned = {
        key: value
        for key, value in summary.items()
        if key != "run_summary_identity_sha256"
    }
    assert report["report_identity_sha256"] == _identity(
        b"weekly_shadow_01_report_v1\0",
        report_unsigned,
    )
    assert summary["run_summary_identity_sha256"] == _identity(
        b"weekly_shadow_01_run_summary_v1\0",
        summary_unsigned,
    )
    assert receipt.report_identity_sha256 == report["report_identity_sha256"]
    assert receipt.run_summary_identity_sha256 == summary[
        "run_summary_identity_sha256"
    ]
    assert summary["report_identity_sha256"] == report[
        "report_identity_sha256"
    ]
    assert len(report_raw) <= 262_144
    assert len(summary_raw) <= 65_536
    Draft202012Validator(
        json.loads(
            (response_context.root / contracts.SCHEMA_FILENAME_BY_VERSION[
                "weekly_shadow_01_analyst_report_v1"
            ]).read_bytes()
        )
    ).validate(report)
    Draft202012Validator(
        json.loads(
            (response_context.root / contracts.SCHEMA_FILENAME_BY_VERSION[
                "weekly_shadow_01_run_summary_v1"
            ]).read_bytes()
        )
    ).validate(summary)
    for path in (final, report_path, summary_path):
        status = path.stat(follow_symlinks=False)
        assert stat.S_ISDIR(status.st_mode) if path == final else stat.S_ISREG(
            status.st_mode
        )
    assert stat.S_IMODE(final.stat().st_mode) == 0o700
    for path in (report_path, summary_path):
        status = path.stat()
        assert stat.S_IMODE(status.st_mode) == 0o600
        assert status.st_nlink == 1


def test_no_raw_capture_validation_prompt_package_or_pointer_artifact_is_published(
    response_context: _ResponseContext,
    tmp_path: Path,
) -> None:
    output = _output_root(tmp_path)
    receipt = _expect_success(_call(response_context, output), reused=False)
    final, _, summary_path = _artifact_paths(output, receipt)
    assert tuple(sorted(path.name for path in final.iterdir())) == tuple(
        sorted(_FILENAMES)
    )
    summary_raw = summary_path.read_bytes()
    forbidden = (
        response_context.raw_response,
        base64.b64encode(response_context.raw_response),
        response_context.raw_response.hex().encode("ascii"),
    )
    assert all(value not in summary_raw for value in forbidden)
    assert not any(
        name.lower() in {"latest", "current", "active", "pointer", "index", "manifest"}
        for name in (
            path.name
            for path in output.rglob("*")
        )
    )


def test_existing_identical_generation_is_independently_reused(
    response_context: _ResponseContext,
    tmp_path: Path,
) -> None:
    output = _output_root(tmp_path)
    first = _expect_success(_call(response_context, output), reused=False)
    final, report_path, summary_path = _artifact_paths(output, first)
    before = (
        final.stat().st_ino,
        report_path.read_bytes(),
        summary_path.read_bytes(),
    )
    second = _expect_success(_call(response_context, output), reused=True)
    assert second.report_identity_sha256 == first.report_identity_sha256
    assert second.run_summary_identity_sha256 == first.run_summary_identity_sha256
    assert (
        final.stat().st_ino,
        report_path.read_bytes(),
        summary_path.read_bytes(),
    ) == before


def test_determinism_is_independent_of_attempt_name_and_output_root(
    response_context: _ResponseContext,
    tmp_path: Path,
) -> None:
    first_root = tmp_path / "first"
    second_root = tmp_path / "second"
    first_root.mkdir(mode=0o700)
    second_root.mkdir(mode=0o700)
    first = _expect_success(_call(response_context, first_root), reused=False)
    second = _expect_success(_call(response_context, second_root), reused=False)
    first_values = _published_values(first_root, first)
    second_values = _published_values(second_root, second)
    assert first_values == second_values
    assert (
        first.report_identity_sha256,
        first.run_summary_identity_sha256,
        first.publication_relative_path,
        first.artifact_filenames,
    ) == (
        second.report_identity_sha256,
        second.run_summary_identity_sha256,
        second.publication_relative_path,
        second.artifact_filenames,
    )


def test_different_valid_content_produces_a_distinct_generation(
    response_context: _ResponseContext,
    tmp_path: Path,
) -> None:
    output = _output_root(tmp_path)
    first = _expect_success(_call(response_context, output), reused=False)
    changed = copy.deepcopy(response_context.response)
    changed["analytical_sections"]["observations"][0]["statement"] = (
        "The supplied evidence supports a different bounded observation."
    )
    second = _expect_success(
        _call(response_context, output, raw=_canonical(changed)),
        reused=False,
    )
    assert first.report_identity_sha256 != second.report_identity_sha256
    assert first.run_summary_identity_sha256 != second.run_summary_identity_sha256
    reports = output / "reports"
    assert {
        path.name for path in reports.iterdir()
    } == {first.report_identity_sha256, second.report_identity_sha256}


def test_upstream_failure_preserves_reason_and_performs_no_output_mutation(
    response_context: _ResponseContext,
    tmp_path: Path,
) -> None:
    output = _output_root(tmp_path)
    result = _call(response_context, output, raw=b"{}")
    _expect_failure(result, "WS01_BR_ARTIFACT_ECHO_INCOMPLETE")
    assert list(output.iterdir()) == []


def test_both_artifacts_validate_before_any_filesystem_mutation(
    response_context: _ResponseContext,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    output = _output_root(tmp_path)

    def fail_prepare(_context: object) -> object:
        raise publisher._WS01dFailure("WS01_BR_REPORT_CONSTRUCTION_FAILED")

    monkeypatch.setattr(publisher, "_prepare_artifacts", fail_prepare)
    result = _call(response_context, output)
    _expect_failure(result, "WS01_BR_REPORT_CONSTRUCTION_FAILED")
    assert list(output.iterdir()) == []


@pytest.mark.parametrize("kind", ("missing", "file", "symlink"))
def test_output_root_must_be_an_existing_real_directory(
    response_context: _ResponseContext,
    tmp_path: Path,
    kind: str,
) -> None:
    target = tmp_path / "target"
    if kind == "file":
        target.write_bytes(b"x")
    elif kind == "symlink":
        real = tmp_path / "real"
        real.mkdir()
        target.symlink_to(real, target_is_directory=True)
    result = _call(response_context, target)
    _expect_failure(result, "WS01_BR_PUBLICATION_FAILED")
    if kind == "missing":
        assert not target.exists()


@pytest.mark.parametrize("child", ("report_attempts", "reports"))
def test_fixed_namespace_symlinks_fail_closed(
    response_context: _ResponseContext,
    tmp_path: Path,
    child: str,
) -> None:
    output = _output_root(tmp_path)
    outside = tmp_path / "outside"
    outside.mkdir()
    (output / child).symlink_to(outside, target_is_directory=True)
    result = _call(response_context, output)
    _expect_failure(result, "WS01_BR_PUBLICATION_FAILED")
    assert list(outside.iterdir()) == []


@pytest.mark.parametrize("child", ("report_attempts", "reports"))
def test_unexpected_fixed_namespace_child_type_fails_closed(
    response_context: _ResponseContext,
    tmp_path: Path,
    child: str,
) -> None:
    output = _output_root(tmp_path)
    (output / child).write_bytes(b"not a directory")
    result = _call(response_context, output)
    _expect_failure(result, "WS01_BR_PUBLICATION_FAILED")


def test_short_writes_complete_without_changing_bytes(
    response_context: _ResponseContext,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    output = _output_root(tmp_path)
    original = publisher._os.write
    calls = 0

    def short_write(descriptor: int, payload: object) -> int:
        nonlocal calls
        calls += 1
        return original(descriptor, payload[:7])

    monkeypatch.setattr(publisher._os, "write", short_write)
    receipt = _expect_success(_call(response_context, output), reused=False)
    assert calls > 2
    report, summary, report_raw, summary_raw = _published_values(output, receipt)
    assert report_raw == _canonical(report)
    assert summary_raw == _canonical(summary)


def test_zero_length_write_fails_precommit_without_final_generation(
    response_context: _ResponseContext,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    output = _output_root(tmp_path)
    monkeypatch.setattr(publisher._os, "write", lambda *_args, **_kwargs: 0)
    result = _call(response_context, output)
    _expect_failure(result, "WS01_BR_PUBLICATION_FAILED")
    reports = output / "reports"
    assert reports.is_dir()
    assert list(reports.iterdir()) == []


@pytest.mark.parametrize(
    ("exception_type", "reason_code"),
    (
        (publisher._RenamePrimitiveUnavailable, "WS01_BR_PUBLICATION_FAILED"),
        (publisher._RenameDeterministicFailure, "WS01_BR_PUBLICATION_FAILED"),
        (RuntimeError, "WS01_BR_PUBLICATION_AMBIGUOUS"),
    ),
)
def test_rename_failure_taxonomy_is_fail_closed(
    response_context: _ResponseContext,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    exception_type: type[BaseException],
    reason_code: str,
) -> None:
    output = _output_root(tmp_path)

    def fail(*_args: object, **_kwargs: object) -> object:
        if exception_type is publisher._RenameDeterministicFailure:
            raise exception_type(5)
        raise exception_type()

    monkeypatch.setattr(
        publisher,
        "_rename_attempt_to_final_noreplace",
        fail,
    )
    result = _call(response_context, output)
    _expect_failure(result, reason_code)
    assert list((output / "reports").iterdir()) == []


@pytest.mark.parametrize("post_commit_directory_fsync", (1, 2))
def test_post_rename_parent_fsync_failure_is_ambiguous_and_not_deleted(
    response_context: _ResponseContext,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    post_commit_directory_fsync: int,
) -> None:
    output = _output_root(tmp_path)
    original = publisher._fsync_directory
    rename_completed = False
    post_calls = 0
    original_rename = publisher._rename_attempt_to_final_noreplace

    def rename(*args: object, **kwargs: object) -> bool:
        nonlocal rename_completed
        result = original_rename(*args, **kwargs)
        rename_completed = result
        return result

    def fsync(descriptor: int) -> None:
        nonlocal post_calls
        if rename_completed:
            post_calls += 1
            if post_calls == post_commit_directory_fsync:
                raise publisher._WS01dFailure("WS01_BR_PUBLICATION_FAILED")
        original(descriptor)

    monkeypatch.setattr(
        publisher,
        "_rename_attempt_to_final_noreplace",
        rename,
    )
    monkeypatch.setattr(publisher, "_fsync_directory", fsync)
    result = _call(response_context, output)
    _expect_failure(result, "WS01_BR_PUBLICATION_AMBIGUOUS")
    reports = output / "reports"
    assert len(list(reports.iterdir())) == 1


def test_post_rename_final_verification_failure_is_ambiguous_and_not_deleted(
    response_context: _ResponseContext,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    output = _output_root(tmp_path)
    original = publisher._verify_final_generation
    calls = 0

    def fail_after_rename(*args: object, **kwargs: object) -> None:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise publisher._WS01dFailure(
                "WS01_BR_IMMUTABLE_VERIFICATION_FAILED"
            )
        original(*args, **kwargs)

    monkeypatch.setattr(publisher, "_verify_final_generation", fail_after_rename)
    result = _call(response_context, output)
    _expect_failure(result, "WS01_BR_PUBLICATION_AMBIGUOUS")
    assert len(list((output / "reports").iterdir())) == 1


def test_output_root_replacement_before_commit_fails_without_final_generation(
    response_context: _ResponseContext,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    output = _output_root(tmp_path)
    displaced = tmp_path / "displaced"
    original = publisher._create_attempt_directory

    def replace_root(*args: object, **kwargs: object) -> object:
        attempt = original(*args, **kwargs)
        output.rename(displaced)
        output.mkdir(mode=0o700)
        return attempt

    monkeypatch.setattr(publisher, "_create_attempt_directory", replace_root)
    result = _call(response_context, output)
    _expect_failure(result, "WS01_BR_PUBLICATION_FAILED")
    assert list(output.iterdir()) == []
    assert list((displaced / "reports").iterdir()) == []


def test_output_root_replacement_after_rename_is_ambiguous_and_not_deleted(
    response_context: _ResponseContext,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    output = _output_root(tmp_path)
    displaced = tmp_path / "displaced"
    original = publisher._rename_attempt_to_final_noreplace

    def rename_then_replace(*args: object, **kwargs: object) -> bool:
        committed = original(*args, **kwargs)
        assert committed is True
        output.rename(displaced)
        output.mkdir(mode=0o700)
        return committed

    monkeypatch.setattr(
        publisher,
        "_rename_attempt_to_final_noreplace",
        rename_then_replace,
    )
    result = _call(response_context, output)
    _expect_failure(result, "WS01_BR_PUBLICATION_AMBIGUOUS")
    assert list(output.iterdir()) == []
    assert len(list((displaced / "reports").iterdir())) == 1


def test_reports_directory_replacement_before_commit_fails_closed(
    response_context: _ResponseContext,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    output = _output_root(tmp_path)
    displaced = output / "reports-displaced"
    original = publisher._create_attempt_directory

    def replace_reports(*args: object, **kwargs: object) -> object:
        attempt = original(*args, **kwargs)
        (output / "reports").rename(displaced)
        (output / "reports").mkdir(mode=0o700)
        return attempt

    monkeypatch.setattr(publisher, "_create_attempt_directory", replace_reports)
    result = _call(response_context, output)
    _expect_failure(result, "WS01_BR_PUBLICATION_FAILED")
    assert list((output / "reports").iterdir()) == []
    assert list(displaced.iterdir()) == []


@pytest.mark.parametrize("regular_file_fsync_number", (1, 2))
def test_each_artifact_fsync_failure_is_precommit_and_creates_no_final(
    response_context: _ResponseContext,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    regular_file_fsync_number: int,
) -> None:
    output = _output_root(tmp_path)
    original = publisher._os.fsync
    regular_calls = 0

    def fail_selected(descriptor: int) -> None:
        nonlocal regular_calls
        if stat.S_ISREG(os.fstat(descriptor).st_mode):
            regular_calls += 1
            if regular_calls == regular_file_fsync_number:
                raise OSError("fsync fault")
        original(descriptor)

    monkeypatch.setattr(publisher._os, "fsync", fail_selected)
    result = _call(response_context, output)
    _expect_failure(result, "WS01_BR_PUBLICATION_FAILED")
    assert list((output / "reports").iterdir()) == []


def test_attempt_directory_fsync_failure_is_precommit(
    response_context: _ResponseContext,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    output = _output_root(tmp_path)
    original = publisher._fsync_directory

    def fail_attempt(descriptor: int) -> None:
        if set(os.listdir(descriptor)) == set(_FILENAMES):
            raise publisher._WS01dFailure("WS01_BR_PUBLICATION_FAILED")
        original(descriptor)

    monkeypatch.setattr(publisher, "_fsync_directory", fail_attempt)
    result = _call(response_context, output)
    _expect_failure(result, "WS01_BR_PUBLICATION_FAILED")
    assert list((output / "reports").iterdir()) == []


def test_staged_readback_mismatch_is_detected_before_rename(
    response_context: _ResponseContext,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    output = _output_root(tmp_path)
    original = publisher._create_artifact_file
    created = 0

    def corrupt_after_create(*args: object, **kwargs: object) -> int:
        nonlocal created
        descriptor = original(*args, **kwargs)
        created += 1
        if created == 2:
            os.lseek(descriptor, 0, os.SEEK_SET)
            os.write(descriptor, b"!")
            os.fsync(descriptor)
        return descriptor

    monkeypatch.setattr(publisher, "_create_artifact_file", corrupt_after_create)
    result = _call(response_context, output)
    _expect_failure(result, "WS01_BR_IMMUTABLE_VERIFICATION_FAILED")
    assert list((output / "reports").iterdir()) == []


def test_attempt_inventory_mismatch_is_detected_before_rename(
    response_context: _ResponseContext,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    output = _output_root(tmp_path)
    original = publisher._create_artifact_file
    created = 0

    def add_extra(directory: object, *args: object, **kwargs: object) -> int:
        nonlocal created
        descriptor = original(directory, *args, **kwargs)
        created += 1
        if created == 2:
            extra = os.open(
                "unexpected",
                os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                0o600,
                dir_fd=directory.descriptor,
            )
            os.close(extra)
        return descriptor

    monkeypatch.setattr(publisher, "_create_artifact_file", add_extra)
    result = _call(response_context, output)
    _expect_failure(result, "WS01_BR_IMMUTABLE_VERIFICATION_FAILED")
    assert list((output / "reports").iterdir()) == []


def test_staged_closing_rejects_late_extra_entry_before_rename(
    response_context: _ResponseContext,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    output = _output_root(tmp_path)
    original_verify = publisher._verify_staged_attempt
    original_rename = publisher._rename_attempt_to_final_noreplace
    verify_calls = 0
    rename_calls = 0

    def verify_then_mutate(*args: object, **kwargs: object) -> object:
        nonlocal verify_calls
        verified = original_verify(*args, **kwargs)
        verify_calls += 1
        if verify_calls == 2:
            _create_relative_file(
                verified.directory.descriptor,
                "unexpected-after-staged-read",
                b"x",
            )
        return verified

    def count_rename(*args: object, **kwargs: object) -> bool:
        nonlocal rename_calls
        rename_calls += 1
        return original_rename(*args, **kwargs)

    monkeypatch.setattr(publisher, "_verify_staged_attempt", verify_then_mutate)
    monkeypatch.setattr(
        publisher,
        "_rename_attempt_to_final_noreplace",
        count_rename,
    )
    result = _call(response_context, output)
    _expect_failure(result, "WS01_BR_IMMUTABLE_VERIFICATION_FAILED")
    assert verify_calls == 2
    assert rename_calls == 0
    assert list((output / "reports").iterdir()) == []


@pytest.mark.parametrize("artifact_name", _FILENAMES)
def test_staged_closing_rejects_late_same_name_artifact_replacement(
    response_context: _ResponseContext,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    artifact_name: str,
) -> None:
    output = _output_root(tmp_path)
    original_verify = publisher._verify_staged_attempt
    original_rename = publisher._rename_attempt_to_final_noreplace
    verify_calls = 0
    rename_calls = 0

    def verify_then_replace(*args: object, **kwargs: object) -> object:
        nonlocal verify_calls
        verified = original_verify(*args, **kwargs)
        verify_calls += 1
        if verify_calls == 2:
            entry = (
                verified.report
                if artifact_name == _REPORT_FILENAME
                else verified.summary
            )
            _replace_relative_file(
                verified.directory.descriptor,
                artifact_name,
                size=entry.witness.size,
            )
        return verified

    def count_rename(*args: object, **kwargs: object) -> bool:
        nonlocal rename_calls
        rename_calls += 1
        return original_rename(*args, **kwargs)

    monkeypatch.setattr(publisher, "_verify_staged_attempt", verify_then_replace)
    monkeypatch.setattr(
        publisher,
        "_rename_attempt_to_final_noreplace",
        count_rename,
    )
    result = _call(response_context, output)
    _expect_failure(result, "WS01_BR_IMMUTABLE_VERIFICATION_FAILED")
    assert verify_calls == 2
    assert rename_calls == 0
    assert list((output / "reports").iterdir()) == []


@pytest.mark.parametrize(
    "mutation_point",
    (
        "after-initial-inventory",
        "after-first-artifact-read",
        "after-second-artifact-read",
        "immediately-before-closing",
    ),
)
def test_new_publication_closing_rejects_late_extra_entry_as_ambiguous(
    response_context: _ResponseContext,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    mutation_point: str,
) -> None:
    output = _output_root(tmp_path)
    original_open = publisher._open_artifact_for_verification
    original_closing = publisher._verify_generation_closing
    open_calls = 0
    mutated = False

    def open_with_mutation(
        directory: object,
        name: str,
        expected_size: int,
        *,
        owner: object,
    ) -> object:
        nonlocal open_calls, mutated
        open_calls += 1
        if (
            mutation_point == "after-initial-inventory"
            and open_calls == 1
        ):
            _create_relative_file(
                directory.descriptor,
                "unexpected-after-final-inventory",
                b"x",
            )
            mutated = True
        verified = original_open(
            directory,
            name,
            expected_size,
            owner=owner,
        )
        if (
            mutation_point == "after-first-artifact-read"
            and open_calls == 1
        ) or (
            mutation_point == "after-second-artifact-read"
            and open_calls == 2
        ):
            _create_relative_file(
                directory.descriptor,
                "unexpected-after-final-read",
                b"x",
            )
            mutated = True
        return verified

    def mutate_before_closing(generation: object) -> None:
        nonlocal mutated
        if (
            mutation_point == "immediately-before-closing"
            and re.fullmatch(r"[0-9a-f]{64}", generation.directory.name)
            and not mutated
        ):
            _create_relative_file(
                generation.directory.descriptor,
                "unexpected-before-final-closing",
                b"x",
            )
            mutated = True
        original_closing(generation)

    monkeypatch.setattr(
        publisher,
        "_open_artifact_for_verification",
        open_with_mutation,
    )
    monkeypatch.setattr(
        publisher,
        "_verify_generation_closing",
        mutate_before_closing,
    )
    result = _call(response_context, output)
    _expect_failure(result, "WS01_BR_PUBLICATION_AMBIGUOUS")
    assert mutated is True
    final = _only_final_directory(output)
    assert any(path.name.startswith("unexpected-") for path in final.iterdir())


@pytest.mark.parametrize("artifact_name", _FILENAMES)
def test_new_publication_closing_rejects_late_artifact_replacement_as_ambiguous(
    response_context: _ResponseContext,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    artifact_name: str,
) -> None:
    output = _output_root(tmp_path)
    original = publisher._open_artifact_for_verification
    replaced = False

    def read_then_replace(
        directory: object,
        name: str,
        expected_size: int,
        *,
        owner: object,
    ) -> object:
        nonlocal replaced
        verified = original(
            directory,
            name,
            expected_size,
            owner=owner,
        )
        if name == artifact_name:
            _replace_relative_file(
                directory.descriptor,
                name,
                size=verified.witness.size,
            )
            replaced = True
        return verified

    monkeypatch.setattr(
        publisher,
        "_open_artifact_for_verification",
        read_then_replace,
    )
    result = _call(response_context, output)
    _expect_failure(result, "WS01_BR_PUBLICATION_AMBIGUOUS")
    assert replaced is True
    final = _only_final_directory(output)
    assert (final / artifact_name).read_bytes().startswith(b"!")


@pytest.mark.parametrize(
    "replacement",
    ("missing", "directory", "symlink", "file"),
)
def test_new_publication_closing_rejects_final_directory_rebinding_as_ambiguous(
    response_context: _ResponseContext,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    replacement: str,
) -> None:
    output = _output_root(tmp_path)
    original = publisher._verify_final_generation
    displaced_name: str | None = None

    def verify_then_rebind(*args: object, **kwargs: object) -> object:
        nonlocal displaced_name
        verified = original(*args, **kwargs)
        directory = verified.directory
        displaced_name = f"displaced-{directory.name}"
        os.rename(
            directory.name,
            displaced_name,
            src_dir_fd=directory.parent_descriptor,
            dst_dir_fd=directory.parent_descriptor,
        )
        if replacement == "directory":
            os.mkdir(
                directory.name,
                0o700,
                dir_fd=directory.parent_descriptor,
            )
        elif replacement == "symlink":
            os.symlink(
                displaced_name,
                directory.name,
                dir_fd=directory.parent_descriptor,
            )
        elif replacement == "file":
            _create_relative_file(
                directory.parent_descriptor,
                directory.name,
                b"x",
            )
        return verified

    monkeypatch.setattr(
        publisher,
        "_verify_final_generation",
        verify_then_rebind,
    )
    result = _call(response_context, output)
    _expect_failure(result, "WS01_BR_PUBLICATION_AMBIGUOUS")
    reports = output / "reports"
    assert displaced_name is not None
    assert (reports / displaced_name).is_dir()


@pytest.mark.parametrize(
    "mutation",
    ("extra-entry", "report-replacement", "directory-rebind"),
)
def test_existing_final_reuse_closing_rejects_concurrent_mutation(
    response_context: _ResponseContext,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    mutation: str,
) -> None:
    output = _output_root(tmp_path)
    first = _expect_success(_call(response_context, output), reused=False)
    original = publisher._verify_final_generation
    verification_calls = 0

    def verify_then_mutate(*args: object, **kwargs: object) -> object:
        nonlocal verification_calls
        verified = original(*args, **kwargs)
        verification_calls += 1
        if verification_calls != 2:
            return verified
        directory = verified.directory
        if mutation == "extra-entry":
            _create_relative_file(
                directory.descriptor,
                "unexpected-during-reuse",
                b"x",
            )
        elif mutation == "report-replacement":
            _replace_relative_file(
                directory.descriptor,
                _REPORT_FILENAME,
                size=verified.report.witness.size,
            )
        else:
            os.rename(
                directory.name,
                f"displaced-{directory.name}",
                src_dir_fd=directory.parent_descriptor,
                dst_dir_fd=directory.parent_descriptor,
            )
        return verified

    monkeypatch.setattr(
        publisher,
        "_verify_final_generation",
        verify_then_mutate,
    )
    result = _call(response_context, output)
    _expect_failure(result, "WS01_BR_PUBLICATION_AMBIGUOUS")
    assert verification_calls == 2
    assert result.value is None
    assert first.report_identity_sha256 not in repr(result)


@pytest.mark.parametrize("route", ("new", "existing", "eexist"))
@pytest.mark.parametrize(
    "replacement",
    ("missing", "directory", "symlink", "file"),
)
def test_complete_closing_rejects_late_fixed_reports_entry_mutation(
    response_context: _ResponseContext,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    route: str,
    replacement: str,
) -> None:
    output = _output_root(tmp_path)
    if route == "existing":
        _expect_success(_call(response_context, output), reused=False)
    collision_observed = (
        _install_real_eexist_collision(monkeypatch)
        if route == "eexist"
        else []
    )
    original = publisher._verify_generation_closing
    displaced = output / "reports-displaced"
    mutated = False
    report_identity: str | None = None

    def close_then_mutate(generation: object) -> None:
        nonlocal mutated, report_identity
        original(generation)
        if (
            re.fullmatch(r"[0-9a-f]{64}", generation.directory.name)
            and not mutated
        ):
            report_identity = generation.directory.name
            _displace_and_replace_directory_entry(
                output / "reports",
                displaced=displaced,
                replacement=replacement,
            )
            mutated = True

    monkeypatch.setattr(
        publisher,
        "_verify_generation_closing",
        close_then_mutate,
    )
    result = _call(response_context, output)
    _expect_failure(result, "WS01_BR_PUBLICATION_AMBIGUOUS")
    assert mutated is True
    assert report_identity is not None
    assert (displaced / report_identity).is_dir()
    _assert_replacement_kind(output / "reports", replacement)
    assert collision_observed == ([True] if route == "eexist" else [])
    attempts = tuple((displaced.parent / "report_attempts").iterdir())
    assert len(attempts) == (1 if route == "eexist" else 0)


@pytest.mark.parametrize("route", ("new", "existing", "eexist"))
@pytest.mark.parametrize(
    "replacement",
    ("missing", "directory", "symlink", "file"),
)
def test_complete_closing_rejects_late_output_root_entry_mutation(
    response_context: _ResponseContext,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    route: str,
    replacement: str,
) -> None:
    output = _output_root(tmp_path)
    if route == "existing":
        _expect_success(_call(response_context, output), reused=False)
    collision_observed = (
        _install_real_eexist_collision(monkeypatch)
        if route == "eexist"
        else []
    )
    original = publisher._verify_generation_closing
    displaced = tmp_path / "output-displaced"
    mutated = False
    report_identity: str | None = None

    def close_then_mutate(generation: object) -> None:
        nonlocal mutated, report_identity
        original(generation)
        if (
            re.fullmatch(r"[0-9a-f]{64}", generation.directory.name)
            and not mutated
        ):
            report_identity = generation.directory.name
            _displace_and_replace_directory_entry(
                output,
                displaced=displaced,
                replacement=replacement,
            )
            mutated = True

    monkeypatch.setattr(
        publisher,
        "_verify_generation_closing",
        close_then_mutate,
    )
    result = _call(response_context, output)
    _expect_failure(result, "WS01_BR_PUBLICATION_AMBIGUOUS")
    assert mutated is True
    assert report_identity is not None
    assert (displaced / "reports" / report_identity).is_dir()
    _assert_replacement_kind(output, replacement)
    assert collision_observed == ([True] if route == "eexist" else [])
    attempts = tuple((displaced / "report_attempts").iterdir())
    assert len(attempts) == (1 if route == "eexist" else 0)


def test_stable_real_eexist_collision_reuses_verified_generation(
    response_context: _ResponseContext,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    output = _output_root(tmp_path)
    collision_observed = _install_real_eexist_collision(monkeypatch)
    receipt = _expect_success(_call(response_context, output), reused=True)
    assert collision_observed == [True]
    final, report_path, summary_path = _artifact_paths(output, receipt)
    assert tuple(sorted(path.name for path in final.iterdir())) == tuple(
        sorted(_FILENAMES)
    )
    assert report_path.is_file()
    assert summary_path.is_file()
    assert len(tuple((output / "report_attempts").iterdir())) == 1


def test_post_closing_descriptor_close_error_does_not_downgrade_success(
    response_context: _ResponseContext,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    output = _output_root(tmp_path)
    original = publisher._DescriptorOwner.close_all
    close_calls = 0

    def close_then_report_error(owner: object) -> bool:
        nonlocal close_calls
        close_calls += 1
        assert original(owner) is False
        return True

    monkeypatch.setattr(
        publisher._DescriptorOwner,
        "close_all",
        close_then_report_error,
    )
    receipt = _expect_success(_call(response_context, output), reused=False)
    assert close_calls == 1
    final, _, _ = _artifact_paths(output, receipt)
    assert final.is_dir()


def test_no_final_directory_exists_before_noreplace_rename_and_inode_is_preserved(
    response_context: _ResponseContext,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    output = _output_root(tmp_path)
    original = publisher._rename_attempt_to_final_noreplace
    witnesses: list[tuple[int, int]] = []

    def observe(
        source_parent: int,
        source_name: str,
        destination_parent: int,
        destination_name: str,
    ) -> bool:
        with pytest.raises(FileNotFoundError):
            os.stat(
                destination_name,
                dir_fd=destination_parent,
                follow_symlinks=False,
            )
        source_inode = os.stat(
            source_name,
            dir_fd=source_parent,
            follow_symlinks=False,
        ).st_ino
        committed = original(
            source_parent,
            source_name,
            destination_parent,
            destination_name,
        )
        destination_inode = os.stat(
            destination_name,
            dir_fd=destination_parent,
            follow_symlinks=False,
        ).st_ino
        witnesses.append((source_inode, destination_inode))
        return committed

    monkeypatch.setattr(
        publisher,
        "_rename_attempt_to_final_noreplace",
        observe,
    )
    _expect_success(_call(response_context, output), reused=False)
    assert witnesses and witnesses[0][0] == witnesses[0][1]


def test_atomic_implementation_uses_no_overwrite_fallback() -> None:
    tree = ast.parse(Path(publisher.__file__).read_text(encoding="utf-8"))
    called = {
        (
            node.func.value.id
            if isinstance(node.func, ast.Attribute)
            and isinstance(node.func.value, ast.Name)
            else None,
            node.func.attr
            if isinstance(node.func, ast.Attribute)
            else node.func.id,
        )
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, (ast.Attribute, ast.Name))
    }
    assert ("_os", "rename") not in called
    assert ("_os", "replace") not in called
    assert ("_shutil", "move") not in called
    source = Path(publisher.__file__).read_text(encoding="utf-8")
    assert "_RENAME_NOREPLACE = 1" in source
    assert "library.renameat2" in source


def test_existing_same_identity_with_different_bytes_is_a_conflict(
    response_context: _ResponseContext,
    tmp_path: Path,
) -> None:
    output = _output_root(tmp_path)
    receipt = _expect_success(_call(response_context, output), reused=False)
    _, report_path, _ = _artifact_paths(output, receipt)
    report = json.loads(report_path.read_bytes())
    report["validated_analyst_content"]["analyst_confidence"] = "LOW"
    report_path.write_bytes(_canonical(report))
    os.chmod(report_path, 0o600)
    result = _call(response_context, output)
    _expect_failure(result, "WS01_BR_PUBLICATION_CONFLICT")
    assert json.loads(report_path.read_bytes())["validated_analyst_content"][
        "analyst_confidence"
    ] == "LOW"


@pytest.mark.parametrize("defect", ("extra-file", "wrong-mode", "hard-link"))
def test_invalid_existing_final_is_never_reused_or_repaired(
    response_context: _ResponseContext,
    tmp_path: Path,
    defect: str,
) -> None:
    output = _output_root(tmp_path)
    receipt = _expect_success(_call(response_context, output), reused=False)
    final, report_path, _ = _artifact_paths(output, receipt)
    if defect == "extra-file":
        (final / "unexpected").write_bytes(b"x")
        os.chmod(final / "unexpected", 0o600)
    elif defect == "wrong-mode":
        os.chmod(report_path, 0o644)
    else:
        os.link(report_path, tmp_path / "hard-link")
    result = _call(response_context, output)
    _expect_failure(result, "WS01_BR_PUBLICATION_CONFLICT")


def test_stale_attempts_are_ignored_and_never_selected(
    response_context: _ResponseContext,
    tmp_path: Path,
) -> None:
    output = _output_root(tmp_path)
    attempts = output / "report_attempts"
    attempts.mkdir(mode=0o700)
    stale = attempts / ".attempt-00000000000000000000000000000000.tmp"
    stale.mkdir(mode=0o700)
    (stale / "partial").write_bytes(b"not a report")
    receipt = _expect_success(_call(response_context, output), reused=False)
    final, _, _ = _artifact_paths(output, receipt)
    assert final.is_dir()
    assert stale.is_dir()
    assert (stale / "partial").read_bytes() == b"not a report"


def test_attempt_name_collision_retry_is_bounded_without_pipeline_retry(
    response_context: _ResponseContext,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    output = _output_root(tmp_path)
    attempts = output / "report_attempts"
    attempts.mkdir(mode=0o700)
    collision = attempts / ".attempt-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa.tmp"
    collision.mkdir(mode=0o700)
    validation_calls = 0
    original = validator._validate_analyst_response_for_downstream

    def validate(*args: object, **kwargs: object) -> object:
        nonlocal validation_calls
        validation_calls += 1
        return original(*args, **kwargs)

    monkeypatch.setattr(publisher._secrets, "token_hex", lambda _size: "a" * 32)
    monkeypatch.setattr(
        publisher._response_validator,
        "_validate_analyst_response_for_downstream",
        validate,
    )
    result = _call(response_context, output)
    _expect_failure(result, "WS01_BR_PUBLICATION_FAILED")
    assert validation_calls == 1
    assert list((output / "reports").iterdir()) == []


def test_two_identical_concurrent_publishers_create_one_and_reuse_one(
    response_context: _ResponseContext,
    tmp_path: Path,
) -> None:
    output = _output_root(tmp_path)
    with ThreadPoolExecutor(max_workers=2) as executor:
        results = tuple(
            executor.map(
                lambda _index: _call(response_context, output),
                range(2),
            )
        )
    receipts = tuple(
        _expect_success(result, reused=result.value.publication_reused)
        for result in results
    )
    assert sorted(receipt.publication_reused for receipt in receipts) == [
        False,
        True,
    ]
    assert len({receipt.report_identity_sha256 for receipt in receipts}) == 1
    assert len({receipt.run_summary_identity_sha256 for receipt in receipts}) == 1
    assert len(list((output / "reports").iterdir())) == 1


def test_two_different_concurrent_publishers_create_distinct_generations(
    response_context: _ResponseContext,
    tmp_path: Path,
) -> None:
    output = _output_root(tmp_path)
    changed = copy.deepcopy(response_context.response)
    changed["analytical_sections"]["observations"][0]["statement"] = (
        "Concurrent but deterministically different analyst content."
    )
    raws = (response_context.raw_response, _canonical(changed))
    with ThreadPoolExecutor(max_workers=2) as executor:
        results = tuple(
            executor.map(
                lambda raw: _call(response_context, output, raw=raw),
                raws,
            )
        )
    receipts = tuple(_expect_success(result, reused=False) for result in results)
    assert len({receipt.report_identity_sha256 for receipt in receipts}) == 2
    assert len(list((output / "reports").iterdir())) == 2


def test_receipt_is_exact_frozen_slot_based_relative_and_nonserializable(
    response_context: _ResponseContext,
    tmp_path: Path,
) -> None:
    output = _output_root(tmp_path)
    receipt = _expect_success(_call(response_context, output), reused=False)
    assert tuple(field.name for field in fields(receipt)) == (
        "report_identity_sha256",
        "run_summary_identity_sha256",
        "publication_relative_path",
        "artifact_filenames",
        "publication_reused",
    )
    assert type(receipt).__slots__ == (
        "report_identity_sha256",
        "run_summary_identity_sha256",
        "publication_relative_path",
        "artifact_filenames",
        "publication_reused",
    )
    assert not hasattr(receipt, "__dict__")
    assert not receipt.publication_relative_path.startswith("/")
    assert os.fspath(output) not in repr(receipt)
    assert ".attempt-" not in repr(receipt)
    with pytest.raises(FrozenInstanceError):
        receipt.publication_reused = True
    with pytest.raises(TypeError):
        pickle.dumps(receipt)
    with pytest.raises(TypeError):
        receipt.__reduce__()
    with pytest.raises(TypeError):
        publisher._PublicationReceipt()


def test_canonical_byte_bounds_are_exact_and_one_over_fails() -> None:
    assert len(
        publisher._canonical_json_bytes("a" * 6, maximum=8)
    ) == 8
    with pytest.raises(
        publisher._WS01dFailure,
        match="WS01_BR_REPORT_CONSTRUCTION_FAILED",
    ):
        publisher._canonical_json_bytes("a" * 7, maximum=8)


@pytest.mark.parametrize(
    "exception",
    (KeyboardInterrupt(), SystemExit(), GeneratorExit()),
    ids=("keyboard-interrupt", "system-exit", "generator-exit"),
)
def test_control_flow_exceptions_propagate_from_ws01c(
    response_context: _ResponseContext,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    exception: BaseException,
) -> None:
    output = _output_root(tmp_path)

    def raise_control_flow(*_args: object, **_kwargs: object) -> object:
        raise exception

    monkeypatch.setattr(
        publisher._response_validator,
        "_validate_analyst_response_for_downstream",
        raise_control_flow,
    )
    with pytest.raises(type(exception)):
        _call(response_context, output)
    assert list(output.iterdir()) == []


def test_unexpected_ordinary_exception_maps_to_internal_invariant_failure(
    response_context: _ResponseContext,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    output = _output_root(tmp_path)
    monkeypatch.setattr(
        publisher._response_validator,
        "_validate_analyst_response_for_downstream",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("secret")),
    )
    result = _call(response_context, output)
    _expect_failure(result, "WS01_BR_INTERNAL_INVARIANT_FAILURE")
    assert "secret" not in repr(result)
    assert list(output.iterdir()) == []


def test_result_failures_are_reason_only_and_contain_no_partial_receipt(
    response_context: _ResponseContext,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    output = _output_root(tmp_path)
    secret = "ABSOLUTE-OUTPUT-SECRET"
    monkeypatch.setattr(
        publisher,
        "_rename_attempt_to_final_noreplace",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError(secret)),
    )
    result = _call(response_context, output)
    _expect_failure(result, "WS01_BR_PUBLICATION_AMBIGUOUS")
    assert secret not in repr(result)
    assert os.fspath(output) not in repr(result)
    assert response_context.raw_response.decode("utf-8") not in repr(result)


def test_real_ltetf_inventory_contains_exact_three_internal_edges() -> None:
    root = Path(__file__).parents[2]
    relative_paths = (
        "src/investment_orchestrator/observability/weekly_shadow_01_source_adapter.py",
        "src/investment_orchestrator/observability/weekly_shadow_01_package_builder.py",
        "src/investment_orchestrator/observability/weekly_shadow_01_response_validator.py",
        "src/investment_orchestrator/observability/weekly_shadow_01_report_publisher.py",
    )
    sources: dict[str, gap._ParsedProductionSource] = {}
    for relative_path in relative_paths:
        module = gap._module_name_for_path(relative_path)
        tree = ast.parse((root / relative_path).read_text(encoding="utf-8"))
        imports, findings, dynamic_imports = gap._imports_in_tree(tree, module)
        sources[module] = gap._ParsedProductionSource(
            relative_path=relative_path,
            module_name=module,
            tree=tree,
            imports=imports,
            dynamic_imports=dynamic_imports,
            findings=findings,
            report_reader=False,
            policy_reader=False,
            broker_capabilities=(),
        )
    relations = {
        (
            relation.importer_module.rsplit(".", 1)[-1],
            relation.target_module.rsplit(".", 1)[-1],
            relation.category.value,
        )
        for source in sources.values()
        for relation in gap._classify_consumer_relations(source, sources=sources)
        if relation.target_module in sources
    }
    assert relations == {
        (
            "weekly_shadow_01_package_builder",
            "weekly_shadow_01_source_adapter",
            "INTERNAL_IMPLEMENTATION_EDGE",
        ),
        (
            "weekly_shadow_01_response_validator",
            "weekly_shadow_01_package_builder",
            "INTERNAL_IMPLEMENTATION_EDGE",
        ),
        (
            "weekly_shadow_01_report_publisher",
            "weekly_shadow_01_response_validator",
            "INTERNAL_IMPLEMENTATION_EDGE",
        ),
    }
    inventory = gap._scan_production_inventory(root)
    assert inventory.observer_external_consumers == (
        "src/investment_orchestrator/cli/observe_ltetf_target_architecture_gaps.py",
    )
    assert inventory.dynamic_findings == ()
    assert inventory.report_artifact_readers == ()
    assert inventory.prohibited_observer_capability_imports == ()
    assert inventory.policy_artifact_consumers == ()
    assert inventory.p4a_runtime_consumers == ()
    assert inventory.broker_capability_imports == ()
    assert inventory.weekly_llm_invocation_markers == ()


def test_no_production_consumer_pointer_cli_or_downstream_import_exists() -> None:
    root = Path(__file__).parents[2]
    publisher_module = (
        "investment_orchestrator.observability.weekly_shadow_01_report_publisher"
    )
    importers: list[str] = []
    for path in (root / "src").rglob("*.py"):
        if path == Path(publisher.__file__):
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import) and any(
                alias.name == publisher_module for alias in node.names
            ):
                importers.append(path.relative_to(root).as_posix())
            if isinstance(node, ast.ImportFrom) and (
                node.module == publisher_module
                or any(
                    alias.name == "weekly_shadow_01_report_publisher"
                    and node.module == "investment_orchestrator.observability"
                    for alias in node.names
                )
            ):
                importers.append(path.relative_to(root).as_posix())
    assert importers == []
    publisher_tree = ast.parse(
        Path(publisher.__file__).read_text(encoding="utf-8")
    )
    string_literals = {
        node.value.lower()
        for node in ast.walk(publisher_tree)
        if isinstance(node, ast.Constant) and type(node.value) is str
    }
    assert not string_literals.intersection(
        {
            "latest",
            "current",
            "active",
            "pointer",
            "index",
            "manifest",
        }
    )
    called_names = {
        node.func.attr
        if isinstance(node.func, ast.Attribute)
        else node.func.id
        for node in ast.walk(publisher_tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, (ast.Attribute, ast.Name))
    }
    assert not called_names.intersection(
        {
            "submit_order",
            "execute_order",
            "model_call",
        }
    )


def test_module_toplevel_has_no_io_publication_network_subprocess_or_registration() -> None:
    tree = ast.parse(Path(publisher.__file__).read_text(encoding="utf-8"))
    top_level = ast.Module(
        body=[
            node
            for node in tree.body
            if not isinstance(
                node,
                (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef),
            )
        ],
        type_ignores=[],
    )
    forbidden = {
        "open",
        "read",
        "write",
        "mkdir",
        "fsync",
        "rename",
        "renameat2",
        "publish",
        "register",
        "run",
        "Popen",
        "socket",
    }
    assert not {
        node.func.attr if isinstance(node.func, ast.Attribute) else node.func.id
        for node in ast.walk(top_level)
        if isinstance(node, ast.Call)
        and isinstance(node.func, (ast.Attribute, ast.Name))
        and (
            node.func.attr
            if isinstance(node.func, ast.Attribute)
            else node.func.id
        )
        in forbidden
    }


def test_schema_contract_identity_and_negative_authority_constants_are_unchanged() -> None:
    assert contracts.SCHEMA_IDENTITY_SHA256_BY_VERSION[
        "weekly_shadow_01_analyst_report_v1"
    ] == "7b415fa8eb7cb4ecce92ddf06eb394574f7d1435dd840657396dd2eeb0f4feb8"
    assert contracts.SCHEMA_IDENTITY_SHA256_BY_VERSION[
        "weekly_shadow_01_run_summary_v1"
    ] == "114e92f0d151bba7266a651172cd7dac01f9652a4c6fe47557582b10dcf706a7"
    assert contracts.SEMANTIC_CONTRACT_IDENTITY_SHA256_BY_VERSION[
        "weekly_shadow_01_analyst_report_v1"
    ] == "195112bf9087b1f63f680c93a77d41487e4bceae4564a621c55c15b6cb684014"
    assert contracts.SEMANTIC_CONTRACT_IDENTITY_SHA256_BY_VERSION[
        "weekly_shadow_01_run_summary_v1"
    ] == "88bc37d815c348fa0791c51fbdc660f2527c2d9975a01ab2bde2b9853c2a99b3"
    assert contracts.NEGATIVE_AUTHORITY_PROFILE_IDENTITY_SHA256 == (
        "b20ea7218880c5799897d7d3fbd74515af88ad6fcc9e2f4c1d4cc83649e61ff1"
    )
    assert dict(contracts.NEGATIVE_AUTHORITY_PROFILE) == _NEGATIVE_AUTHORITY


def test_private_context_contract_remains_exact_nine_fields() -> None:
    assert tuple(field.name for field in fields(validator._WS01cDownstreamContext)) == (
        "run_id",
        "input_package_identity_sha256",
        "response_capture_identity_sha256",
        "validation_identity_sha256",
        "validated_analyst_content",
        "analyst_report_contract",
        "run_summary_contract",
        "negative_authority_profile",
        "negative_authority_profile_identity_sha256",
    )
