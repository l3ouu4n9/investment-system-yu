"""R2F-1a immutable Step 1A render-observation tests."""

from __future__ import annotations

from datetime import date
import hashlib
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import tempfile
from typing import Any

import pytest
import yaml

from investment_orchestrator.cli import run_step1
from investment_orchestrator.research import replacement_observation as r2f
from investment_orchestrator.research.evidence_packet import (
    build_evidence_packet_and_selection,
)
from investment_orchestrator.research.research_anchor_approval_manifest import (
    compute_operator_completed_anchor_sha256,
)
from investment_orchestrator.validators.strategy_settings import parse_strategy_settings_text
from investment_orchestrator.workflow.step1a_grounding_compile import (
    build_step1a_evidence_packet,
)


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _settings(as_of: str = "2026-07-12") -> str:
    return f"""as_of: "{as_of}"
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
"""


def _anchors() -> str:
    return yaml.safe_dump(
        {
            "schema_version": "research_anchors_v1",
            "as_of_date": "2026-07-12",
            "is_llm_generated": False,
            "anchors": [],
        },
        sort_keys=False,
    )


def _approved_anchor(anchor_id: str = "APPROVED_FIXTURE", ticker: str = "FIXA") -> dict[str, Any]:
    return {
        "anchor_id": anchor_id,
        "anchor_type": "structural_theme",
        "applicable_tickers": [ticker],
        "anchor_date_et": "2026-07-01",
        "valid_from": "2026-07-01",
        "valid_until": "2026-12-31",
        "source_type": "operator",
        "confidence_floor": "medium",
    }


def _approval(anchor: dict[str, Any], approval_id: str, *, valid_hash: bool = True) -> dict[str, Any]:
    return {
        "approval_id": approval_id,
        "decision": "approve",
        "operator_completed_anchor": anchor,
        "operator_completed_anchor_sha256": (
            compute_operator_completed_anchor_sha256(anchor) if valid_hash else "0" * 64
        ),
    }


def _approvals(
    *,
    approvals: list[dict[str, Any]] | None = None,
    revocations: list[dict[str, Any]] | None = None,
) -> str:
    payload: dict[str, Any] = {
        "schema_version": "research_anchor_approvals_v1",
        "is_llm_generated": False,
        "as_of_date": "2026-07-12",
        "approvals": approvals or [],
    }
    if revocations is not None:
        payload["revocations"] = revocations
    return yaml.safe_dump(payload, sort_keys=False)


def _revocation(anchor: dict[str, Any], approval_id: str = "APR-1") -> dict[str, Any]:
    return {
        "revocation_id": "REV-1",
        "target_type": "approval_anchor",
        "approval_id": approval_id,
        "anchor_id": anchor["anchor_id"],
        "operator_completed_anchor_sha256": compute_operator_completed_anchor_sha256(anchor),
        "effective_as_of": "2026-07-12",
        "reason": "fixture revocation",
        "revoked_by": "operator",
    }


def _setup_repo(root: Path, *, approvals_text: str | None = None, as_of: str = "2026-07-12") -> None:
    _write(root / "inputs/current/strategy_settings.yaml", _settings(as_of))
    _write(root / "inputs/current/portfolio_snapshot.txt", "(1) fixture portfolio snapshot\n")
    _write(root / "inputs/current/research_anchors.yaml", _anchors())
    _write(
        root / "inputs/current/research_anchor_approvals.yaml",
        approvals_text if approvals_text is not None else _approvals(),
    )
    _write(root / "prompts/analyst_memo.txt", "MEMO\n{{ evidence_packet_json }}\n")


@pytest.fixture
def isolated_repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "repo"
    _setup_repo(root)
    monkeypatch.setattr(r2f, "repo_root", lambda: root)
    monkeypatch.setattr(r2f, "_today", lambda: date(2026, 7, 12))
    return root


def _generation(result: dict[str, str]) -> Path:
    return Path(result["generation_path"])


