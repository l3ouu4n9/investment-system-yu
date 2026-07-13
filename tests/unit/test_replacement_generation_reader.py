"""One-shot read-only R2F-1b-a generation and memo operation tests."""

from __future__ import annotations

from datetime import date
import json
import os
from pathlib import Path
import shutil
from typing import Any

import pytest
import yaml

from investment_orchestrator.research import replacement_generation_reader as reader
from investment_orchestrator.research import replacement_observation as r2f


def _write(path: Path, value: str | bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(value, bytes):
        path.write_bytes(value)
    else:
        path.write_text(value, encoding="utf-8")


def _setup_repo(root: Path) -> None:
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
    _write(root / "inputs/current/portfolio_snapshot.txt", "fixture portfolio\n")
    _write(
        root / "inputs/current/research_anchors.yaml",
        yaml.safe_dump(
            {
                "schema_version": "research_anchors_v1",
                "as_of_date": "2026-07-12",
                "is_llm_generated": False,
                "anchors": [{
                    "anchor_id": "ANCHOR_FIXA",
                    "anchor_type": "structural_theme",
                    "applicable_tickers": ["FIXA"],
                    "anchor_date_et": "2026-07-01",
                    "valid_from": "2026-07-01",
                    "valid_until": "2026-12-31",
                    "source_type": "operator",
                    "confidence_floor": "medium",
                }],
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
    _write(root / "prompts/analyst_memo.txt", "MEMO\n{{ evidence_packet_json }}\n")


@pytest.fixture
def rendered_generation(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, str, Path]:
    root = tmp_path / "repo"
    _setup_repo(root)
    monkeypatch.setattr(r2f, "repo_root", lambda: root)
    monkeypatch.setattr(r2f, "_today", lambda: date(2026, 7, 12))
    result = r2f.replacement_render()
    return root, result["generation_id"], Path(result["generation_path"])


def _capture(root: Path, generation_id: str) -> reader._VerifiedMemoInput:
    return reader._validate_generation_memo_operation_at_root_for_tests(
        generation_id,
        root,
        lambda value: value,
    )


def _write_bound_memo(root: Path, generation_id: str, generation: Path) -> bytes:
    source = _capture(root, generation_id).source_binding
    raw = json.dumps(
        {
            "schema_version": "r2f_analyst_memo_envelope_v1",
            "source_binding": source.to_dict(),
            "memo_result": "NO_TRADE",
            "confidence": "LOW",
            "instrument_observations": [],
        },
        sort_keys=True,
    ).encode("utf-8")
    (generation / reader.MEMO_RAW_FILENAME).write_bytes(raw)
    return raw


def test_one_shot_reader_returns_only_pure_input_after_cleanup(
    rendered_generation: tuple[Path, str, Path],
) -> None:
    root, generation_id, generation = rendered_generation
    raw = _write_bound_memo(root, generation_id, generation)
    value = _capture(root, generation_id)
    assert value.source_binding.r2f1a_generation_id == generation_id
    assert [item.instrument_id for item in value.eligible_instruments] == ["FIXA", "FIXB", "FIXC"]
    assert value.active_anchor_ids == ("ANCHOR_FIXA",)
    assert value.memo_raw.raw_bytes == raw
    assert not hasattr(value, "close")
    assert not any("fd" in name or "path" in name for name in value.__dataclass_fields__)


@pytest.mark.parametrize("mutation,code", [
    ("marker", "SOURCE_GENERATION_INCOMPLETE"),
    ("extra", "SOURCE_GENERATION_INVALID"),
    ("missing", "SOURCE_GENERATION_INCOMPLETE"),
    ("directory", "SOURCE_GENERATION_INVALID"),
    ("symlink", "SOURCE_GENERATION_INVALID"),
    ("fifo", "SOURCE_GENERATION_INVALID"),
])
def test_exact_inventory_is_required(
    rendered_generation: tuple[Path, str, Path], mutation: str, code: str
) -> None:
    root, generation_id, generation = rendered_generation
    if mutation == "marker": (generation / reader.IN_PROGRESS_FILENAME).write_text("x")
    elif mutation == "extra": (generation / ".extra").write_text("x")
    elif mutation == "missing": (generation / reader.RENDER_BINDING_FILENAME).unlink()
    elif mutation == "directory":
        (generation / reader.MEMO_RAW_FILENAME).unlink(); (generation / reader.MEMO_RAW_FILENAME).mkdir()
    elif mutation == "symlink":
        (generation / reader.MEMO_RAW_FILENAME).unlink()
        (generation / reader.MEMO_RAW_FILENAME).symlink_to(root / "inputs/current/portfolio_snapshot.txt")
    else:
        if not hasattr(os, "mkfifo"):
            pytest.skip("FIFO creation is unavailable")
        (generation / reader.MEMO_RAW_FILENAME).unlink()
        os.mkfifo(generation / reader.MEMO_RAW_FILENAME)
    with pytest.raises(reader.ReplacementGenerationReaderError, match=code): _capture(root, generation_id)


@pytest.mark.parametrize("filename", [reader.MANIFEST_FILENAME, reader.EVIDENCE_FILENAME, reader.PROMPT_FILENAME, reader.RENDER_BINDING_FILENAME])
def test_immutable_artifact_substitution_fails(
    rendered_generation: tuple[Path, str, Path], filename: str
) -> None:
    root, generation_id, generation = rendered_generation
    path = generation / filename
    path.write_bytes(path.read_bytes() + b" ")
    with pytest.raises(reader.ReplacementGenerationReaderError, match="SOURCE_GENERATION_INVALID"): _capture(root, generation_id)


def test_generation_id_is_exact_lowercase_sha256(rendered_generation: tuple[Path, str, Path]) -> None:
    root, _generation_id, _generation = rendered_generation
    for value in ("A" * 64, "a" * 63, "../" + "a" * 62):
        with pytest.raises(reader.ReplacementGenerationReaderError, match="SOURCE_GENERATION_ID_INVALID"): _capture(root, value)


def test_same_inode_overwrite_between_two_reads_fails(
    rendered_generation: tuple[Path, str, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    root, generation_id, generation = rendered_generation
    memo_path = generation / reader.MEMO_RAW_FILENAME
    memo_path.write_bytes(b"A" * 256)
    real = reader._read_bounded_descriptor
    calls = 0
    def mutate(fd: int, **kwargs: Any) -> bytes:
        nonlocal calls
        value = real(fd, **kwargs); calls += 1
        if calls == 1: memo_path.write_bytes(b"B" * 256)
        return value
    monkeypatch.setattr(reader, "_read_bounded_descriptor", mutate)
    with pytest.raises(reader.ReplacementGenerationReaderError, match="MEMO_SOURCE_UNSTABLE"): _capture(root, generation_id)


def test_rename_replace_after_memo_open_fails(
    rendered_generation: tuple[Path, str, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    root, generation_id, generation = rendered_generation
    memo_path = generation / reader.MEMO_RAW_FILENAME
    memo_path.write_bytes(b"memo")
    real = reader._read_bounded_descriptor
    replaced = False
    def replace(fd: int, **kwargs: Any) -> bytes:
        nonlocal replaced
        value = real(fd, **kwargs)
        if not replaced:
            replaced = True
            backup = generation / "old.memo"; memo_path.rename(backup); memo_path.write_bytes(b"new!")
        return value
    monkeypatch.setattr(reader, "_read_bounded_descriptor", replace)
    with pytest.raises(reader.ReplacementGenerationReaderError, match="MEMO_SOURCE_UNSTABLE"): _capture(root, generation_id)


def test_generation_directory_replacement_during_operation_fails(
    rendered_generation: tuple[Path, str, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    root, generation_id, generation = rendered_generation
    displaced = generation.parent / "displaced"
    replacement = generation.parent / "replacement"
    shutil.copytree(generation, replacement)
    real = reader._capture_verified_memo_at
    def replace(*args: Any, **kwargs: Any) -> bytes:
        generation.rename(displaced); replacement.rename(generation)
        return real(*args, **kwargs)
    monkeypatch.setattr(reader, "_capture_verified_memo_at", replace)
    with pytest.raises(reader.ReplacementGenerationReaderError, match="SOURCE_GENERATION_INVALID"): _capture(root, generation_id)


def test_memo_size_boundary(rendered_generation: tuple[Path, str, Path]) -> None:
    root, generation_id, generation = rendered_generation
    memo_path = generation / reader.MEMO_RAW_FILENAME
    memo_path.write_bytes(b"x" * 65_536)
    assert _capture(root, generation_id).memo_raw.byte_size == 65_536
    memo_path.write_bytes(b"x" * 65_537)
    with pytest.raises(reader.ReplacementGenerationReaderError, match="MEMO_TOO_LARGE"): _capture(root, generation_id)


def test_reader_is_read_only(rendered_generation: tuple[Path, str, Path]) -> None:
    root, generation_id, generation = rendered_generation
    _write_bound_memo(root, generation_id, generation)
    before = {p.relative_to(root): p.read_bytes() for p in root.rglob("*") if p.is_file()}
    _capture(root, generation_id)
    after = {p.relative_to(root): p.read_bytes() for p in root.rglob("*") if p.is_file()}
    assert after == before


def test_no_persistent_handle_registry_or_close_surface() -> None:
    assert reader.__all__ == ("ReplacementGenerationReaderError",)
    for name in ("open_verified_generation", "VerifiedR2F1aGeneration", "_LIVE_VERIFIED_HANDLES", "_VerifiedHandleSeal"):
        assert not hasattr(reader, name)


def test_success_and_validation_failure_return_fd_count_to_baseline(
    rendered_generation: tuple[Path, str, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    if not Path("/proc/self/fd").exists(): pytest.skip("/proc fd accounting unavailable")
    root, generation_id, generation = rendered_generation
    _write_bound_memo(root, generation_id, generation)
    baseline = len(list(Path("/proc/self/fd").iterdir()))
    for _ in range(5): _capture(root, generation_id)
    assert len(list(Path("/proc/self/fd").iterdir())) == baseline


@pytest.mark.parametrize(
    "phase",
    [
        "repository_open",
        "artifacts_directory_open",
        "r2f_root_directory_open",
        "generations_directory_open",
        "generation_directory_open",
        "inventory",
        "manifest_read",
        "evidence_read",
        "prompt_read",
        "binding_read",
        "semantic_identity",
        "memo_capture",
        "validator",
    ],
)
def test_processing_boundary_failure_closes_every_acquired_descriptor(
    rendered_generation: tuple[Path, str, Path],
    monkeypatch: pytest.MonkeyPatch,
    phase: str,
) -> None:
    if not Path("/proc/self/fd").exists():
        pytest.skip("/proc fd accounting unavailable")
    root, generation_id, generation = rendered_generation
    _write_bound_memo(root, generation_id, generation)
    baseline = len(list(Path("/proc/self/fd").iterdir()))

    def fail() -> None:
        raise RuntimeError("PRIVATE_BOUNDARY_FAILURE")

    if phase == "repository_open":
        monkeypatch.setattr(reader, "_open_absolute_directory_chain", lambda *_a, **_k: fail())
    elif phase.endswith("_directory_open"):
        real_open_directory = reader._open_directory_at
        calls = 0
        fail_at = {
            "artifacts_directory_open": 1,
            "r2f_root_directory_open": 4,
            "generations_directory_open": 5,
            "generation_directory_open": 6,
        }[phase]

        def fail_selected_directory(*args: Any, **kwargs: Any) -> int:
            nonlocal calls
            calls += 1
            if calls == fail_at:
                fail()
            return real_open_directory(*args, **kwargs)

        monkeypatch.setattr(reader, "_open_directory_at", fail_selected_directory)
    elif phase == "inventory":
        monkeypatch.setattr(reader, "_generation_entry_names", lambda *_a, **_k: fail())
    elif phase.endswith("_read"):
        target = {
            "manifest_read": reader.MANIFEST_FILENAME,
            "evidence_read": reader.EVIDENCE_FILENAME,
            "prompt_read": reader.PROMPT_FILENAME,
            "binding_read": reader.RENDER_BINDING_FILENAME,
        }[phase]
        real_read = reader._read_stable_regular_file_at

        def fail_selected_read(directory_fd: int, filename: str, **kwargs: Any) -> bytes:
            if filename == target:
                fail()
            return real_read(directory_fd, filename, **kwargs)

        monkeypatch.setattr(reader, "_read_stable_regular_file_at", fail_selected_read)
    elif phase == "semantic_identity":
        monkeypatch.setattr(reader, "_semantic_generation_identity", lambda *_a, **_k: fail())
    elif phase == "memo_capture":
        monkeypatch.setattr(reader, "_capture_verified_memo_at", lambda *_a, **_k: fail())

    validator = (lambda _value: fail()) if phase == "validator" else (lambda value: value)
    with pytest.raises(reader.ReplacementGenerationReaderError) as raised:
        reader._validate_generation_memo_operation_at_root_for_tests(
            generation_id,
            root,
            validator,
        )
    assert raised.value.args == ("SOURCE_GENERATION_INVALID",)
    assert raised.value.__cause__ is None and raised.value.__context__ is None
    assert len(list(Path("/proc/self/fd").iterdir())) == baseline
    monkeypatch.setattr(reader, "_read_bounded_descriptor", lambda *_a, **_k: (_ for _ in ()).throw(RuntimeError("PRIVATE")))
    for _ in range(5):
        with pytest.raises(reader.ReplacementGenerationReaderError): _capture(root, generation_id)
    assert len(list(Path("/proc/self/fd").iterdir())) == baseline


@pytest.mark.parametrize("positions", [(0,), (3,), (-1,), (0, 2, -1)])
def test_cleanup_failure_discards_result_and_attempts_every_descriptor(
    rendered_generation: tuple[Path, str, Path], monkeypatch: pytest.MonkeyPatch, positions: tuple[int, ...]
) -> None:
    root, generation_id, generation = rendered_generation
    _write_bound_memo(root, generation_id, generation)
    real_owner = reader._DescriptorOwner
    owners: list[reader._DescriptorOwner] = []
    totals: list[int] = []
    class TrackingOwner(real_owner):
        def __init__(self) -> None:
            super().__init__(); owners.append(self)
        def close_all(self) -> bool:
            totals.append(len(self._descriptors))
            return super().close_all()
    monkeypatch.setattr(reader, "_DescriptorOwner", TrackingOwner)
    real_close = reader.os.close
    attempts: list[int] = []
    failed_descriptors: list[int] = []
    def fail(fd: int) -> None:
        total = totals[0]
        index = len(attempts); attempts.append(fd)
        normalized = {total - 1 if value == -1 else value for value in positions}
        if index in normalized:
            failed_descriptors.append(fd); raise OSError("PRIVATE")
        real_close(fd)
    monkeypatch.setattr(reader.os, "close", fail)
    with pytest.raises(reader.ReplacementGenerationReaderError) as raised: _capture(root, generation_id)
    assert raised.value.args == ("SOURCE_GENERATION_CLEANUP_FAILED",)
    assert raised.value.__cause__ is None and raised.value.__context__ is None
    assert len(attempts) == totals[0]
    assert len(attempts) == len(set(attempts))
    monkeypatch.setattr(reader.os, "close", real_close)
    for fd in failed_descriptors:
        try: real_close(fd)
        except OSError: pass


@pytest.mark.parametrize("filename", [reader.MANIFEST_FILENAME, reader.EVIDENCE_FILENAME, reader.RENDER_BINDING_FILENAME])
def test_malformed_source_json_exception_is_private(
    rendered_generation: tuple[Path, str, Path], filename: str
) -> None:
    root, generation_id, generation = rendered_generation
    (generation / filename).write_bytes(b'{"private":"SENTINEL"')
    with pytest.raises(reader.ReplacementGenerationReaderError) as raised: _capture(root, generation_id)
    assert raised.value.args == ("SOURCE_GENERATION_INVALID",)
    assert raised.value.__cause__ is None and raised.value.__context__ is None
    assert vars(raised.value) == {"code": "SOURCE_GENERATION_INVALID"}