def _json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _sha(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def test_cli_adds_only_lazy_replacement_render_and_preserves_legacy_dispatch(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    help_text = run_step1.build_parser().format_help()
    assert "replacement-render" in help_text
    assert "replacement-report" not in help_text

    cases = {
        "render": ("render_step1_prompt", "prompt_path"),
        "parse": ("parse_step1_output", "research_output_path"),
        "analyst-memo-render": ("render_step1_analyst_memo_prompt", "analyst_memo_prompt_path"),
        "analyst-memo-parse": ("parse_step1_analyst_memo_output", "validation_path"),
        "compile-handoff": ("compile_step1_research_handoff", "candidate_path"),
    }
    monkeypatch.setattr(
        run_step1,
        "_display_committed_replacement_result_noexcept",
        lambda _value: pytest.fail("legacy command used replacement-only display helper"),
    )
    for command, (attribute, result_key) in cases.items():
        monkeypatch.setattr(
            run_step1,
            attribute,
            lambda result_key=result_key, command=command: {result_key: f"legacy:{command}"},
        )
        monkeypatch.setattr("sys.argv", ["run_step1", command])
        assert run_step1.main() == 0
        assert capsys.readouterr().out.strip() == f"legacy:{command}"


def test_render_reads_each_source_once_calls_step1a_once_and_reads_no_legacy(
    isolated_repo: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    legacy = isolated_repo / "artifacts/current/step1_research"
    _write(legacy / "raw_output.txt", "LEGACY_RAW_SENTINEL")
    _write(legacy / "research_output.json", "LEGACY_RESEARCH_SENTINEL")
    _write(legacy / "research_degraded_mode_decision.json", "LEGACY_PERMISSION_SENTINEL")

    original_source_read = r2f._read_source_file_at
    original_repository_chain = r2f._open_repository_directory_chain
    original_source_directory = r2f._open_source_directory_at
    reads: dict[str, int] = {}
    repository_opens = 0
    directory_opens: list[str] = []

    def counted_source_read(
        *, input_parent_fd: int, filename: str, source_name: str
    ) -> bytes:
        reads[source_name] = reads.get(source_name, 0) + 1
        return original_source_read(
            input_parent_fd=input_parent_fd,
            filename=filename,
            source_name=source_name,
        )

    def counted_repository_chain(root: Path) -> list[tuple[int, str, int]]:
        nonlocal repository_opens
        repository_opens += 1
        return original_repository_chain(root)

    def counted_source_directory(
        parent_fd: int,
        name: str,
        source_name: str,
    ) -> int:
        directory_opens.append(name)
        return original_source_directory(parent_fd, name, source_name)

    original_builder = r2f.build_step1a_evidence_packet_from_captured_inputs
    calls = 0

    def built(**kwargs: Any) -> dict[str, Any]:
        nonlocal calls
        calls += 1
        return original_builder(**kwargs)

    monkeypatch.setattr(r2f, "_read_source_file_at", counted_source_read)
    monkeypatch.setattr(r2f, "_open_repository_directory_chain", counted_repository_chain)
    monkeypatch.setattr(r2f, "_open_source_directory_at", counted_source_directory)
    monkeypatch.setattr(r2f, "build_step1a_evidence_packet_from_captured_inputs", built)
    result = r2f.replacement_render()

    assert calls == 1
    assert reads == {
        "strategy_settings": 1,
        "portfolio_snapshot": 1,
        "research_anchors": 1,
        "research_anchor_approvals": 1,
    }
    assert repository_opens == 1
    assert directory_opens.count("inputs") == 1
    assert directory_opens.count("current") == 1
    manifest = _json(_generation(result) / "replacement_input_manifest.json")
    assert manifest["capture_profile"] == r2f.CAPTURE_PROFILE
    assert "source_bundle" not in manifest
    assert (legacy / "raw_output.txt").read_text() == "LEGACY_RAW_SENTINEL"
    assert (legacy / "research_output.json").read_text() == "LEGACY_RESEARCH_SENTINEL"
    assert (legacy / "research_degraded_mode_decision.json").read_text() == "LEGACY_PERMISSION_SENTINEL"


@pytest.mark.parametrize("scenario", ["none", "valid", "revoked", "hash_mismatch", "multiple"])
def test_r2f_evidence_has_exact_production_step1a_grounding_parity(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    scenario: str,
) -> None:
    root = tmp_path / "repo"
    anchor1 = _approved_anchor("APPROVED_ONE", "FIXA")
    anchor2 = _approved_anchor("APPROVED_TWO", "FIXB")
    approvals_list: list[dict[str, Any]] = []
    revocations: list[dict[str, Any]] | None = None
    if scenario == "valid":
        approvals_list = [_approval(anchor1, "APR-1")]
    elif scenario == "revoked":
        approvals_list = [_approval(anchor1, "APR-1")]
        revocations = [_revocation(anchor1)]
    elif scenario == "hash_mismatch":
        approvals_list = [_approval(anchor1, "APR-1", valid_hash=False)]
    elif scenario == "multiple":
        approvals_list = [_approval(anchor2, "APR-2"), _approval(anchor1, "APR-1")]
    _setup_repo(root, approvals_text=_approvals(approvals=approvals_list, revocations=revocations))
    monkeypatch.setattr(r2f, "repo_root", lambda: root)
    monkeypatch.setattr(r2f, "_today", lambda: date(2026, 7, 12))
    monkeypatch.chdir(root)

    result = r2f.replacement_render()
    observed = _json(_generation(result) / "evidence_packet.json")
    manifest = _json(_generation(result) / "replacement_input_manifest.json")
    source_artifacts = {
        name: record["path"] for name, record in manifest["inputs"].items()
    }
    production_selection: dict[str, Any] = {}
    production = build_step1a_evidence_packet(
        strategy_settings=parse_strategy_settings_text(_settings()),
        portfolio_snapshot_text="(1) fixture portfolio snapshot\n",
        portfolio_snapshot_path="inputs/current/portfolio_snapshot.txt",
        last_good_available=False,
        last_good_metadata=None,
        research_anchors_path="inputs/current/research_anchors.yaml",
        research_anchor_approvals_path="inputs/current/research_anchor_approvals.yaml",
        source_artifacts=source_artifacts,
        generated_at="2026-07-12T00:00:00+00:00",
        now_date="2026-07-12",
        embedded_selection_out=production_selection,
    )
    legacy_selection: dict[str, Any] = {}
    legacy = build_evidence_packet_and_selection(
        strategy_settings=parse_strategy_settings_text(_settings()),
        portfolio_snapshot_text="(1) fixture portfolio snapshot\n",
        portfolio_snapshot_path="inputs/current/portfolio_snapshot.txt",
        last_good_available=False,
        last_good_metadata=None,
        now_date="2026-07-12",
        generated_at="2026-07-12T00:00:00+00:00",
        source_artifacts=source_artifacts,
        research_anchors_path="inputs/current/research_anchors.yaml",
        research_anchor_approvals_path="inputs/current/research_anchor_approvals.yaml",
        embedded_selection_out=legacy_selection,
    )
    for marker in (
        "runtime_consumed",
        "permission_effect",
        "not_authorization",
        "order_authorization",
        "broker_authorization",
    ):
        observed.pop(marker)
    assert observed == production == legacy
    assert manifest["active_registry"]["selected_source"] == production_selection["selected_source"]
    assert production_selection == legacy_selection


@pytest.mark.parametrize("component", ["output_root", "generations", "generation"])
def test_descriptor_bound_writes_cannot_follow_synchronized_external_symlink(
    isolated_repo: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    component: str,
) -> None:
    external = tmp_path / f"external-{component}"
    external.mkdir()
    moved = isolated_repo / f".descriptor-retained-{component}"
    original = r2f._atomic_create_file_at
    substituted = False

    def write_after_substitution(
        directory_fd: int,
        filename: str,
        content: bytes,
        *,
        containment_guard: Any = None,
    ) -> None:
        nonlocal substituted
        if not substituted:
            output_root = isolated_repo.joinpath(*r2f.R2F_ROOT_PARTS)
            generations = output_root / r2f.GENERATIONS_DIRECTORY
            target = output_root if component == "output_root" else generations
            if component == "generation":
                target = next(generations.iterdir())
            target.rename(moved)
            target.symlink_to(external, target_is_directory=True)
            substituted = True
        original(
            directory_fd,
            filename,
            content,
            containment_guard=containment_guard,
        )

    monkeypatch.setattr(r2f, "_atomic_create_file_at", write_after_substitution)
    with pytest.raises(r2f.ReplacementObservationError, match="output_directory_identity_changed"):
        r2f.replacement_render()
    assert list(external.iterdir()) == []
    assert not list(moved.rglob(r2f.RENDER_BINDING_FILENAME))
    assert list(moved.rglob(r2f.IN_PROGRESS_FILENAME))


def test_internal_symlinked_output_component_is_rejected(
    isolated_repo: Path,
) -> None:
    internal = isolated_repo / "internal-artifacts"
    internal.mkdir()
    (isolated_repo / "artifacts").symlink_to(internal, target_is_directory=True)
    with pytest.raises(r2f.ReplacementObservationError, match="output_directory_open_failed"):
        r2f.replacement_render()
    assert list(internal.iterdir()) == []


def test_similar_prefix_external_directory_is_untouched(isolated_repo: Path) -> None:
    similar = isolated_repo.parent / "repo-similar"
    similar.mkdir()
    result = r2f.replacement_render()
    assert _generation(result).is_dir()
    assert list(similar.iterdir()) == []


def test_same_inputs_reuse_same_generation_without_overwriting_operator_memo(
    isolated_repo: Path,
) -> None:
    first = r2f.replacement_render()
    generation = _generation(first)
    raw = generation / r2f.MEMO_RAW_FILENAME
    raw.write_text("operator memo for R2F-1b", encoding="utf-8")
    immutable_before = {
        name: (generation / name).read_bytes()
        for name in (*r2f.IMMUTABLE_FILENAMES.values(), r2f.RENDER_BINDING_FILENAME)
    }

    second = r2f.replacement_render()
    assert second["generation_id"] == first["generation_id"]
    assert second["generation_reused"] == "true"
    assert raw.read_text(encoding="utf-8") == "operator memo for R2F-1b"
    assert {
        name: (generation / name).read_bytes()
        for name in immutable_before
    } == immutable_before


def test_changed_input_creates_a_distinct_generation(isolated_repo: Path) -> None:
    first = r2f.replacement_render()
    settings = isolated_repo / r2f.INPUT_PATHS["strategy_settings"]
    settings.write_text(_settings().replace("target_new_buy_budget_this_run: 10", "target_new_buy_budget_this_run: 11"))
    second = r2f.replacement_render()
    assert second["generation_id"] != first["generation_id"]
    assert Path(first["generation_path"]).is_dir()
    assert Path(second["generation_path"]).is_dir()


def test_semantic_generation_is_reproducible_across_equivalent_checkouts(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    observations: list[tuple[dict[str, str], dict[str, bytes], dict[str, Any]]] = []
    identities: list[tuple[tuple[int, int], tuple[int, int]]] = []
    for name in ("checkout-a", "checkout-b"):
        root = tmp_path / name / "repo"
        _setup_repo(root)
        identities.append(
            (
                (root.stat().st_dev, root.stat().st_ino),
                (
                    (root / r2f.INPUT_PARENT_PATH).stat().st_dev,
                    (root / r2f.INPUT_PARENT_PATH).stat().st_ino,
                ),
            )
        )
        monkeypatch.setattr(r2f, "repo_root", lambda root=root: root)
        monkeypatch.setattr(r2f, "_today", lambda: date(2026, 7, 12))
        result = r2f.replacement_render()
        generation = _generation(result)
        immutable = {
            filename: (generation / filename).read_bytes()
            for filename in (
                *r2f.IMMUTABLE_FILENAMES.values(),
                r2f.RENDER_BINDING_FILENAME,
            )
        }
        manifest = json.loads(immutable["replacement_input_manifest.json"])
        observations.append((result, immutable, manifest))

    assert identities[0] != identities[1]
    first, second = observations
    assert first[0]["generation_id"] == second[0]["generation_id"]
    assert first[1] == second[1]
    assert first[2] == second[2]
    assert "source_bundle" not in first[2]
    assert first[2]["capture_profile"] == r2f.CAPTURE_PROFILE
    assert first[0]["generation_id"] == r2f._canonical_sha256(
        r2f._semantic_generation_identity(first[2])
    )


def test_inode_mode_and_mtime_changes_do_not_change_semantic_generation(
    isolated_repo: Path,
) -> None:
    first = r2f.replacement_render()
    for relative in r2f.INPUT_PATHS.values():
        path = isolated_repo / relative
        path.chmod(0o640)
        current = path.stat()
        os.utime(path, ns=(current.st_atime_ns + 1_000_000, current.st_mtime_ns + 1_000_000))
    second = r2f.replacement_render()
    assert second["generation_id"] == first["generation_id"]
    assert second["generation_reused"] == "true"

    current_parent = isolated_repo / r2f.INPUT_PARENT_PATH
    replacement_parent = isolated_repo / "inputs/replacement-current"
    retained_parent = isolated_repo / "inputs/retained-current"
    shutil.copytree(current_parent, replacement_parent)
    original_identity = (current_parent.stat().st_dev, current_parent.stat().st_ino)
    current_parent.rename(retained_parent)
    replacement_parent.rename(current_parent)
    assert (current_parent.stat().st_dev, current_parent.stat().st_ino) != original_identity
    third = r2f.replacement_render()
    assert third["generation_id"] == first["generation_id"]
    assert third["generation_reused"] == "true"


def test_immutable_completed_generation_cannot_be_overwritten(isolated_repo: Path) -> None:
    result = r2f.replacement_render()
    manifest = _generation(result) / "replacement_input_manifest.json"
    manifest.write_bytes(manifest.read_bytes() + b" ")
    with pytest.raises(r2f.ReplacementObservationError, match="IMMUTABLE_ARTIFACT_HASH_MISMATCH"):
        r2f.replacement_render()


def test_interruption_leaves_no_binding_and_cannot_masquerade_as_complete(
    isolated_repo: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original = r2f._atomic_create_file_at

    def interrupted(
        directory_fd: int,
        filename: str,
        content: bytes,
        *,
        containment_guard: Any = None,
    ) -> None:
        if filename == "evidence_packet.json":
            raise RuntimeError("injected interruption")
        original(
            directory_fd,
            filename,
            content,
            containment_guard=containment_guard,
        )

    monkeypatch.setattr(r2f, "_atomic_create_file_at", interrupted)
    with pytest.raises(r2f.ReplacementObservationError, match="GENERATION_PUBLICATION_FAILURE"):
        r2f.replacement_render()
    generations = isolated_repo.joinpath(*r2f.R2F_ROOT_PARTS, r2f.GENERATIONS_DIRECTORY)
    incomplete = next(generations.iterdir())
    assert not (incomplete / r2f.RENDER_BINDING_FILENAME).exists()
    assert (incomplete / r2f.IN_PROGRESS_FILENAME).is_file()

    monkeypatch.setattr(r2f, "_atomic_create_file_at", original)
    with pytest.raises(r2f.ReplacementObservationError, match="INCOMPLETE_GENERATION_PRESENT"):
        r2f.replacement_render()
    assert not (incomplete / r2f.RENDER_BINDING_FILENAME).exists()


def test_render_binding_is_written_last_and_hashes_exact_file_bytes(
    isolated_repo: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original = r2f._atomic_create_file_at
    order: list[str] = []

    def recorded(
        directory_fd: int,
        filename: str,
        content: bytes,
        *,
        containment_guard: Any = None,
    ) -> None:
        order.append(filename)
        original(
            directory_fd,
            filename,
            content,
            containment_guard=containment_guard,
        )

    monkeypatch.setattr(r2f, "_atomic_create_file_at", recorded)
    result = r2f.replacement_render()
    generation = _generation(result)
    binding = _json(generation / r2f.RENDER_BINDING_FILENAME)
    assert order[-1] == r2f.RENDER_BINDING_FILENAME
    immutable = binding["immutable_render_artifacts"]
    for filename, record in immutable.items():
        assert record["file_sha256"] == _sha((generation / filename).read_bytes())
    raw_record = binding["operator_editable_inputs"][r2f.MEMO_RAW_FILENAME]
    assert raw_record["initial_file_sha256"] == _sha(b"")
    assert raw_record["render_witness_attests_initial_bytes_only"] is True


@pytest.mark.parametrize(
    "mutation,match",
    [
        (lambda value: value.update({"unknown": True}), "key_closure"),
        (lambda value: value.pop("compatibility_profile"), "key_closure"),
        (lambda value: value.update({"report_only": False}), "authority_markers"),
        (lambda value: value.update({"compatibility_profile": "future"}), "profile"),
        (lambda value: value["evidence_packet"].update({"file_sha256": "0"}), "hash"),
        (
            lambda value: value["inputs"]["research_anchors"].update(
                {"production_text_sha256": "0"}
            ),
            "hash",
        ),
        (
            lambda value: value["domain_validation"].update({"status": "DOMAIN_INVALID"}),
            "domain_status",
        ),
        (
            lambda value: value["domain_validation"].update({"diagnostics": ["RAW_VALUE"]}),
            "domain_diagnostics",
        ),
        (
            lambda value: value.update({"capture_profile": "checkout_inode_bound_v0"}),
            "capture_profile",
        ),
    ],
)
def test_manifest_exact_closure_and_markers(
    isolated_repo: Path,
    mutation: Any,
    match: str,
) -> None:
    result = r2f.replacement_render()
    manifest = _json(_generation(result) / "replacement_input_manifest.json")
    mutation(manifest)
    with pytest.raises(r2f.ReplacementObservationError, match=match):
        r2f._validate_manifest(manifest)


@pytest.mark.parametrize(
    "mutation,match",
    [
        (lambda value: value.update({"unknown": True}), "key_closure"),
        (lambda value: value.pop("scope"), "key_closure"),
        (lambda value: value.update({"broker_authorization": True}), "authority_markers"),
        (lambda value: value.update({"compatibility_profile": "future"}), "profile"),
        (lambda value: value.update({"generation_id": "0" * 64}), "generation_mismatch"),
    ],
)
def test_binding_exact_closure_and_markers(
    isolated_repo: Path,
    mutation: Any,
    match: str,
) -> None:
    result = r2f.replacement_render()
    binding = _json(_generation(result) / r2f.RENDER_BINDING_FILENAME)
    mutation(binding)
    with pytest.raises(r2f.ReplacementObservationError, match=match):
        r2f._validate_render_binding(binding, expected_generation_id=result["generation_id"])


def test_future_dated_as_of_fails_before_output(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "repo"
    _setup_repo(root, as_of="2026-07-13")
    monkeypatch.setattr(r2f, "repo_root", lambda: root)
    monkeypatch.setattr(r2f, "_today", lambda: date(2026, 7, 12))
    with pytest.raises(r2f.ReplacementObservationError, match="future"):
        r2f.replacement_render()
    assert not (root / "artifacts").exists()


@pytest.mark.parametrize(
    ("input_name", "replacement", "match"),
    [
        ("research_anchors", "research_anchors_future", "DOMAIN_INVALID_NO_GENERATION"),
        ("research_anchor_approvals", "research_anchor_approvals_future", "DOMAIN_INVALID_NO_GENERATION"),
    ],
)
def test_unsupported_source_schema_fails_before_output(
    isolated_repo: Path,
    input_name: str,
    replacement: str,
    match: str,
) -> None:
    path = isolated_repo / r2f.INPUT_PATHS[input_name]
    path.write_text(path.read_text().replace(r2f.SOURCE_VERSIONS[input_name], replacement))
    with pytest.raises(r2f.ReplacementObservationError, match=match):
        r2f.replacement_render()
    assert not (isolated_repo / "artifacts").exists()


def test_tampered_completed_binding_hash_fails_without_overwrite(isolated_repo: Path) -> None:
    result = r2f.replacement_render()
    binding_path = _generation(result) / r2f.RENDER_BINDING_FILENAME
    binding = _json(binding_path)
    binding["immutable_render_artifacts"]["analyst_memo_prompt.txt"]["file_sha256"] = "0" * 64
    tampered = r2f._json_file_bytes(binding)
    binding_path.write_bytes(tampered)
    with pytest.raises(r2f.ReplacementObservationError, match="BINDING_HASH_MISMATCH"):
        r2f.replacement_render()
    assert binding_path.read_bytes() == tampered


def test_identical_inputs_are_byte_deterministic(isolated_repo: Path) -> None:
    first = r2f.replacement_render()
    generation = _generation(first)
    before = {path.name: path.read_bytes() for path in generation.iterdir() if path.is_file()}
    second = r2f.replacement_render()
    after = {path.name: path.read_bytes() for path in generation.iterdir() if path.is_file()}
    assert second["generation_id"] == first["generation_id"]
    assert after == before
    assert len(first["generation_id"]) == 64
    assert first["generation_id"] == first["generation_id"].lower()


def test_directory_and_file_descriptors_close_on_success_reuse_and_failure(
    isolated_repo: Path,
) -> None:
    if not Path("/proc/self/fd").is_dir():
        pytest.skip("descriptor-count probe requires /proc/self/fd")
    before = len(os.listdir("/proc/self/fd"))
    result = r2f.replacement_render()
    assert len(os.listdir("/proc/self/fd")) == before
    r2f.replacement_render()
    assert len(os.listdir("/proc/self/fd")) == before
    binding = _generation(result) / r2f.RENDER_BINDING_FILENAME
    binding.write_bytes(binding.read_bytes() + b" ")
    with pytest.raises(r2f.ReplacementObservationError):
        r2f.replacement_render()
    assert len(os.listdir("/proc/self/fd")) == before
    source = isolated_repo / r2f.INPUT_PATHS["strategy_settings"]
    moved = isolated_repo / "strategy-settings-original"
    source.rename(moved)
    source.symlink_to(moved)
    with pytest.raises(r2f.ReplacementObservationError, match="SOURCE_SYMLINK_OR_NONREGULAR"):
        r2f.replacement_render()
    assert len(os.listdir("/proc/self/fd")) == before


@pytest.mark.parametrize(
    "failure_kind",
    ["close_oserror", "close_runtime", "directory_chain", "cleanup_dispatcher"],
)
def test_postcommit_cleanup_exceptions_cannot_change_committed_success(
    isolated_repo: Path,
    monkeypatch: pytest.MonkeyPatch,
    failure_kind: str,
) -> None:
    original_remove = r2f._remove_in_progress_marker_at
    original_close = r2f.os.close
    original_chain_cleanup = r2f._close_directory_chain_noexcept
    original_dispatcher = r2f._cleanup_noexcept
    committed = False
    leaked_descriptors: set[int] = set()
    baseline_descriptors = {
        int(value) for value in os.listdir("/proc/self/fd")
    } if Path("/proc/self/fd").is_dir() else set()

    def committed_remove(directory_fd: int) -> None:
        nonlocal committed
        original_remove(directory_fd)
        committed = True

    def injected_close(descriptor: int) -> None:
        if committed and failure_kind in {"close_oserror", "close_runtime"}:
            leaked_descriptors.add(descriptor)
            error = OSError("injected descriptor close failure")
            if failure_kind == "close_runtime":
                error = RuntimeError("injected descriptor close failure")
            raise error
        original_close(descriptor)

    def injected_chain_cleanup(chain: list[tuple[int, str, int]]) -> None:
        if committed and failure_kind == "directory_chain":
            leaked_descriptors.update(child for _parent, _name, child in chain)
            raise RuntimeError("injected directory-chain cleanup failure")
        original_chain_cleanup(chain)

    def injected_dispatcher(action: Any, *args: Any) -> None:
        if committed and failure_kind == "cleanup_dispatcher":
            if action is r2f._close_fd_noexcept and args:
                leaked_descriptors.add(args[0])
            elif action is r2f._close_directory_chain_noexcept and args:
                leaked_descriptors.update(child for _parent, _name, child in args[0])
            raise RuntimeError("injected outer cleanup-dispatch failure")
        original_dispatcher(action, *args)

    monkeypatch.setattr(r2f, "_remove_in_progress_marker_at", committed_remove)
    monkeypatch.setattr(r2f.os, "close", injected_close)
    monkeypatch.setattr(r2f, "_close_directory_chain_noexcept", injected_chain_cleanup)
    monkeypatch.setattr(r2f, "_cleanup_noexcept", injected_dispatcher)
    result = r2f.replacement_render()

    monkeypatch.setattr(r2f.os, "close", original_close)
    monkeypatch.setattr(r2f, "_close_directory_chain_noexcept", original_chain_cleanup)
    monkeypatch.setattr(r2f, "_cleanup_noexcept", original_dispatcher)
    monkeypatch.setattr(r2f, "_remove_in_progress_marker_at", original_remove)
    if baseline_descriptors:
        leaked_descriptors.update(
            int(value) for value in os.listdir("/proc/self/fd")
            if int(value) not in baseline_descriptors
        )
    for descriptor in leaked_descriptors:
        try:
            original_close(descriptor)
        except OSError:
            pass

    generation = _generation(result)
    assert not (generation / r2f.IN_PROGRESS_FILENAME).exists()
    assert set(path.name for path in generation.iterdir()) == set(
        r2f.COMPLETED_GENERATION_FILENAMES
    )
    reused = r2f.replacement_render()
    assert reused["generation_id"] == result["generation_id"]
    assert reused["generation_reused"] == "true"


@pytest.mark.parametrize(
    "display_error",
    [BrokenPipeError("broken pipe"), OSError("stdout unavailable"), RuntimeError("display failure")],
)
def test_committed_replacement_immediate_display_failure_returns_success_and_reuses(
    isolated_repo: Path,
    monkeypatch: pytest.MonkeyPatch,
    display_error: BaseException,
) -> None:
    class FailedStdout:
        def write(self, _value: str) -> int:
            raise display_error

        def flush(self) -> None:
            raise display_error

        def fileno(self) -> int:
            raise display_error

    monkeypatch.setattr(sys, "stdout", FailedStdout())
    monkeypatch.setattr(sys, "argv", ["run_step1", "replacement-render"])
    monkeypatch.setattr(
        run_step1.os,
        "_exit",
        lambda _status: pytest.fail("programmatic main called os._exit"),
    )
    assert run_step1.main() is run_step1._COMMITTED_DISPLAY_FAILURE

    generations = isolated_repo.joinpath(
        *r2f.R2F_ROOT_PARTS,
        r2f.GENERATIONS_DIRECTORY,
    )
    generation = next(generations.iterdir())
    assert not (generation / r2f.IN_PROGRESS_FILENAME).exists()
    reused = r2f.replacement_render()
    assert reused["generation_reused"] == "true"


def test_replacement_display_persistent_flush_failure_returns_committed_status(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class BufferedBrokenStdout:
        def __init__(self) -> None:
            self.flush_calls = 0
            self.writes: list[str] = []

        def write(self, value: str) -> int:
            self.writes.append(value)
            return len(value)

        def flush(self) -> None:
            self.flush_calls += 1
            raise BrokenPipeError("persistent buffered flush failure")

    stream = BufferedBrokenStdout()
    monkeypatch.setattr(sys, "stdout", stream)
    result = run_step1._display_committed_replacement_result_noexcept("committed")

    assert result is run_step1._COMMITTED_DISPLAY_FAILURE
    assert stream.writes == ["committed\n"]
    assert stream.flush_calls == 1


@pytest.mark.parametrize(
    "display_error",
    [BrokenPipeError("broken"), OSError("write"), RuntimeError("display")],
)
def test_replacement_display_helper_reports_every_direct_display_failure(
    monkeypatch: pytest.MonkeyPatch,
    display_error: BaseException,
) -> None:
    class FailedStdout:
        def write(self, _value: str) -> int:
            raise display_error

        def flush(self) -> None:
            raise display_error

    monkeypatch.setattr(sys, "stdout", FailedStdout())
    assert (
        run_step1._display_committed_replacement_result_noexcept("committed")
        is run_step1._COMMITTED_DISPLAY_FAILURE
    )


def test_programmatic_main_persistent_display_failure_returns_private_status(
    isolated_repo: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class PersistentBrokenStdout:
        def write(self, value: str) -> int:
            return len(value)

        def flush(self) -> None:
            raise BrokenPipeError("persistent display failure")

    monkeypatch.setattr(sys, "stdout", PersistentBrokenStdout())
    monkeypatch.setattr(sys, "argv", ["run_step1", "replacement-render"])
    monkeypatch.setattr(
        run_step1.os,
        "_exit",
        lambda _status: pytest.fail("programmatic main called os._exit"),
    )

    assert run_step1.main() is run_step1._COMMITTED_DISPLAY_FAILURE
    generation = next(
        isolated_repo.joinpath(*r2f.R2F_ROOT_PARTS, r2f.GENERATIONS_DIRECTORY).iterdir()
    )
    assert not (generation / r2f.IN_PROGRESS_FILENAME).exists()


def test_programmatic_main_normal_replacement_display_never_uses_hard_exit(
    isolated_repo: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(sys, "argv", ["run_step1", "replacement-render"])
    monkeypatch.setattr(
        run_step1.os,
        "_exit",
        lambda _status: pytest.fail("programmatic main called os._exit"),
    )

    assert run_step1.main() == 0
    assert capsys.readouterr().out.strip().endswith("analyst_memo_prompt.txt")
    assert next(
        isolated_repo.joinpath(*r2f.R2F_ROOT_PARTS, r2f.GENERATIONS_DIRECTORY).iterdir()
    ).is_dir()


def _copy_cli_repository(root: Path) -> None:
    source_root = Path(__file__).resolve().parents[2] / "src" / "investment_orchestrator"
    shutil.copytree(
        source_root,
        root / "src" / "investment_orchestrator",
        ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
    )
    _setup_repo(root)


def _replacement_cli_environment(root: Path) -> dict[str, str]:
    return {
        **os.environ,
        "PYTHONPATH": str(root / "src"),
        "PYTHONDONTWRITEBYTECODE": "1",
    }


def _assert_completed_generation(root: Path) -> tuple[Path, Path]:
    generations = root.joinpath(*r2f.R2F_ROOT_PARTS, r2f.GENERATIONS_DIRECTORY)
    generation_paths = list(generations.iterdir())
    assert len(generation_paths) == 1
    generation = generation_paths[0]
    assert not (generation / r2f.IN_PROGRESS_FILENAME).exists()
    assert {path.name for path in generation.iterdir()} == set(r2f.COMPLETED_GENERATION_FILENAMES)
    return generations, generation


def test_replacement_render_real_broken_pipe_keeps_committed_generation_reusable(
    tmp_path: Path,
) -> None:
    """Exercise the real module and interpreter shutdown through a closed pipe."""
    root = tmp_path / "isolated-repository"
    _copy_cli_repository(root)
    environment = _replacement_cli_environment(root)
    command = [
        sys.executable,
        "-m",
        "investment_orchestrator.cli.run_step1",
        "replacement-render",
    ]

    read_fd, write_fd = os.pipe()
    consumer = subprocess.Popen(
        ["head", "-n", "0"],
        stdin=read_fd,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
    )
    os.close(read_fd)
    try:
        consumer_stderr = consumer.communicate(timeout=10)[1]
        assert consumer.returncode == 0, consumer_stderr.decode("utf-8", errors="replace")
        producer = subprocess.Popen(
            command,
            cwd=root,
            stdout=write_fd,
            stderr=subprocess.PIPE,
            env=environment,
        )
    finally:
        os.close(write_fd)

    producer_stderr = producer.communicate(timeout=30)[1].decode("utf-8", errors="replace")
    assert producer.returncode == 0, producer_stderr
    assert producer_stderr == ""

    generations, _generation = _assert_completed_generation(root)

    rerun = subprocess.run(
        command,
        cwd=root,
        capture_output=True,
        env=environment,
        timeout=30,
        check=False,
    )
    assert rerun.returncode == 0, rerun.stderr.decode("utf-8", errors="replace")
    assert rerun.stderr == b""
    assert len(list(generations.iterdir())) == 1


@pytest.mark.parametrize("mode", ["persistent_stdout", "unusable_silencing_operations"])
def test_replacement_render_process_entrypoint_hard_exits_after_committed_display_failure(
    tmp_path: Path,
    mode: str,
) -> None:
    root = tmp_path / mode
    _copy_cli_repository(root)
    if mode == "persistent_stdout":
        launcher = """
import runpy
import sys

class PersistentBrokenStdout:
    def write(self, value):
        return len(value)
    def flush(self):
        raise BrokenPipeError("persistent display failure")

sys.stdout = PersistentBrokenStdout()
sys.argv = ["run_step1", "replacement-render"]
runpy.run_module("investment_orchestrator.cli.run_step1", run_name="__main__")
"""
    else:
        launcher = """
import argparse
import os as real_os
import runpy
import sys
from types import SimpleNamespace
import investment_orchestrator.research.replacement_observation
import investment_orchestrator.workflow.step1_research

class PersistentBrokenStdout:
    def write(self, value):
        return len(value)
    def flush(self):
        raise BrokenPipeError("persistent display failure")

def forbidden(name):
    def raise_if_called(*args, **kwargs):
        raise AssertionError(name + " must not be called")
    return raise_if_called

sys.modules["os"] = SimpleNamespace(
    _exit=real_os._exit,
    open=forbidden("open"),
    dup2=forbidden("dup2"),
)
sys.stdout = PersistentBrokenStdout()
sys.argv = ["run_step1", "replacement-render"]
runpy.run_module("investment_orchestrator.cli.run_step1", run_name="__main__")
"""
    command = [sys.executable, "-c", launcher]
    process = subprocess.run(
        command,
        cwd=root,
        capture_output=True,
        env=_replacement_cli_environment(root),
        timeout=30,
        check=False,
    )

    assert process.returncode == 0, process.stderr.decode("utf-8", errors="replace")
    assert process.stderr == b""
    generations, _generation = _assert_completed_generation(root)
    rerun = subprocess.run(
        [sys.executable, "-m", "investment_orchestrator.cli.run_step1", "replacement-render"],
        cwd=root,
        capture_output=True,
        env=_replacement_cli_environment(root),
        timeout=30,
        check=False,
    )
    assert rerun.returncode == 0, rerun.stderr.decode("utf-8", errors="replace")
    assert rerun.stderr == b""
    assert len(list(generations.iterdir())) == 1


def test_prompt_is_hash_bound_and_r2f1a_stops_before_memo_parsing(isolated_repo: Path) -> None:
    result = r2f.replacement_render()
    generation = _generation(result)
    manifest_bytes = (generation / "replacement_input_manifest.json").read_bytes()
    evidence_bytes = (generation / "evidence_packet.json").read_bytes()
    prompt = (generation / "analyst_memo_prompt.txt").read_text(encoding="utf-8")
    manifest = json.loads(manifest_bytes)
    evidence = json.loads(evidence_bytes)
    assert result["generation_id"] in prompt
    assert _sha(manifest_bytes) in prompt
    assert r2f._canonical_sha256(manifest) in prompt
    assert _sha(evidence_bytes) in prompt
    assert r2f._canonical_sha256(evidence) in prompt
    assert "permissions, budgets, quantities, orders, execution, and universe creation" in prompt
    assert "R2F-1b will require an exact hash-bound memo envelope" in prompt
    assert (generation / r2f.MEMO_RAW_FILENAME).read_bytes() == b""
    forbidden = {
        "analyst_memo.json",
        "analyst_memo_validation.json",
        "replacement_research_candidate.json",
        "replacement_research_candidate_validation.json",
        "replacement_research_binding.json",
        "replacement_coverage_report.json",
        "replacement_compatibility_report.json",
    }
    assert forbidden.isdisjoint({path.name for path in generation.iterdir()})


def test_every_json_artifact_has_code_owned_authority_markers(isolated_repo: Path) -> None:
    generation = _generation(r2f.replacement_render())
    expected = {
        "report_only": True,
        "runtime_consumed": False,
        "permission_effect": "none",
        "not_authorization": True,
        "order_authorization": False,
        "broker_authorization": False,
    }
    for path in generation.glob("*.json"):
        payload = _json(path)
        for key, value in expected.items():
            assert payload[key] == value, (path.name, key)


def test_runtime_has_no_r2f_consumer_or_forbidden_downstream_call() -> None:
    source_root = Path(__file__).resolve().parents[2] / "src/investment_orchestrator"
    allowed_isolated_readers = {
        source_root / "research/replacement_observation.py",
        source_root / "research/replacement_generation_reader.py",
    }
    consumers: list[Path] = []
    for path in source_root.rglob("*.py"):
        if path in allowed_isolated_readers or path == source_root / "cli/run_step1.py":
            continue
        text = path.read_text(encoding="utf-8")
        if r2f.GENERATIONS_DIRECTORY in text and "r2f_report_only" in text:
            consumers.append(path)
    assert consumers == []

    # R2F-1b-a may only add this exact descriptor-read-only source verifier.
    # It remains prohibited for availability, permission, weekly, gate, order,
    # broker, and other runtime modules to import it or consume R2F artifacts.
    reader_path = source_root / "research/replacement_generation_reader.py"
    reader_source = reader_path.read_text(encoding="utf-8")
    for forbidden in (
        "os.mkdir(",
        "os.rename(",
        "os.replace(",
        "os.unlink(",
        "os.write(",
        "O_CREAT",
        "write_json(",
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
    ):
        assert forbidden not in reader_source
    for forbidden_import in (
        "investment_orchestrator.state.",
        "investment_orchestrator.workflow.",
        "investment_orchestrator.cli.",
        "investment_orchestrator.broker",
        "investment_orchestrator.order",
    ):
        assert forbidden_import not in reader_source
    for path in source_root.rglob("*.py"):
        if path == reader_path:
            continue
        text = path.read_text(encoding="utf-8")
        runtime_path = (
            any(part in {"state", "workflow", "cli"} for part in path.parts)
            or "order" in path.name
            or "broker" in path.name
            or "handoff" in path.name
        )
        if runtime_path:
            assert "replacement_generation_reader" not in text, path

    source = Path(r2f.__file__).read_text(encoding="utf-8")
    for forbidden in (
        "parse_analyst_memo_text",
        "compile_research_handoff",
        "validate_research_handoff",
        "evaluate_research_availability",
        "research_degraded_mode_decision",
        "run_weekly",
        "step2_decision_builder",
        "step3_audit_engine",
        "step4_order_compiler",
        "final_execution_safety",
        "quarantine",
        "write_last_good_research_handoff",
    ):
        assert forbidden not in source


@pytest.mark.parametrize("source_name", list(r2f.INPUT_PATHS))
@pytest.mark.parametrize(
    "replacement_kind",
    ["external_symlink", "internal_symlink", "different_regular", "directory", "fifo"],
)
def test_source_substitution_before_descriptor_open_never_reads_replacement(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    source_name: str,
    replacement_kind: str,
) -> None:
    root = tmp_path / "repo"
    _setup_repo(root)
    target = root / r2f.INPUT_PATHS[source_name]
    backup = tmp_path / f"original-{target.name}"
    replacement = tmp_path / f"replacement-{source_name}"
    replacement.write_bytes(b"EXTERNAL_REPLACEMENT_SENTINEL\n")
    internal = root / f"internal-{source_name}"
    internal.write_bytes(b"INTERNAL_REPLACEMENT_SENTINEL\n")

    original_open = os.open
    original_read = os.read
    substituted = False
    replacement_identity: tuple[int, int] | None = None
    replacement_reads = 0

    def open_after_discovery(
        path: str | bytes,
        flags: int,
        mode: int = 0o777,
        *,
        dir_fd: int | None = None,
    ) -> int:
        nonlocal substituted, replacement_identity
        if not substituted and path == target.name and not (flags & os.O_DIRECTORY):
            target.rename(backup)
            if replacement_kind == "external_symlink":
                target.symlink_to(replacement)
                entry = replacement.stat()
            elif replacement_kind == "internal_symlink":
                target.symlink_to(internal)
                entry = internal.stat()
            elif replacement_kind == "different_regular":
                replacement.rename(target)
                entry = target.stat()
            elif replacement_kind == "directory":
                target.mkdir()
                entry = target.stat()
            else:
                os.mkfifo(target)
                entry = target.stat()
            replacement_identity = (entry.st_dev, entry.st_ino)
            substituted = True
        return original_open(path, flags, mode, dir_fd=dir_fd)

    def tracked_read(descriptor: int, size: int) -> bytes:
        nonlocal replacement_reads
        entry = os.fstat(descriptor)
        if replacement_identity == (entry.st_dev, entry.st_ino):
            replacement_reads += 1
        return original_read(descriptor, size)

    monkeypatch.setattr(r2f, "repo_root", lambda: root)
    monkeypatch.setattr(r2f, "_today", lambda: date(2026, 7, 12))
    monkeypatch.setattr(r2f, "_require_descriptor_primitives", lambda: None)
    monkeypatch.setattr(r2f.os, "open", open_after_discovery)
    monkeypatch.setattr(r2f.os, "read", tracked_read)
    with pytest.raises(r2f.ReplacementObservationError, match="SOURCE_") as caught:
        r2f.replacement_render()
    assert substituted is True
    assert replacement_reads == 0
    assert "SENTINEL" not in str(caught.value)
    assert str(replacement) not in str(caught.value)
    assert not (root / "artifacts").exists()


def test_source_replacement_after_descriptor_open_reads_original_descriptor(
    isolated_repo: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = isolated_repo / r2f.INPUT_PATHS["strategy_settings"]
    original_bytes = target.read_bytes()
    replacement = tmp_path / "replacement-settings.yaml"
    replacement.write_text(
        _settings().replace(
            "target_new_buy_budget_this_run: 10",
            "target_new_buy_budget_this_run: 77",
        )
    )
    original_reader = r2f._read_all_descriptor_bytes
    swapped = False
    descriptor_bytes: bytes | None = None
    original_identity = (target.stat().st_dev, target.stat().st_ino)

    def read_after_swap(descriptor: int) -> bytes:
        nonlocal swapped, descriptor_bytes
        opened = os.fstat(descriptor)
        if not swapped and (opened.st_dev, opened.st_ino) == original_identity:
            moved = tmp_path / "original-settings.yaml"
            target.rename(moved)
            replacement.rename(target)
            swapped = True
        value = original_reader(descriptor)
        if (opened.st_dev, opened.st_ino) == original_identity and descriptor_bytes is None:
            descriptor_bytes = value
        return value

    monkeypatch.setattr(r2f, "_read_all_descriptor_bytes", read_after_swap)
    result = r2f.replacement_render()
    manifest = _json(_generation(result) / "replacement_input_manifest.json")
    assert descriptor_bytes == original_bytes
    assert descriptor_bytes != target.read_bytes()
    assert manifest["inputs"]["strategy_settings"]["file_sha256"] == _sha(original_bytes)


@pytest.mark.parametrize("replacement_kind", ["external_symlink", "different_directory"])
def test_source_parent_substitution_is_rejected_before_any_source_read(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    replacement_kind: str,
) -> None:
    root = tmp_path / "repo"
    _setup_repo(root)
    current = root / "inputs/current"
    moved = root / "inputs/original-current"
    external = tmp_path / "external-current"
    external.mkdir()
    original_open = os.open
    substituted = False
    read_calls = 0

    def open_after_parent_discovery(
        path: str | bytes,
        flags: int,
        mode: int = 0o777,
        *,
        dir_fd: int | None = None,
    ) -> int:
        nonlocal substituted
        if not substituted and path == "current" and flags & os.O_DIRECTORY:
            current.rename(moved)
            if replacement_kind == "external_symlink":
                current.symlink_to(external, target_is_directory=True)
            else:
                external.rename(current)
            substituted = True
        return original_open(path, flags, mode, dir_fd=dir_fd)

    def no_source_read(_descriptor: int) -> bytes:
        nonlocal read_calls
        read_calls += 1
        return b""

    monkeypatch.setattr(r2f, "repo_root", lambda: root)
    monkeypatch.setattr(r2f, "_today", lambda: date(2026, 7, 12))
    monkeypatch.setattr(r2f, "_require_descriptor_primitives", lambda: None)
    monkeypatch.setattr(r2f.os, "open", open_after_parent_discovery)
    monkeypatch.setattr(r2f, "_read_all_descriptor_bytes", no_source_read)
    with pytest.raises(r2f.ReplacementObservationError, match="SOURCE_PARENT"):
        r2f.replacement_render()
    assert substituted is True
    assert read_calls == 0
    assert not (root / "artifacts").exists()


def test_in_place_source_mutation_during_read_fails_closed(
    isolated_repo: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = isolated_repo / r2f.INPUT_PATHS["strategy_settings"]
    original_reader = r2f._read_all_descriptor_bytes
    mutated = False
    original_identity = (target.stat().st_dev, target.stat().st_ino)

    def read_then_mutate(descriptor: int) -> bytes:
        nonlocal mutated
        value = original_reader(descriptor)
        opened = os.fstat(descriptor)
        if not mutated and (opened.st_dev, opened.st_ino) == original_identity:
            target.write_bytes(value + b"\n# concurrent mutation\n")
            mutated = True
        return value

    monkeypatch.setattr(r2f, "_read_all_descriptor_bytes", read_then_mutate)
    with pytest.raises(r2f.ReplacementObservationError, match="SOURCE_IDENTITY_CHANGED"):
        r2f.replacement_render()
    assert mutated is True
    assert not (isolated_repo / "artifacts").exists()


@pytest.mark.parametrize("replace_after_index", range(4))
def test_all_sources_share_retained_input_parent_and_path_replacement_fails_commit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    replace_after_index: int,
) -> None:
    root = tmp_path / "repo"
    _setup_repo(root)
    current = root / "inputs/current"
    retained = root / "inputs/retained-current"
    replacement = root / "inputs/replacement-current"
    shutil.copytree(current, replacement)
    (replacement / "portfolio_snapshot.txt").write_text(
        "(1) replacement-parent sentinel portfolio\n",
        encoding="utf-8",
    )
    original_bytes = {
        name: (current / Path(relative).name).read_bytes()
        for name, relative in r2f.INPUT_PATHS.items()
    }
    observed: dict[str, bytes] = {}
    original_read = r2f._read_source_file_at
    read_index = 0

    def replace_parent_after_read(
        *, input_parent_fd: int, filename: str, source_name: str
    ) -> bytes:
        nonlocal read_index
        value = original_read(
            input_parent_fd=input_parent_fd,
            filename=filename,
            source_name=source_name,
        )
        observed[source_name] = value
        if read_index == replace_after_index:
            current.rename(retained)
            replacement.rename(current)
        read_index += 1
        return value

    monkeypatch.setattr(r2f, "repo_root", lambda: root)
    monkeypatch.setattr(r2f, "_today", lambda: date(2026, 7, 12))
    monkeypatch.setattr(r2f, "_read_source_file_at", replace_parent_after_read)
    with pytest.raises(r2f.ReplacementObservationError, match="INPUT_PARENT_IDENTITY_CHANGED"):
        r2f.replacement_render()
    assert observed == original_bytes
    generation = next(
        root.joinpath(*r2f.R2F_ROOT_PARTS, r2f.GENERATIONS_DIRECTORY).iterdir()
    )
    assert (generation / r2f.IN_PROGRESS_FILENAME).is_file()

    displaced = root / "inputs/displaced-replacement-current"
    current.rename(displaced)
    retained.rename(current)
    monkeypatch.setattr(r2f, "_read_source_file_at", original_read)
    with pytest.raises(r2f.ReplacementObservationError, match="INCOMPLETE_GENERATION_PRESENT"):
        r2f.replacement_render()


def test_repository_replacement_cannot_redirect_prompt_or_generation_writes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "repo"
    retained_root = tmp_path / "retained-original-repo"
    replacement_root = tmp_path / "replacement-repo"
    _setup_repo(root)
    _write(
        replacement_root / "prompts/analyst_memo.txt",
        "REPLACEMENT_PROMPT_SENTINEL\n{{ evidence_packet_json }}\n",
    )
    original_capture = r2f._capture_inputs

    def capture_then_replace(*, input_parent_fd: int) -> dict[str, dict[str, Any]]:
        captured = original_capture(input_parent_fd=input_parent_fd)
        root.rename(retained_root)
        replacement_root.rename(root)
        return captured

    monkeypatch.setattr(r2f, "repo_root", lambda: root)
    monkeypatch.setattr(r2f, "_today", lambda: date(2026, 7, 12))
    monkeypatch.setattr(r2f, "_capture_inputs", capture_then_replace)
    with pytest.raises(r2f.ReplacementObservationError, match="REPOSITORY_IDENTITY_CHANGED"):
        r2f.replacement_render()
    assert not list(root.rglob(r2f.RENDER_BINDING_FILENAME))
    incomplete = next(
        retained_root.joinpath(*r2f.R2F_ROOT_PARTS, r2f.GENERATIONS_DIRECTORY).iterdir()
    )
    assert (incomplete / r2f.IN_PROGRESS_FILENAME).is_file()

    displaced = tmp_path / "displaced-replacement-repo"
    root.rename(displaced)
    retained_root.rename(root)
    monkeypatch.setattr(r2f, "_capture_inputs", original_capture)
    with pytest.raises(r2f.ReplacementObservationError, match="INCOMPLETE_GENERATION_PRESENT"):
        r2f.replacement_render()


def test_prompt_template_is_captured_before_later_path_replacement(
    isolated_repo: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    template = isolated_repo / r2f.MEMO_PROMPT_TEMPLATE_PATH
    retained = isolated_repo / "prompts/retained-analyst-memo.txt"
    replacement = isolated_repo / "prompts/replacement-analyst-memo.txt"
    replacement.write_text(
        "REPLACEMENT_PROMPT_SENTINEL\n{{ evidence_packet_json }}\n",
        encoding="utf-8",
    )
    original_capture = r2f._capture_inputs

    def capture_then_replace(*, input_parent_fd: int) -> dict[str, dict[str, Any]]:
        captured = original_capture(input_parent_fd=input_parent_fd)
        template.rename(retained)
        replacement.rename(template)
        return captured

    monkeypatch.setattr(r2f, "_capture_inputs", capture_then_replace)
    result = r2f.replacement_render()
    prompt = (_generation(result) / "analyst_memo_prompt.txt").read_text(encoding="utf-8")
    assert prompt.startswith("MEMO\n")
    assert "REPLACEMENT_PROMPT_SENTINEL" not in prompt


def test_newline_forms_match_production_semantics_but_keep_raw_identity(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    forms = {
        "lf": ("\n", True),
        "crlf": ("\r\n", True),
        "cr": ("\r", True),
        "no_final_newline": ("\n", False),
    }
    observations: dict[str, dict[str, Any]] = {}
    for label, (separator, trailing) in forms.items():
        root = tmp_path / label / "repo"
        _setup_repo(root)
        for relative in r2f.INPUT_PATHS.values():
            path = root / relative
            normalized = path.read_text(encoding="utf-8")
            if not trailing:
                normalized = normalized.rstrip("\n")
            path.write_bytes(normalized.replace("\n", separator).encode("utf-8"))
        monkeypatch.setattr(r2f, "repo_root", lambda root=root: root)
        monkeypatch.setattr(r2f, "_today", lambda: date(2026, 7, 12))
        monkeypatch.chdir(root)
        result = r2f.replacement_render()
        generation = _generation(result)
        observed = _json(generation / "evidence_packet.json")
        manifest = _json(generation / "replacement_input_manifest.json")
        production_selection: dict[str, Any] = {}
        production = build_step1a_evidence_packet(
            strategy_settings=parse_strategy_settings_text(
                (root / r2f.INPUT_PATHS["strategy_settings"]).read_text(encoding="utf-8")
            ),
            portfolio_snapshot_text=(root / r2f.INPUT_PATHS["portfolio_snapshot"]).read_text(
                encoding="utf-8"
            ),
            portfolio_snapshot_path=r2f.INPUT_PATHS["portfolio_snapshot"],
            last_good_available=False,
            last_good_metadata=None,
            research_anchors_path=r2f.INPUT_PATHS["research_anchors"],
            research_anchor_approvals_path=r2f.INPUT_PATHS["research_anchor_approvals"],
            source_artifacts={name: record["path"] for name, record in manifest["inputs"].items()},
            generated_at="2026-07-12T00:00:00+00:00",
            now_date="2026-07-12",
            embedded_selection_out=production_selection,
        )
        for marker in (
            "runtime_consumed",
            "permission_effect",
            "not_authorization",
            "order_authorization",
            "broker_authorization",
        ):
            observed.pop(marker)
        assert observed == production
        anchor_record = manifest["inputs"]["research_anchors"]
        observations[label] = {
            "raw": anchor_record["file_sha256"],
            "semantic": anchor_record["production_text_sha256"],
            "registry": production["active_anchor_registry"],
            "evidence": production,
        }

    assert len({observations[name]["raw"] for name in ("lf", "crlf", "cr")}) == 3
    assert len({observations[name]["semantic"] for name in ("lf", "crlf", "cr")}) == 1
    assert observations["lf"]["registry"] == observations["crlf"]["registry"]
    assert observations["lf"]["registry"] == observations["cr"]["registry"]
    assert observations["lf"]["evidence"] == observations["crlf"]["evidence"]
    assert observations["lf"]["evidence"] == observations["cr"]["evidence"]


@pytest.mark.parametrize(
    "extra_kind",
    ["regular", "hidden", "directory", "symlink", "fifo", "temp", "socket"],
)
def test_completed_generation_rejects_every_unexpected_entry(
    isolated_repo: Path,
    tmp_path: Path,
    extra_kind: str,
) -> None:
    generation = _generation(r2f.replacement_render())
    name = {
        "regular": "unexpected.txt",
        "hidden": ".unexpected",
        "directory": "unexpected-directory",
        "symlink": "unexpected-symlink",
        "fifo": "unexpected-fifo",
        "temp": ".evidence_packet.json.r2f1a.tmp",
        "socket": "unexpected-socket",
    }[extra_kind]
    extra = generation / name
    sock: socket.socket | None = None
    short_socket_root: tempfile.TemporaryDirectory[str] | None = None
    if extra_kind in {"regular", "hidden", "temp"}:
        extra.write_text("unexpected", encoding="utf-8")
    elif extra_kind == "directory":
        extra.mkdir()
    elif extra_kind == "symlink":
        target = tmp_path / "external-sentinel"
        target.write_text("EXTERNAL_SENTINEL", encoding="utf-8")
        extra.symlink_to(target)
    elif extra_kind == "fifo":
        os.mkfifo(extra)
    else:
        short_socket_root = tempfile.TemporaryDirectory(prefix="r2f-socket-")
        short_socket = Path(short_socket_root.name) / "socket"
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            sock.bind(str(short_socket))
        except PermissionError:
            sock.close()
            short_socket_root.cleanup()
            pytest.skip("sandbox does not permit constructing an AF_UNIX socket entry")
        os.link(short_socket, extra)
    try:
        with pytest.raises(r2f.ReplacementObservationError, match="GENERATION_INVENTORY_MISMATCH"):
            r2f.replacement_render()
        assert extra.exists() or extra.is_symlink()
    finally:
        if sock is not None:
            sock.close()
        if short_socket_root is not None:
            short_socket_root.cleanup()


@pytest.mark.parametrize(
    "invalid_kind",
    [
        "duplicate_anchors",
        "malformed_anchor",
        "duplicate_approvals",
        "invalid_approval",
        "llm_approvals",
        "invalid_completed_anchor",
        "non_operator_completed_anchor",
    ],
)
def test_domain_invalid_sources_create_no_generation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    invalid_kind: str,
) -> None:
    root = tmp_path / "repo"
    anchor = _approved_anchor("DOMAIN_ANCHOR", "FIXA")
    approvals_text = _approvals()
    anchors_text = _anchors()
    if invalid_kind == "duplicate_anchors":
        anchors_text = yaml.safe_dump(
            {
                "schema_version": "research_anchors_v1",
                "as_of_date": "2026-07-12",
                "is_llm_generated": False,
                "anchors": [anchor, anchor],
            },
            sort_keys=False,
        )
    elif invalid_kind == "malformed_anchor":
        anchors_text = yaml.safe_dump(
            {
                "schema_version": "research_anchors_v1",
                "as_of_date": "2026-07-12",
                "is_llm_generated": False,
                "anchors": [{"anchor_id": "MISSING_FIELDS"}],
            },
            sort_keys=False,
        )
    elif invalid_kind == "duplicate_approvals":
        approvals_text = _approvals(
            approvals=[_approval(anchor, "APR-1"), _approval(anchor, "APR-1")]
        )
    elif invalid_kind == "invalid_approval":
        approvals_text = _approvals(approvals=[{"approval_id": "APR-1", "decision": "approve"}])
    elif invalid_kind == "llm_approvals":
        payload = yaml.safe_load(_approvals())
        payload["is_llm_generated"] = True
        approvals_text = yaml.safe_dump(payload, sort_keys=False)
    elif invalid_kind == "invalid_completed_anchor":
        invalid_anchor = dict(anchor)
        invalid_anchor["applicable_tickers"] = ["OUTSIDE"]
        approvals_text = _approvals(approvals=[_approval(invalid_anchor, "APR-1")])
    else:
        invalid_anchor = dict(anchor)
        invalid_anchor["source_type"] = "official"
        approvals_text = _approvals(approvals=[_approval(invalid_anchor, "APR-1")])
    _setup_repo(root, approvals_text=approvals_text)
    (root / r2f.INPUT_PATHS["research_anchors"]).write_text(anchors_text)
    monkeypatch.setattr(r2f, "repo_root", lambda: root)
    monkeypatch.setattr(r2f, "_today", lambda: date(2026, 7, 12))
    with pytest.raises(r2f.ReplacementObservationError, match="DOMAIN_INVALID_NO_GENERATION"):
        r2f.replacement_render()
    assert not (root / "artifacts").exists()


@pytest.mark.parametrize("source_name", list(r2f.INPUT_PATHS))
def test_blank_required_source_fails_before_generation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    source_name: str,
) -> None:
    root = tmp_path / "repo"
    _setup_repo(root)
    (root / r2f.INPUT_PATHS[source_name]).write_bytes(b"")
    monkeypatch.setattr(r2f, "repo_root", lambda: root)
    monkeypatch.setattr(r2f, "_today", lambda: date(2026, 7, 12))
    with pytest.raises(r2f.ReplacementObservationError, match=f"SOURCE_BLANK:{source_name}"):
        r2f.replacement_render()
    assert not (root / "artifacts").exists()


@pytest.mark.parametrize("valid_kind", ["revoked", "expired", "hash_mismatch"])
def test_domain_valid_nonactivating_sources_publish_bounded_diagnostics(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    valid_kind: str,
) -> None:
    root = tmp_path / "repo"
    anchor = _approved_anchor("NONACTIVATING", "FIXA")
    revocations: list[dict[str, Any]] | None = None
    if valid_kind == "expired":
        anchor["valid_until"] = "2026-07-01"
    approval = _approval(anchor, "APR-1", valid_hash=valid_kind != "hash_mismatch")
    if valid_kind == "revoked":
        revocations = [_revocation(anchor)]
    _setup_repo(root, approvals_text=_approvals(approvals=[approval], revocations=revocations))
    monkeypatch.setattr(r2f, "repo_root", lambda: root)
    monkeypatch.setattr(r2f, "_today", lambda: date(2026, 7, 12))
    generation = _generation(r2f.replacement_render())
    manifest = _json(generation / "replacement_input_manifest.json")
    assert manifest["domain_validation"]["status"] == r2f.DOMAIN_VALID_STATUS
    expected = {
        "revoked": "REVOCATION_PRESENT",
        "expired": "EXPIRED_OR_INACTIVE_APPROVAL",
        "hash_mismatch": "APPROVAL_HASH_MISMATCH",
    }[valid_kind]
    assert expected in manifest["domain_validation"]["diagnostics"]
    assert (generation / r2f.RENDER_BINDING_FILENAME).is_file()


def test_production_evidence_invariant_failure_creates_no_generation(
    isolated_repo: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(r2f, "build_step1a_evidence_packet_from_captured_inputs", lambda **_: {})
    with pytest.raises(
        r2f.ReplacementObservationError,
        match="PRODUCTION_EVIDENCE_INVARIANT_FAILURE",
    ):
        r2f.replacement_render()
    assert not (isolated_repo / "artifacts").exists()


def test_production_semantic_parity_failure_creates_no_generation(
    isolated_repo: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original = r2f.build_step1a_evidence_packet_from_captured_inputs

    def mismatched(**kwargs: Any) -> dict[str, Any]:
        packet = original(**kwargs)
        packet["research_anchors"] = dict(packet["research_anchors"])
        packet["research_anchors"]["path"] = "inputs/current/not-the-frozen-source.yaml"
        return packet

    monkeypatch.setattr(r2f, "build_step1a_evidence_packet_from_captured_inputs", mismatched)
    with pytest.raises(r2f.ReplacementObservationError, match="PRODUCTION_PARITY_FAILURE"):
        r2f.replacement_render()
    assert not (isolated_repo / "artifacts").exists()


@pytest.mark.parametrize(
    "filename",
    [
        "replacement_input_manifest.json",
        "evidence_packet.json",
        "analyst_memo_prompt.txt",
        r2f.MEMO_RAW_FILENAME,
        r2f.RENDER_BINDING_FILENAME,
    ],
)
def test_failure_after_each_published_file_never_becomes_reusable(
    isolated_repo: Path,
    monkeypatch: pytest.MonkeyPatch,
    filename: str,
) -> None:
    original = r2f._atomic_create_file_at

    def fail_after_write(
        directory_fd: int,
        current: str,
        content: bytes,
        *,
        containment_guard: Any = None,
    ) -> None:
        original(
            directory_fd,
            current,
            content,
            containment_guard=containment_guard,
        )
        if current == filename:
            raise RuntimeError("failure after write")

    monkeypatch.setattr(r2f, "_atomic_create_file_at", fail_after_write)
    with pytest.raises(r2f.ReplacementObservationError, match="GENERATION_PUBLICATION_FAILURE"):
        r2f.replacement_render()
    generations = isolated_repo.joinpath(*r2f.R2F_ROOT_PARTS, r2f.GENERATIONS_DIRECTORY)
    generation = next(generations.iterdir())
    assert (generation / r2f.IN_PROGRESS_FILENAME).exists()
    monkeypatch.setattr(r2f, "_atomic_create_file_at", original)
    with pytest.raises(r2f.ReplacementObservationError, match="INCOMPLETE_GENERATION_PRESENT"):
        r2f.replacement_render()


@pytest.mark.parametrize("phase", ["before_marker", "after_marker", "marker_unlink_failure"])
def test_marker_phase_failures_never_become_reusable(
    isolated_repo: Path,
    monkeypatch: pytest.MonkeyPatch,
    phase: str,
) -> None:
    original_create = r2f._create_in_progress_marker_at
    original_remove = r2f._remove_in_progress_marker_at
    if phase in {"before_marker", "after_marker"}:
        def failed_create(directory_fd: int) -> None:
            if phase == "after_marker":
                original_create(directory_fd)
            raise RuntimeError("marker creation boundary")

        monkeypatch.setattr(r2f, "_create_in_progress_marker_at", failed_create)
    else:
        def failed_remove(_directory_fd: int) -> None:
            raise r2f.ReplacementObservationError("IN_PROGRESS_MARKER_REMOVE_FAILED")

        monkeypatch.setattr(r2f, "_remove_in_progress_marker_at", failed_remove)
    expected = (
        "IN_PROGRESS_MARKER_REMOVE_FAILED"
        if phase == "marker_unlink_failure"
        else "GENERATION_PUBLICATION_FAILURE"
    )
    with pytest.raises(r2f.ReplacementObservationError, match=expected):
        r2f.replacement_render()
    generations = isolated_repo.joinpath(*r2f.R2F_ROOT_PARTS, r2f.GENERATIONS_DIRECTORY)
    generation = next(generations.iterdir())
    if phase == "before_marker":
        assert not (generation / r2f.IN_PROGRESS_FILENAME).exists()
        assert not (generation / r2f.RENDER_BINDING_FILENAME).exists()
    else:
        assert (generation / r2f.IN_PROGRESS_FILENAME).exists()
    monkeypatch.setattr(r2f, "_create_in_progress_marker_at", original_create)
    monkeypatch.setattr(r2f, "_remove_in_progress_marker_at", original_remove)
    with pytest.raises(r2f.ReplacementObservationError, match="INCOMPLETE_GENERATION_PRESENT"):
        r2f.replacement_render()


@pytest.mark.parametrize(
    "failure_point",
    ["generation_parent", "marker_fsync", "final_generation_fsync", "final_parent_fsync"],
)
def test_directory_fsync_failures_never_become_reusable(
    isolated_repo: Path,
    monkeypatch: pytest.MonkeyPatch,
    failure_point: str,
) -> None:
    original = r2f._fsync_directory
    parent_calls = 0
    binding_calls = 0

    def failed_fsync(directory_fd: int, error_code: str) -> None:
        nonlocal parent_calls, binding_calls
        if error_code == "PARENT_FSYNC_FAILURE":
            parent_calls += 1
            if failure_point == "generation_parent" and parent_calls == 6:
                raise r2f.ReplacementObservationError(error_code)
            if failure_point == "final_parent_fsync" and parent_calls == 7:
                raise r2f.ReplacementObservationError(error_code)
        if error_code == "BINDING_DURABILITY_FAILURE":
            binding_calls += 1
            if failure_point == "marker_fsync" and binding_calls == 1:
                raise r2f.ReplacementObservationError(error_code)
            if failure_point == "final_generation_fsync" and binding_calls == 3:
                raise r2f.ReplacementObservationError(error_code)
        original(directory_fd, error_code)

    monkeypatch.setattr(r2f, "_fsync_directory", failed_fsync)
    with pytest.raises(r2f.ReplacementObservationError):
        r2f.replacement_render()
    generations = isolated_repo.joinpath(*r2f.R2F_ROOT_PARTS, r2f.GENERATIONS_DIRECTORY)
    generation = next(generations.iterdir())
    monkeypatch.setattr(r2f, "_fsync_directory", original)
    with pytest.raises(r2f.ReplacementObservationError, match="INCOMPLETE_GENERATION_PRESENT"):
        r2f.replacement_render()
    assert not (
        (generation / r2f.RENDER_BINDING_FILENAME).exists()
        and not (generation / r2f.IN_PROGRESS_FILENAME).exists()
    )


@pytest.mark.parametrize(
    "failure_point",
    [
        "artifact_write",
        "artifact_fsync",
        "binding_write",
        "binding_rename",
        "generation_fsync",
        "parent_fsync",
        "inventory_verification",
        "repository_identity",
        "input_parent_identity",
    ],
)
def test_every_precommit_failure_retains_marker_and_cannot_be_reused(
    isolated_repo: Path,
    monkeypatch: pytest.MonkeyPatch,
    failure_point: str,
) -> None:
    original_atomic = r2f._atomic_create_file_at
    original_fsync = r2f._fsync_directory
    original_verify = r2f._verify_precommit_generation
    original_revalidate = r2f._revalidate_retained_paths
    binding_written = False

    def injected_atomic(
        directory_fd: int,
        filename: str,
        content: bytes,
        *,
        containment_guard: Any = None,
    ) -> None:
        nonlocal binding_written
        if failure_point == "artifact_write" and filename == "replacement_input_manifest.json":
            raise RuntimeError("artifact write boundary")
        if failure_point == "binding_write" and filename == r2f.RENDER_BINDING_FILENAME:
            raise RuntimeError("binding write boundary")
        original_atomic(
            directory_fd,
            filename,
            content,
            containment_guard=containment_guard,
        )
        if failure_point == "artifact_fsync" and filename == "evidence_packet.json":
            raise RuntimeError("artifact fsync boundary")
        if filename == r2f.RENDER_BINDING_FILENAME:
            binding_written = True
            if failure_point == "binding_rename":
                raise RuntimeError("binding rename boundary")

    def injected_fsync(directory_fd: int, error_code: str) -> None:
        if binding_written and failure_point == "generation_fsync" and error_code == "BINDING_DURABILITY_FAILURE":
            raise r2f.ReplacementObservationError(error_code)
        if binding_written and failure_point == "parent_fsync" and error_code == "PARENT_FSYNC_FAILURE":
            raise r2f.ReplacementObservationError(error_code)
        original_fsync(directory_fd, error_code)

    def injected_verify(**kwargs: Any) -> None:
        if failure_point == "inventory_verification":
            raise r2f.ReplacementObservationError("GENERATION_INVENTORY_MISMATCH")
        original_verify(**kwargs)

    def injected_revalidate(**kwargs: Any) -> None:
        if failure_point == "repository_identity":
            raise r2f.ReplacementObservationError("REPOSITORY_IDENTITY_CHANGED")
        if failure_point == "input_parent_identity":
            raise r2f.ReplacementObservationError("INPUT_PARENT_IDENTITY_CHANGED")
        original_revalidate(**kwargs)

    monkeypatch.setattr(r2f, "_atomic_create_file_at", injected_atomic)
    monkeypatch.setattr(r2f, "_fsync_directory", injected_fsync)
    monkeypatch.setattr(r2f, "_verify_precommit_generation", injected_verify)
    monkeypatch.setattr(r2f, "_revalidate_retained_paths", injected_revalidate)
    with pytest.raises(r2f.ReplacementObservationError):
        r2f.replacement_render()

    generation = next(
        isolated_repo.joinpath(
            *r2f.R2F_ROOT_PARTS,
            r2f.GENERATIONS_DIRECTORY,
        ).iterdir()
    )
    assert (generation / r2f.IN_PROGRESS_FILENAME).is_file()

    monkeypatch.setattr(r2f, "_atomic_create_file_at", original_atomic)
    monkeypatch.setattr(r2f, "_fsync_directory", original_fsync)
    monkeypatch.setattr(r2f, "_verify_precommit_generation", original_verify)
    monkeypatch.setattr(r2f, "_revalidate_retained_paths", original_revalidate)
    with pytest.raises(r2f.ReplacementObservationError, match="INCOMPLETE_GENERATION_PRESENT"):
        r2f.replacement_render()


def test_precommit_inventory_is_exactly_five_files_plus_marker(
    isolated_repo: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original = r2f._verify_precommit_generation
    observed: frozenset[str] | None = None

    def recorded(*, generation_fd: int, **kwargs: Any) -> None:
        nonlocal observed
        observed = frozenset(os.listdir(generation_fd))
        original(generation_fd=generation_fd, **kwargs)

    monkeypatch.setattr(r2f, "_verify_precommit_generation", recorded)
    r2f.replacement_render()
    assert observed == r2f.PRECOMMIT_GENERATION_FILENAMES


def test_successful_marker_unlink_is_the_final_publication_operation(
    isolated_repo: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert not hasattr(r2f, "_force_incomplete_generation_state")
    committed = False
    operations: list[str] = []

    def guard(name: str, original: Any) -> Any:
        def wrapped(*args: Any, **kwargs: Any) -> Any:
            assert committed is False, name
            operations.append(name)
            return original(*args, **kwargs)

        return wrapped

    for name in (
        "_atomic_create_file_at",
        "_fsync_directory",
        "_verify_precommit_generation",
        "_verify_publication_paths",
        "_revalidate_retained_paths",
    ):
        monkeypatch.setattr(r2f, name, guard(name, getattr(r2f, name)))

    original_remove = r2f._remove_in_progress_marker_at

    def committed_remove(directory_fd: int) -> None:
        nonlocal committed
        original_remove(directory_fd)
        committed = True
        operations.append("marker_commit")

    monkeypatch.setattr(r2f, "_remove_in_progress_marker_at", committed_remove)
    result = r2f.replacement_render()
    assert result["generation_reused"] == "false"
    assert committed is True
    assert operations[-1] == "marker_commit"


def test_documentation_keeps_r2f1a_render_only_and_non_authoritative() -> None:
    document = Path(__file__).resolve().parents[2] / "docs/r2f1_step1_replacement_observation.md"
    text = " ".join(document.read_text(encoding="utf-8").split())
    for required in (
        "manually invoked",
        "not the authoritative Sunday weekly workflow",
        "immutable Step 1A",
        "render_generation_binding.json",
        "There is no `replacement-report` command",
        "does not parse the memo",
        "does not read or write legacy Deep Research output",
        "production_text_sha256",
        ".render_in_progress",
        "exactly the five listed entries",
        "DOMAIN_VALID_BUT_NONACTIVATING",
        "DOMAIN_INVALID_NO_GENERATION",
        "requires POSIX directory descriptors",
        "content-semantic projection",
        "Equivalent clean checkouts",
        "transient in-memory race-detection state",
        "best effort and explicitly no-throw",
        "CLI stdout is observability only",
        "R2F-1b and all later R2F",
    ):
        assert required in text
