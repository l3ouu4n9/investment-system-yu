"""WS01b read-only R2F v2 source-verifier and adapter tests."""

from __future__ import annotations

import ast
from dataclasses import replace
from datetime import date
import inspect
import json
import os
from pathlib import Path
import shutil
import time
from types import MappingProxyType
from typing import Any

import pytest
import yaml

from investment_orchestrator.observability import weekly_shadow_01_contracts as contracts
from investment_orchestrator.observability import weekly_shadow_01_package_builder as builder
from investment_orchestrator.observability import weekly_shadow_01_source_adapter as adapter
from investment_orchestrator.research import replacement_observation as r2f


_SCHEMA_FILES = tuple(contracts.SCHEMA_FILENAME_BY_VERSION.values())


def _write(path: Path, value: str | bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(value, bytes):
        path.write_bytes(value)
    else:
        path.write_text(value, encoding="utf-8")


def _anchor(anchor_id: str = "ANCHOR_FIXA", ticker: str = "FIXA") -> dict[str, Any]:
    return {
        "anchor_id": anchor_id,
        "anchor_type": "structural_theme",
        "applicable_tickers": [ticker],
        "anchor_date_et": "2026-07-01",
        "valid_from": "2026-07-01",
        "valid_until": "2026-12-31",
        "source_type": "operator",
        "confidence_floor": "medium",
        "summary": f"Verified summary for {anchor_id}.",
    }


def _setup_repo(root: Path, *, anchors: list[dict[str, Any]] | None = None) -> None:
    source_root = Path(__file__).parents[2]
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
                "anchors": anchors if anchors is not None else [_anchor()],
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
    for relative in _SCHEMA_FILES:
        _write(root / relative, (source_root / relative).read_bytes())
    contract_relative = (
        "src/investment_orchestrator/observability/weekly_shadow_01_contracts.py"
    )
    _write(root / contract_relative, (source_root / contract_relative).read_bytes())


@pytest.fixture
def rendered_generation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> tuple[Path, str, Path]:
    root = tmp_path / "repo"
    _setup_repo(root)
    monkeypatch.setattr(r2f, "repo_root", lambda: root)
    monkeypatch.setattr(r2f, "_today", lambda: date(2026, 7, 12))
    result = r2f.replacement_render()
    return root, result["generation_id"], Path(result["generation_path"])


def _rewrite_json(path: Path, mutation) -> None:
    value = json.loads(path.read_text(encoding="utf-8"))
    mutation(value)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _expect_success(result: object) -> object:
    assert type(result).__name__ == "_WS01bResult"
    assert type(result).__module__.rsplit(".", 1)[-1] in {
        "weekly_shadow_01_source_adapter",
        "weekly_shadow_01_package_builder",
    }
    assert result.ok is True
    assert result.reason_code is None
    assert result.value is not None
    return result.value


def _verify_result(fixture: tuple[Path, str, Path]) -> object:
    root, generation_id, _ = fixture
    return adapter.verify_r2f_v2_generation(generation_id, repository_root=root)


def _verify(fixture: tuple[Path, str, Path]) -> object:
    return _expect_success(_verify_result(fixture))


def _snapshot_from_verified(verified: object) -> object:
    return adapter._build_source_snapshot(verified)


def _snapshot(fixture: tuple[Path, str, Path]) -> object:
    root, generation_id, _ = fixture
    return _expect_success(
        adapter.build_source_snapshot(generation_id, repository_root=root)
    )


def _package_from_snapshot(snapshot: object) -> object:
    return builder._build_analyst_input_package(snapshot)


def _render_from_package(package: object) -> object:
    return builder._render_analyst_prompt(package)


def _expect_contract_surface_rejection(
    fixture: tuple[Path, str, Path],
) -> None:
    _assert_failure_result(
        _verify_result(fixture),
        "WS01_BR_INTERNAL_INVARIANT_FAILURE",
    )


def _assert_failure_result(
    result: object,
    expected_code: str,
    *forbidden_values: object,
) -> None:
    assert type(result) is adapter._WS01bResult
    assert result.ok is False
    assert result.value is None
    assert result.reason_code == expected_code
    assert not isinstance(result, BaseException)
    assert not hasattr(result, "__dict__")
    assert not hasattr(result, "__traceback__")
    assert not hasattr(result, "__cause__")
    assert not hasattr(result, "__context__")
    assert type(result).__slots__ == ("ok", "value", "reason_code")
    rendered = repr(result)
    for forbidden in forbidden_values:
        if str(forbidden):
            assert str(forbidden) not in rendered
    with pytest.raises((AttributeError, TypeError)):
        result.reason_code = "WS01_BR_SOURCE_GENERATION_INVALID"


def _assert_no_reachable_exception(value: object) -> None:
    pending = [value]
    seen: set[int] = set()
    while pending:
        current = pending.pop()
        if id(current) in seen:
            continue
        seen.add(id(current))
        if isinstance(current, BaseException):
            BaseException.__getattribute__(current, "__traceback__")
            pytest.fail("an exception object is reachable from a WS01b result")
        if type(current) in {tuple, list, frozenset}:
            pending.extend(current)
        elif isinstance(current, MappingProxyType):
            pending.extend(current.values())
        else:
            for slot in getattr(type(current), "__slots__", ()):
                if hasattr(current, slot):
                    pending.append(getattr(current, slot))


def _tamper_extracted_contract_values(
    monkeypatch: pytest.MonkeyPatch,
    mutation,
) -> None:
    original = adapter._contract_exports_from_source

    def tampered(contract_source: bytes) -> dict[str, object]:
        values = original(contract_source)
        mutation(values)
        return values

    monkeypatch.setattr(adapter, "_contract_exports_from_source", tampered)


def _deterministic_ws01b_result(
    fixture: tuple[Path, str, Path],
) -> tuple[str, str, bytes, str, bytes, str]:
    root, generation_id, _ = fixture
    verified = _expect_success(
        adapter.verify_r2f_v2_generation(generation_id, repository_root=root)
    )
    snapshot = _expect_success(
        adapter.build_source_snapshot(generation_id, repository_root=root)
    )
    package = _expect_success(
        builder.build_analyst_input_package(generation_id, repository_root=root)
    )
    rendered = _expect_success(
        builder.render_analyst_prompt(generation_id, repository_root=root)
    )
    return (
        verified.evaluation_timestamp_utc,
        snapshot.snapshot_identity_sha256,
        package.canonical_json_bytes,
        package.input_package_identity_sha256,
        rendered.prompt_bytes,
        rendered.prompt_render_identity_sha256,
    )


def test_valid_explicit_generation_is_verified_and_projected_losslessly(
    rendered_generation: tuple[Path, str, Path],
) -> None:
    verified = _verify(rendered_generation)
    snapshot = _snapshot_from_verified(verified)
    assert verified.source_generation_id == rendered_generation[1]
    assert verified.source_generation_version == "step1_replacement_render_observation_v2"
    assert verified.evaluation_timestamp_utc == "2026-07-12T00:00:00Z"
    assert tuple(item.source_id for item in verified.source_artifact_bindings) == (
        "replacement_input_manifest.json",
        "evidence_packet.json",
        "analyst_memo_prompt.txt",
        "render_generation_binding.json",
    )
    assert snapshot.active_anchors[0]["anchor_id"] == "ANCHOR_FIXA"
    assert snapshot.active_anchors[0]["normalized_value"]["applicable_tickers"] == (
        "FIXA",
    )
    assert snapshot.active_anchors[0]["normalized_value"]["summary"] == (
        "Verified summary for ANCHOR_FIXA."
    )


@pytest.mark.parametrize(
    "generation_id",
    [
        "",
        "a" * 63,
        "a" * 65,
        "A" * 64,
        "g" + "a" * 63,
        " " + "a" * 63,
        "a" * 63 + " ",
        "a/" + "a" * 62,
        "a\\" + "a" * 62,
        "../" + "a" * 61,
        "%2f" + "a" * 61,
        "current",
        "latest",
        "active",
    ],
)
def test_generation_selection_requires_exact_lowercase_sha256(generation_id: str) -> None:
    _assert_failure_result(
        adapter.verify_r2f_v2_generation(generation_id),
        "WS01_BR_SOURCE_GENERATION_INVALID",
        generation_id,
    )


def test_missing_generation_has_no_scan_newest_or_fallback(
    rendered_generation: tuple[Path, str, Path],
) -> None:
    root, _, _ = rendered_generation
    _assert_failure_result(
        adapter.verify_r2f_v2_generation("f" * 64, repository_root=root),
        "WS01_BR_SOURCE_READ_UNSTABLE",
    )


def test_incomplete_and_unknown_generation_entries_fail_closed(
    rendered_generation: tuple[Path, str, Path],
) -> None:
    root, generation_id, generation = rendered_generation
    (generation / ".render_in_progress").write_bytes(b"")
    _assert_failure_result(
        adapter.verify_r2f_v2_generation(generation_id, repository_root=root),
        "WS01_BR_SOURCE_GENERATION_INVALID",
    )
    (generation / ".render_in_progress").unlink()
    unknown_name = "unknown-ENTRY-SECRET-22F1.txt"
    (generation / unknown_name).write_bytes(b"unknown")
    _assert_failure_result(
        adapter.verify_r2f_v2_generation(generation_id, repository_root=root),
        "WS01_BR_SOURCE_ARTIFACT_SET_MISMATCH",
        unknown_name,
        generation_id,
        generation,
        root,
    )


def test_raw_analyst_output_is_never_opened_statted_read_hashed_or_bound(
    rendered_generation: tuple[Path, str, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    root, generation_id, _ = rendered_generation
    original_open = adapter._os.open
    original_stat = adapter._os.stat

    def guarded_open(path, *args, **kwargs):
        assert path != "analyst_memo_raw_output.txt"
        return original_open(path, *args, **kwargs)

    def guarded_stat(path, *args, **kwargs):
        assert path != "analyst_memo_raw_output.txt"
        return original_stat(path, *args, **kwargs)

    monkeypatch.setattr(adapter, "_require_descriptor_primitives", lambda: None)
    monkeypatch.setattr(adapter._os, "open", guarded_open)
    monkeypatch.setattr(adapter._os, "stat", guarded_stat)
    verified = _expect_success(
        adapter.verify_r2f_v2_generation(generation_id, repository_root=root)
    )
    assert "analyst_memo_raw_output.txt" not in {
        item.source_id for item in verified.source_artifact_bindings
    }


@pytest.mark.parametrize("filename", contracts.CONSUMED_SOURCE_ARTIFACT_ROLES)
def test_consumed_source_symlink_is_rejected(
    rendered_generation: tuple[Path, str, Path], filename: str
) -> None:
    root, generation_id, generation = rendered_generation
    target = generation / filename
    target.unlink()
    target.symlink_to("/etc/passwd")
    _assert_failure_result(
        adapter.verify_r2f_v2_generation(generation_id, repository_root=root),
        "WS01_BR_SOURCE_READ_UNSTABLE",
    )


def test_symlinked_repository_path_component_is_rejected(
    rendered_generation: tuple[Path, str, Path], tmp_path: Path
) -> None:
    root, generation_id, _ = rendered_generation
    relocated = tmp_path / "relocated"
    shutil.copytree(root, relocated)
    linked = tmp_path / "linked"
    linked.symlink_to(relocated, target_is_directory=True)
    _assert_failure_result(
        adapter.verify_r2f_v2_generation(generation_id, repository_root=linked),
        "WS01_BR_SOURCE_READ_UNSTABLE",
    )


def test_nonregular_consumed_source_is_rejected_without_blocking(
    rendered_generation: tuple[Path, str, Path],
) -> None:
    root, generation_id, generation = rendered_generation
    prompt = generation / "analyst_memo_prompt.txt"
    prompt.unlink()
    os.mkfifo(prompt)
    _assert_failure_result(
        adapter.verify_r2f_v2_generation(generation_id, repository_root=root),
        "WS01_BR_SOURCE_READ_UNSTABLE",
    )


def test_changed_file_identity_during_read_fails_without_retry(
    rendered_generation: tuple[Path, str, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    root, generation_id, generation = rendered_generation
    target_inode = (generation / "replacement_input_manifest.json").stat().st_ino
    original = adapter._regular_file_state
    calls = 0

    def unstable(value):
        nonlocal calls
        state = original(value)
        if state.inode == target_inode:
            calls += 1
            if calls >= 3:
                return replace(state, mtime_ns=state.mtime_ns + 1)
        return state

    monkeypatch.setattr(adapter, "_regular_file_state", unstable)
    _assert_failure_result(
        adapter.verify_r2f_v2_generation(generation_id, repository_root=root),
        "WS01_BR_SOURCE_READ_UNSTABLE",
    )


def test_same_size_in_place_byte_race_fails_when_metadata_witnesses_look_stable(
    rendered_generation: tuple[Path, str, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    root, generation_id, generation = rendered_generation
    target = generation / "replacement_input_manifest.json"
    original_state = adapter._regular_file_state
    original_read = adapter._os.read
    fixed = original_state(target.stat())
    mutation_count = 0

    def masked_state(value):
        observed = original_state(value)
        if (observed.device, observed.inode) == (fixed.device, fixed.inode):
            return fixed
        return observed

    def racing_read(descriptor: int, count: int) -> bytes:
        nonlocal mutation_count
        chunk = original_read(descriptor, count)
        if mutation_count == 0 and os.fstat(descriptor).st_ino == fixed.inode and chunk:
            raw = target.read_bytes()
            changed = (b"X" if raw[:1] != b"X" else b"Y") + raw[1:]
            with target.open("r+b", buffering=0) as handle:
                handle.write(changed)
            mutation_count += 1
        return chunk

    monkeypatch.setattr(adapter, "_regular_file_state", masked_state)
    monkeypatch.setattr(adapter._os, "read", racing_read)
    _assert_failure_result(
        adapter.verify_r2f_v2_generation(generation_id, repository_root=root),
        "WS01_BR_SOURCE_READ_UNSTABLE",
    )
    assert mutation_count == 1


def test_mutation_after_file_snapshot_before_generation_second_pass_fails(
    rendered_generation: tuple[Path, str, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    root, generation_id, generation = rendered_generation
    target = generation / "replacement_input_manifest.json"
    original = adapter._read_stable_regular_file_snapshot_at
    manifest_reads = 0

    def mutate_after_snapshot(directory_fd, filename, **kwargs):
        nonlocal manifest_reads
        snapshot = original(directory_fd, filename, **kwargs)
        if filename == "replacement_input_manifest.json":
            manifest_reads += 1
            if manifest_reads == 1:
                raw = target.read_bytes()
                with target.open("r+b", buffering=0) as handle:
                    handle.write((b"X" if raw[:1] != b"X" else b"Y") + raw[1:])
        return snapshot

    monkeypatch.setattr(
        adapter,
        "_read_stable_regular_file_snapshot_at",
        mutate_after_snapshot,
    )
    _assert_failure_result(
        adapter.verify_r2f_v2_generation(generation_id, repository_root=root),
        "WS01_BR_SOURCE_READ_UNSTABLE",
    )
    assert manifest_reads == 2


def test_same_size_path_entry_replacement_between_generation_passes_fails(
    rendered_generation: tuple[Path, str, Path],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root, generation_id, generation = rendered_generation
    target = generation / "replacement_input_manifest.json"
    replacement = tmp_path / "replacement.json"
    raw = target.read_bytes()
    replacement.write_bytes((b"X" if raw[:1] != b"X" else b"Y") + raw[1:])
    original = adapter._read_stable_regular_file_snapshot_at
    replacement_count = 0

    def replace_after_first_pass(directory_fd, filename, **kwargs):
        nonlocal replacement_count
        snapshot = original(directory_fd, filename, **kwargs)
        if filename == "render_generation_binding.json" and replacement_count == 0:
            os.replace(replacement, target)
            replacement_count += 1
        return snapshot

    monkeypatch.setattr(
        adapter,
        "_read_stable_regular_file_snapshot_at",
        replace_after_first_pass,
    )
    _assert_failure_result(
        adapter.verify_r2f_v2_generation(generation_id, repository_root=root),
        "WS01_BR_SOURCE_READ_UNSTABLE",
    )
    assert replacement_count == 1


def test_individual_source_byte_bound_is_checked_before_allocation(
    rendered_generation: tuple[Path, str, Path],
) -> None:
    root, generation_id, generation = rendered_generation
    (generation / "analyst_memo_prompt.txt").write_bytes(b"x" * (1_048_576 + 1))
    _assert_failure_result(
        adapter.verify_r2f_v2_generation(generation_id, repository_root=root),
        "WS01_BR_RESOURCE_BOUND_EXCEEDED",
    )


def test_repository_relocation_and_cwd_do_not_change_verification(
    rendered_generation: tuple[Path, str, Path], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root, generation_id, _ = rendered_generation
    first = _expect_success(
        adapter.build_source_snapshot(generation_id, repository_root=root)
    )
    relocated = tmp_path / "elsewhere" / "repo"
    shutil.copytree(root, relocated)
    monkeypatch.chdir("/")
    second = _expect_success(
        adapter.build_source_snapshot(
            generation_id,
            repository_root=relocated,
        )
    )
    assert first.snapshot_identity_sha256 == second.snapshot_identity_sha256
    assert first.source_artifact_bindings == second.source_artifact_bindings


def test_source_bound_time_is_independent_of_machine_clock_and_timezone(
    rendered_generation: tuple[Path, str, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    simulated_clocks = (
        date(2020, 1, 1),
        date(2026, 7, 12),
        date(2035, 12, 31),
    )
    timezones = ("UTC0", "PST8PDT", "JST-9")
    today_calls: list[date] = []
    results: list[tuple[str, str, bytes, str, bytes, str]] = []
    original_timezone = os.environ.get("TZ")

    try:
        for simulated_clock in simulated_clocks:
            class SimulatedDate:
                @staticmethod
                def fromisoformat(value: str) -> date:
                    return date.fromisoformat(value)

                @staticmethod
                def today() -> date:
                    today_calls.append(simulated_clock)
                    return simulated_clock

            monkeypatch.setattr(adapter, "_date", SimulatedDate)
            for timezone in timezones:
                os.environ["TZ"] = timezone
                if hasattr(time, "tzset"):
                    time.tzset()
                results.append(_deterministic_ws01b_result(rendered_generation))
    finally:
        if original_timezone is None:
            os.environ.pop("TZ", None)
        else:
            os.environ["TZ"] = original_timezone
        if hasattr(time, "tzset"):
            time.tzset()

    assert today_calls == []
    assert all(result == results[0] for result in results)
    assert results[0][0] == "2026-07-12T00:00:00Z"


@pytest.mark.parametrize(
    "filename,mutation,expected_code",
    [
        (
            "replacement_input_manifest.json",
            lambda value: value.pop("as_of"),
            "WS01_BR_SOURCE_GENERATION_INVALID",
        ),
        (
            "replacement_input_manifest.json",
            lambda value: value.__setitem__("as_of", "2026-7-12"),
            "WS01_BR_SOURCE_GENERATION_INVALID",
        ),
        (
            "replacement_input_manifest.json",
            lambda value: value.__setitem__("as_of", "2026-07-11"),
            "WS01_BR_SOURCE_BINDING_MISMATCH",
        ),
        (
            "replacement_input_manifest.json",
            lambda value: value.pop("generated_at"),
            "WS01_BR_SOURCE_GENERATION_INVALID",
        ),
        (
            "replacement_input_manifest.json",
            lambda value: value.__setitem__(
                "generated_at", "2026-07-12T00:00:00Z"
            ),
            "WS01_BR_SOURCE_BINDING_MISMATCH",
        ),
        (
            "evidence_packet.json",
            lambda value: value.pop("generated_at"),
            "WS01_BR_SOURCE_GENERATION_INVALID",
        ),
        (
            "evidence_packet.json",
            lambda value: value.__setitem__(
                "generated_at", "2026-07-11T00:00:00+00:00"
            ),
            "WS01_BR_SOURCE_BINDING_MISMATCH",
        ),
        (
            "evidence_packet.json",
            lambda value: value["strategy_settings_summary"].__setitem__(
                "as_of", "2026-07-11"
            ),
            "WS01_BR_SOURCE_BINDING_MISMATCH",
        ),
        (
            "evidence_packet.json",
            lambda value: value["active_anchor_registry"].__setitem__(
                "generated_at", "2026-07-11T00:00:00+00:00"
            ),
            "WS01_BR_SOURCE_BINDING_MISMATCH",
        ),
    ],
)
def test_missing_malformed_or_contradictory_source_time_fails_closed(
    rendered_generation: tuple[Path, str, Path],
    filename: str,
    mutation,
    expected_code: str,
) -> None:
    root, generation_id, generation = rendered_generation
    _rewrite_json(generation / filename, mutation)
    _assert_failure_result(
        adapter.verify_r2f_v2_generation(generation_id, repository_root=root),
        expected_code,
    )


def test_consumed_file_mtimes_are_not_evaluation_time_authority(
    rendered_generation: tuple[Path, str, Path],
) -> None:
    _, _, generation = rendered_generation
    before = _deterministic_ws01b_result(rendered_generation)
    for index, role in enumerate(contracts.CONSUMED_SOURCE_ARTIFACT_ROLES):
        timestamp_ns = 1_600_000_000_000_000_000 + index
        os.utime(generation / role, ns=(timestamp_ns, timestamp_ns))
    after = _deterministic_ws01b_result(rendered_generation)
    assert after == before


def test_adapter_has_no_wall_clock_or_production_freshness_dependency() -> None:
    source = Path(adapter.__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)
    forbidden_calls = {
        ("_date", "today"),
        ("date", "today"),
        ("_datetime", "now"),
        ("datetime", "now"),
        ("_time", "time"),
        ("time", "time"),
    }
    observed_calls = {
        (call.func.value.id, call.func.attr)
        for call in ast.walk(tree)
        if isinstance(call, ast.Call)
        and isinstance(call.func, ast.Attribute)
        and isinstance(call.func.value, ast.Name)
    }
    imported_modules = {
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    } | {
        node.module or ""
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
    }
    assert observed_calls.isdisjoint(forbidden_calls)
    assert not any(
        module.startswith("investment_orchestrator.state")
        for module in imported_modules
    )


def test_all_ws01a2_schema_and_semantic_identities_are_verified_from_disk(
    rendered_generation: tuple[Path, str, Path],
) -> None:
    verified = _verify(rendered_generation)
    assert contracts.SCHEMA_IDENTITY_SHA256_BY_VERSION == adapter._EXPECTED_SCHEMA_IDENTITIES
    assert contracts.SEMANTIC_CONTRACT_IDENTITY_SHA256_BY_VERSION == (
        adapter._EXPECTED_SEMANTIC_IDENTITIES
    )
    assert verified.contract_catalog_identity_sha256 == (
        "36a0f850a089c3276c62dfe677ebfbce1ee9d1289e0487c3aad358db6cb556d4"
    )
    assert verified.analyst_input_schema["$id"].endswith(
        "weekly_shadow_01_analyst_input_v2.schema.json"
    )


def test_safe_contract_source_evaluation_matches_exported_metadata() -> None:
    values = adapter._contract_exports_from_source(Path(contracts.__file__).read_bytes())
    metadata_names = tuple(
        name
        for name in contracts.__all__
        if name
        not in {
            "CanonicalizationError",
            "IdentityDefinitionError",
            "canonical_json_bytes",
            "domain_separated_sha256",
            "compute_identity",
        }
    ) + (
        "_PROHIBITED_PROFILE_NORMALIZATION_STEPS",
        "_ANALYST_INPUT_V2_SEMANTIC_METADATA",
        "_ANALYST_RESPONSE_V2_SEMANTIC_METADATA",
        "_RESPONSE_CAPTURE_V2_SEMANTIC_METADATA",
        "_SEMANTIC_CONTRACT_RECORDS",
        "_CONTRACT_CATALOG_PAYLOAD",
    )
    for name in metadata_names:
        assert values[name] == getattr(contracts, name)


def test_authenticated_contract_surface_is_shared_deeply_immutable_and_detached(
    rendered_generation: tuple[Path, str, Path],
) -> None:
    verified = _verify(rendered_generation)
    authenticated = verified.authenticated_contract_surface
    snapshot = _snapshot_from_verified(verified)
    first_package = _package_from_snapshot(snapshot)
    first_render = _render_from_package(first_package)

    assert authenticated is snapshot.authenticated_contract_surface
    assert verified.contract_surface is authenticated.runtime_surface
    assert snapshot.contract_surface is authenticated.runtime_surface
    assert isinstance(authenticated.complete_surface, MappingProxyType)
    assert isinstance(authenticated.runtime_surface, MappingProxyType)
    with pytest.raises(TypeError):
        authenticated.complete_surface["contract_module_sha256"] = "0" * 64
    with pytest.raises(TypeError):
        authenticated.complete_surface["grounding_metadata"][
            "SOURCE_LOCATOR_TYPES"
        ] = ("forged",)

    detached = adapter._deep_thaw(authenticated.complete_surface)
    detached["grounding_metadata"]["SOURCE_LOCATOR_TYPES"] = ["forged"]
    detached["schema_identity_sha256_by_version"][
        "weekly_shadow_01_analyst_input_v2"
    ] = "0" * 64

    second_package = _package_from_snapshot(snapshot)
    second_render = _render_from_package(second_package)
    assert authenticated.complete_surface["grounding_metadata"][
        "SOURCE_LOCATOR_TYPES"
    ] == ("active_anchor_by_id", "availability_status", "manifest_diagnostic")
    assert second_package.canonical_json_bytes == first_package.canonical_json_bytes
    assert second_render.prompt_bytes == first_render.prompt_bytes
    assert (
        second_render.prompt_render_identity_sha256
        == first_render.prompt_render_identity_sha256
    )


def test_mutable_duplicate_expected_semantic_mapping_cannot_redefine_authenticity(
    rendered_generation: tuple[Path, str, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    changed = dict(adapter._EXPECTED_SEMANTIC_IDENTITIES)
    changed["weekly_shadow_01_analyst_input_v2"] = "0" * 64
    monkeypatch.setattr(
        adapter, "_EXPECTED_SEMANTIC_IDENTITIES", MappingProxyType(changed)
    )
    _expect_contract_surface_rejection(rendered_generation)


def test_raw_schema_byte_drift_fails_even_when_parsed_schema_identity_is_unchanged(
    rendered_generation: tuple[Path, str, Path],
) -> None:
    root, _, _ = rendered_generation
    path = root / "schemas/weekly_shadow_01_analyst_input.schema.json"
    original_schema = json.loads(path.read_text(encoding="utf-8"))
    path.write_bytes(path.read_bytes() + b" \n")
    assert json.loads(path.read_text(encoding="utf-8")) == original_schema
    _expect_contract_surface_rejection(rendered_generation)


def test_exported_schema_identity_constant_drift_fails_closed(
    rendered_generation: tuple[Path, str, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    def mutate(values: dict[str, object]) -> None:
        changed = dict(values["SCHEMA_IDENTITY_SHA256_BY_VERSION"])
        changed["weekly_shadow_01_analyst_input_v2"] = "0" * 64
        values["SCHEMA_IDENTITY_SHA256_BY_VERSION"] = changed

    _tamper_extracted_contract_values(monkeypatch, mutate)
    _expect_contract_surface_rejection(rendered_generation)


def test_semantic_metadata_and_exported_identity_drift_fail_independently(
    rendered_generation: tuple[Path, str, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    def mutate_metadata(values: dict[str, object]) -> None:
        changed = adapter._detach_contract_json(
            values["_ANALYST_INPUT_V2_SEMANTIC_METADATA"]
        )
        changed["adapter_id"] = "forged_adapter"
        values["_ANALYST_INPUT_V2_SEMANTIC_METADATA"] = changed

    with monkeypatch.context() as patch:
        _tamper_extracted_contract_values(patch, mutate_metadata)
        _expect_contract_surface_rejection(rendered_generation)

    def mutate_identity(values: dict[str, object]) -> None:
        changed = dict(values["SEMANTIC_CONTRACT_IDENTITY_SHA256_BY_VERSION"])
        changed["weekly_shadow_01_analyst_input_v2"] = "0" * 64
        values["SEMANTIC_CONTRACT_IDENTITY_SHA256_BY_VERSION"] = changed

    with monkeypatch.context() as patch:
        _tamper_extracted_contract_values(patch, mutate_identity)
        _expect_contract_surface_rejection(rendered_generation)


def test_catalog_metadata_and_exported_identity_drift_fail_independently(
    rendered_generation: tuple[Path, str, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    def mutate_payload(values: dict[str, object]) -> None:
        changed = adapter._detach_contract_json(values["_CONTRACT_CATALOG_PAYLOAD"])
        changed["catalog_version"] = "forged_catalog"
        values["_CONTRACT_CATALOG_PAYLOAD"] = changed

    with monkeypatch.context() as patch:
        _tamper_extracted_contract_values(patch, mutate_payload)
        _expect_contract_surface_rejection(rendered_generation)

    with monkeypatch.context() as patch:
        _tamper_extracted_contract_values(
            patch,
            lambda values: values.__setitem__(
                "CONTRACT_CATALOG_IDENTITY_SHA256", "0" * 64
            ),
        )
        _expect_contract_surface_rejection(rendered_generation)


@pytest.mark.parametrize(
    "metadata_name,changed_value",
    [
        (
            "RESOURCE_BOUND_PROFILE",
            MappingProxyType(
                {
                    **dict(contracts.RESOURCE_BOUND_PROFILE),
                    "analyst_input_max_bytes": 524_289,
                }
            ),
        ),
        (
            "NEGATIVE_AUTHORITY_PROFILE",
            MappingProxyType(
                {**dict(contracts.NEGATIVE_AUTHORITY_PROFILE), "authority_effect": "forged"}
            ),
        ),
        ("RUN_STATUS_VALUES", contracts.RUN_STATUS_VALUES + ("FORGED",)),
    ],
    ids=("resource-bound-profile", "negative-authority-profile", "vocabulary"),
)
def test_profile_and_vocabulary_metadata_drift_fail_closed(
    rendered_generation: tuple[Path, str, Path],
    monkeypatch: pytest.MonkeyPatch,
    metadata_name: str,
    changed_value: object,
) -> None:
    _tamper_extracted_contract_values(
        monkeypatch,
        lambda values: values.__setitem__(metadata_name, changed_value),
    )
    _expect_contract_surface_rejection(rendered_generation)


def test_prompt_bytes_and_exported_prompt_identity_drift_fail_independently(
    rendered_generation: tuple[Path, str, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    with monkeypatch.context() as patch:
        _tamper_extracted_contract_values(
            patch,
            lambda values: values.__setitem__(
                "PROMPT_TEMPLATE_BYTES",
                values["PROMPT_TEMPLATE_BYTES"] + b"drift\n",
            ),
        )
        _expect_contract_surface_rejection(rendered_generation)

    with monkeypatch.context() as patch:
        _tamper_extracted_contract_values(
            patch,
            lambda values: values.__setitem__(
                "PROMPT_TEMPLATE_IDENTITY_SHA256", "0" * 64
            ),
        )
        _expect_contract_surface_rejection(rendered_generation)


@pytest.mark.parametrize(
    "metadata_name,changed_value",
    [
        (
            "SOURCE_LOCATOR_TYPES",
            contracts.SOURCE_LOCATOR_TYPES + ("forged_locator",),
        ),
        (
            "EVIDENCE_RECORD_CANONICAL_ORDERING",
            MappingProxyType(
                {
                    **dict(contracts.EVIDENCE_RECORD_CANONICAL_ORDERING),
                    "direction": "descending",
                }
            ),
        ),
        (
            "LOGICAL_LOCATOR_UNIQUENESS_RULES",
            contracts.LOGICAL_LOCATOR_UNIQUENESS_RULES + ("allow_duplicate",),
        ),
        (
            "WS01B_RUNTIME_DEFERRED_RESOURCE_BOUND_RESPONSIBILITIES",
            contracts.WS01B_RUNTIME_DEFERRED_RESOURCE_BOUND_RESPONSIBILITIES
            + ("omit_package_byte_bound",),
        ),
    ],
    ids=("locator", "ordering", "uniqueness", "runtime-bound-ownership"),
)
def test_grounding_contract_metadata_drift_fails_closed(
    rendered_generation: tuple[Path, str, Path],
    monkeypatch: pytest.MonkeyPatch,
    metadata_name: str,
    changed_value: object,
) -> None:
    _tamper_extracted_contract_values(
        monkeypatch,
        lambda values: values.__setitem__(metadata_name, changed_value),
    )
    _expect_contract_surface_rejection(rendered_generation)


def test_identity_domain_drift_fails_closed(
    rendered_generation: tuple[Path, str, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    def mutate(values: dict[str, object]) -> None:
        changed = dict(values["DOMAIN_SEPARATORS"])
        changed["contract_catalog"] = b"forged_contract_catalog_domain\0"
        values["DOMAIN_SEPARATORS"] = changed

    _tamper_extracted_contract_values(monkeypatch, mutate)
    _expect_contract_surface_rejection(rendered_generation)


def test_complete_surface_seal_is_function_local_and_computation_drift_fails(
    rendered_generation: tuple[Path, str, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    assert not hasattr(adapter, "_EXPECTED_AUTHENTICATED_CONTRACT_SURFACE_SEAL")
    monkeypatch.setattr(
        adapter, "_compute_authenticated_surface_seal", lambda _payload: "0" * 64
    )
    _expect_contract_surface_rejection(rendered_generation)


def test_source_generation_is_not_examined_before_surface_authentication_succeeds(
    rendered_generation: tuple[Path, str, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        adapter, "_compute_authenticated_surface_seal", lambda _payload: "0" * 64
    )
    monkeypatch.setattr(
        adapter,
        "_verify_generation_at",
        lambda **_kwargs: pytest.fail(
            "source generation must not be accepted before surface authentication"
        ),
    )
    _expect_contract_surface_rejection(rendered_generation)


def test_actual_schema_drift_fails_even_when_python_constants_are_unchanged(
    rendered_generation: tuple[Path, str, Path],
) -> None:
    root, generation_id, _ = rendered_generation
    path = root / "schemas/weekly_shadow_01_analyst_input.schema.json"
    schema = json.loads(path.read_text(encoding="utf-8"))
    schema["description"] += " drift"
    path.write_text(json.dumps(schema, indent=2) + "\n", encoding="utf-8")
    assert contracts.SCHEMA_IDENTITY_SHA256_BY_VERSION[
        "weekly_shadow_01_analyst_input_v2"
    ] == "41c6258b3d27b97554a785628ab3e990e0f1f89bbaad7d70a787dd230853f5f0"
    _assert_failure_result(
        adapter.verify_r2f_v2_generation(generation_id, repository_root=root),
        "WS01_BR_INTERNAL_INVARIANT_FAILURE",
    )


def test_contract_metadata_drift_and_additional_ws01_schema_fail_closed(
    rendered_generation: tuple[Path, str, Path],
) -> None:
    root, generation_id, _ = rendered_generation
    contract_path = (
        root
        / "src/investment_orchestrator/observability/weekly_shadow_01_contracts.py"
    )
    contract_path.write_bytes(contract_path.read_bytes() + b"# drift\n")
    _assert_failure_result(
        adapter.verify_r2f_v2_generation(generation_id, repository_root=root),
        "WS01_BR_INTERNAL_INVARIANT_FAILURE",
    )

    contract_path.write_bytes(
        (Path(__file__).parents[2]
         / "src/investment_orchestrator/observability/weekly_shadow_01_contracts.py").read_bytes()
    )
    _write(root / "schemas/weekly_shadow_01_unexpected.schema.json", b"{}\n")
    _assert_failure_result(
        adapter.verify_r2f_v2_generation(generation_id, repository_root=root),
        "WS01_BR_INTERNAL_INVARIANT_FAILURE",
    )


@pytest.mark.parametrize(
    "filename,mutation",
    [
        (
            "replacement_input_manifest.json",
            lambda value: value.__setitem__("compatibility_profile", "unsupported_v9"),
        ),
        (
            "replacement_input_manifest.json",
            lambda value: value.__setitem__("permission_effect", "trade"),
        ),
        (
            "replacement_input_manifest.json",
            lambda value: value.__setitem__("generated_at", "2026-07-11T00:00:00+00:00"),
        ),
        (
            "evidence_packet.json",
            lambda value: value.__setitem__("strategy_settings_hash", "0" * 64),
        ),
        (
            "render_generation_binding.json",
            lambda value: value.__setitem__("generation_id", "0" * 64),
        ),
    ],
)
def test_manifest_evidence_render_version_authority_and_time_drift_fail_closed(
    rendered_generation: tuple[Path, str, Path], filename: str, mutation
) -> None:
    root, generation_id, generation = rendered_generation
    _rewrite_json(generation / filename, mutation)
    result = adapter.verify_r2f_v2_generation(generation_id, repository_root=root)
    assert result.ok is False
    assert result.reason_code in adapter._BLOCKING_REASON_CODES


def test_duplicate_json_keys_are_rejected_before_source_acceptance(
    rendered_generation: tuple[Path, str, Path],
) -> None:
    root, generation_id, generation = rendered_generation
    path = generation / "replacement_input_manifest.json"
    raw = path.read_bytes()
    path.write_bytes(raw.replace(b'{\n  "active_registry"', b'{\n  "schema_version": "duplicate",\n  "active_registry"', 1))
    _assert_failure_result(
        adapter.verify_r2f_v2_generation(generation_id, repository_root=root),
        "WS01_BR_SOURCE_GENERATION_INVALID",
    )


def test_prompt_and_manifest_recorded_hash_mismatches_fail_closed(
    rendered_generation: tuple[Path, str, Path],
) -> None:
    root, generation_id, generation = rendered_generation
    prompt = generation / "analyst_memo_prompt.txt"
    prompt.write_bytes(prompt.read_bytes() + b"changed\n")
    _assert_failure_result(
        adapter.verify_r2f_v2_generation(generation_id, repository_root=root),
        "WS01_BR_SOURCE_BINDING_MISMATCH",
    )


def test_prompt_verification_is_not_a_circular_recorded_hash_check(
    rendered_generation: tuple[Path, str, Path],
) -> None:
    root, _, generation = rendered_generation
    manifest = json.loads(
        (generation / "replacement_input_manifest.json").read_text(encoding="utf-8")
    )
    evidence = json.loads(
        (generation / "evidence_packet.json").read_text(encoding="utf-8")
    )
    forged_prompt = b"self-consistent but not producer-rendered\n"
    manifest["prompt_contract"]["analyst_memo_prompt_file_sha256"] = (
        adapter._sha256(forged_prompt)
    )
    template = (root / "prompts/r2f_analyst_memo_content_v2.txt").read_bytes()
    with pytest.raises(adapter._SourceAdapterFailure) as caught:
        adapter._validate_prompt_contract(
            manifest,
            evidence=evidence,
            prompt_bytes=forged_prompt,
            template_bytes=template,
        )
    assert caught.value.code == "WS01_BR_SOURCE_BINDING_MISMATCH"


def test_r2f_prompt_template_contract_drift_fails_before_source_acceptance(
    rendered_generation: tuple[Path, str, Path],
) -> None:
    root, generation_id, _ = rendered_generation
    template = root / "prompts/r2f_analyst_memo_content_v2.txt"
    template.write_bytes(template.read_bytes() + b"drift\n")
    _assert_failure_result(
        adapter.verify_r2f_v2_generation(generation_id, repository_root=root),
        "WS01_BR_INTERNAL_INVARIANT_FAILURE",
    )


def test_duplicate_source_anchor_ids_and_availability_type_drift_fail_closed(
    rendered_generation: tuple[Path, str, Path],
) -> None:
    _, _, generation = rendered_generation
    evidence = json.loads(
        (generation / "evidence_packet.json").read_text(encoding="utf-8")
    )
    registry = evidence["active_anchor_registry"]
    registry["active_anchors"].append(json.loads(json.dumps(registry["active_anchors"][0])))
    with pytest.raises(adapter._SourceAdapterFailure) as duplicate:
        adapter._validate_active_registry(registry)
    assert duplicate.value.code == "WS01_BR_SOURCE_BINDING_MISMATCH"

    assert adapter._project_availability(
        {"available": False, "data_gap": None}
    ) == {"available": False, "data_gap": None}
    with pytest.raises(adapter._SourceAdapterFailure):
        adapter._project_availability({"available": 0, "data_gap": None})
    with pytest.raises(adapter._SourceAdapterFailure):
        adapter._project_availability({"available": False})


def test_aggregate_source_byte_bound_is_enforced_independently(
    rendered_generation: tuple[Path, str, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    root, generation_id, generation = rendered_generation
    consumed_total = sum(
        (generation / role).stat().st_size
        for role in contracts.CONSUMED_SOURCE_ARTIFACT_ROLES
    )
    profile = dict(adapter._RESOURCE_BOUND_PROFILE)
    profile["source_artifacts_total_max_bytes"] = consumed_total - 1
    monkeypatch.setattr(adapter, "_RESOURCE_BOUND_PROFILE", MappingProxyType(profile))
    monkeypatch.setattr(adapter, "_verify_contract_profiles", lambda: None)
    _assert_failure_result(
        adapter.verify_r2f_v2_generation(generation_id, repository_root=root),
        "WS01_BR_RESOURCE_BOUND_EXCEEDED",
    )


def test_verifier_never_invokes_or_repairs_with_the_r2f_producer(
    rendered_generation: tuple[Path, str, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        r2f,
        "replacement_render",
        lambda: pytest.fail("WS01b must not invoke the R2F producer"),
    )
    _verify(rendered_generation)


def test_snapshot_is_deeply_immutable_and_detached_from_caller_copies(
    rendered_generation: tuple[Path, str, Path],
) -> None:
    snapshot = _snapshot(rendered_generation)
    with pytest.raises(TypeError):
        snapshot.active_anchors[0]["anchor_id"] = "MUTATED"
    detached = dict(snapshot.active_anchors[0])
    detached["anchor_id"] = "MUTATED"
    assert snapshot.active_anchors[0]["anchor_id"] == "ANCHOR_FIXA"
    assert snapshot.snapshot_identity_sha256 == _snapshot(
        rendered_generation
    ).snapshot_identity_sha256


def test_empty_active_registry_projects_only_the_frozen_manifest_diagnostic(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "repo"
    _setup_repo(root, anchors=[])
    monkeypatch.setattr(r2f, "repo_root", lambda: root)
    monkeypatch.setattr(r2f, "_today", lambda: date(2026, 7, 12))
    result = r2f.replacement_render()
    snapshot = _expect_success(
        adapter.build_source_snapshot(
            result["generation_id"], repository_root=root
        )
    )
    assert snapshot.active_anchors == ()
    assert snapshot.representation_diagnostics == ("EMPTY_ACTIVE_REGISTRY",)


def test_adapter_public_results_are_immutable_code_only_and_unknown_codes_fail_closed() -> None:
    secret = "GENERATION-ID-SECRET-7A11"
    assert adapter._BLOCKING_REASON_CODES == frozenset(
        {
            "WS01_BR_SOURCE_GENERATION_INVALID",
            "WS01_BR_SOURCE_ARTIFACT_SET_MISMATCH",
            "WS01_BR_SOURCE_VERSION_UNSUPPORTED",
            "WS01_BR_SOURCE_READ_UNSTABLE",
            "WS01_BR_SOURCE_BINDING_MISMATCH",
            "WS01_BR_PACKAGE_CONSTRUCTION_FAILED",
            "WS01_BR_RESOURCE_BOUND_EXCEEDED",
            "WS01_BR_INTERNAL_INVARIANT_FAILURE",
        }
    )
    for reason_code in adapter._BLOCKING_REASON_CODES:
        _assert_failure_result(
            adapter._result_failure(reason_code),
            reason_code,
            secret,
        )
    _assert_failure_result(
        adapter.verify_r2f_v2_generation(secret),
        "WS01_BR_SOURCE_GENERATION_INVALID",
        secret,
    )
    _assert_failure_result(
        adapter._result_failure(secret),
        "WS01_BR_INTERNAL_INVARIANT_FAILURE",
        secret,
    )

    outer_secret = "OUTER-CONTEXT-SECRET-801C"
    try:
        raise ValueError(outer_secret)
    except ValueError:
        nested_result = adapter.verify_r2f_v2_generation(secret)
    _assert_failure_result(
        nested_result,
        "WS01_BR_SOURCE_GENERATION_INVALID",
        secret,
        outer_secret,
    )
    _assert_no_reachable_exception(nested_result)


def test_result_constructor_and_former_public_error_are_unavailable() -> None:
    result = adapter._result_failure("WS01_BR_SOURCE_GENERATION_INVALID")
    with pytest.raises(TypeError):
        type(result)()
    assert not hasattr(adapter, "WeeklyShadow01SourceAdapterError")


def test_source_adapter_public_signatures_use_only_primitive_selectors() -> None:
    for operation in (
        adapter.verify_r2f_v2_generation,
        adapter.build_source_snapshot,
    ):
        signature = inspect.signature(operation)
        assert tuple(signature.parameters) == ("generation_id", "repository_root")
        assert (
            signature.parameters["repository_root"].kind
            is inspect.Parameter.KEYWORD_ONLY
        )


def test_snapshot_public_boundary_rejects_former_intermediate_authority_inputs(
    rendered_generation: tuple[Path, str, Path],
) -> None:
    verified = _verify(rendered_generation)
    verified_result = adapter._result_success(verified)
    for legacy_value in (verified, verified_result):
        _assert_failure_result(
            adapter.build_source_snapshot(legacy_value),
            "WS01_BR_SOURCE_GENERATION_INVALID",
        )
    clone = object.__new__(adapter._VerifiedR2FGeneration)
    for name in adapter._VerifiedR2FGeneration.__slots__:
        object.__setattr__(clone, name, getattr(verified, name))
    assert type(clone) is adapter._VerifiedR2FGeneration
    _assert_failure_result(
        adapter.build_source_snapshot(clone),
        "WS01_BR_SOURCE_GENERATION_INVALID",
    )
    secret = "LEGACY-VERIFIED-KEYWORD-SECRET-908A"
    with pytest.raises(TypeError) as failure:
        adapter.build_source_snapshot(verified_result={secret: verified_result})
    assert secret not in str(failure.value)


def test_snapshot_public_boundary_runs_one_private_verify_and_projection(
    rendered_generation: tuple[Path, str, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root, generation_id, _ = rendered_generation
    counts = {"verify": 0, "snapshot": 0}
    original_verify = adapter._verify_r2f_v2_generation
    original_snapshot = adapter._build_source_snapshot

    def verify(*args: object, **kwargs: object) -> object:
        counts["verify"] += 1
        return original_verify(*args, **kwargs)

    def snapshot(*args: object, **kwargs: object) -> object:
        counts["snapshot"] += 1
        return original_snapshot(*args, **kwargs)

    monkeypatch.setattr(adapter, "_verify_r2f_v2_generation", verify)
    monkeypatch.setattr(adapter, "_build_source_snapshot", snapshot)
    result = adapter.build_source_snapshot(generation_id, repository_root=root)
    assert result.ok is True
    assert type(result.value) is adapter._VerifiedSourceSnapshot
    assert counts == {"verify": 1, "snapshot": 1}


def test_snapshot_public_boundary_propagates_source_instability_without_value(
    rendered_generation: tuple[Path, str, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root, generation_id, _ = rendered_generation

    def unstable(*_args: object, **_kwargs: object) -> None:
        raise adapter._SourceAdapterFailure("WS01_BR_SOURCE_READ_UNSTABLE")

    monkeypatch.setattr(adapter, "_verify_r2f_v2_generation", unstable)
    _assert_failure_result(
        adapter.build_source_snapshot(generation_id, repository_root=root),
        "WS01_BR_SOURCE_READ_UNSTABLE",
    )


@pytest.mark.parametrize(
    "former_name",
    ("SourceArtifactBinding", "VerifiedR2FGeneration", "VerifiedSourceSnapshot"),
)
def test_former_public_success_constructors_are_unavailable(former_name: str) -> None:
    assert not hasattr(adapter, former_name)


@pytest.mark.parametrize(
    "helper_name",
    (
        "_verify_ws01a2_contract_surface",
        "_read_stable_regular_file_snapshot_at",
        "_parse_json_object",
        "_verify_generation_at",
    ),
)
def test_verifier_unexpected_internal_failures_are_sanitized(
    rendered_generation: tuple[Path, str, Path],
    monkeypatch: pytest.MonkeyPatch,
    helper_name: str,
) -> None:
    secret = f"UNEXPECTED-VERIFY-SECRET-{helper_name}"

    def fail(*_args: object, **_kwargs: object) -> None:
        raise RuntimeError(secret)

    monkeypatch.setattr(adapter, helper_name, fail)
    result = _verify_result(rendered_generation)
    _assert_failure_result(
        result,
        "WS01_BR_INTERNAL_INVARIANT_FAILURE",
        secret,
        rendered_generation[0],
    )


def test_snapshot_unexpected_projection_failure_is_sanitized(
    rendered_generation: tuple[Path, str, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root, generation_id, _ = rendered_generation
    secret = "UNEXPECTED-PROJECTION-SECRET-93D2"

    def fail(_value: object) -> None:
        raise AssertionError(secret)

    monkeypatch.setattr(adapter, "_project_active_anchor", fail)
    result = adapter.build_source_snapshot(generation_id, repository_root=root)
    _assert_failure_result(
        result,
        "WS01_BR_INTERNAL_INVARIANT_FAILURE",
        secret,
        rendered_generation[0],
    )


def test_expected_os_json_inventory_and_contract_failures_leak_no_input(
    rendered_generation: tuple[Path, str, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root, generation_id, generation = rendered_generation
    os_secret = "OS-READ-SECRET-E10A"

    def fail_read(_descriptor: int, _count: int) -> bytes:
        raise OSError(os_secret)

    monkeypatch.setattr(adapter._os, "read", fail_read)
    _assert_failure_result(
        adapter.verify_r2f_v2_generation(generation_id, repository_root=root),
        "WS01_BR_SOURCE_READ_UNSTABLE",
        os_secret,
        root,
    )
    monkeypatch.undo()

    json_secret = "MALFORMED-JSON-SECRET-1BC4"
    manifest_path = generation / "replacement_input_manifest.json"
    manifest_path.write_bytes(b'{"marker":"' + json_secret.encode("ascii") + b'",')
    _assert_failure_result(
        adapter.verify_r2f_v2_generation(generation_id, repository_root=root),
        "WS01_BR_SOURCE_GENERATION_INVALID",
        json_secret,
        manifest_path,
        root,
    )


@pytest.mark.parametrize("control_flow", (KeyboardInterrupt, SystemExit, GeneratorExit))
@pytest.mark.parametrize("operation", ("verify", "snapshot"))
def test_adapter_public_boundary_does_not_convert_control_flow_exceptions(
    rendered_generation: tuple[Path, str, Path],
    monkeypatch: pytest.MonkeyPatch,
    control_flow: type[BaseException],
    operation: str,
) -> None:
    secret = f"CONTROL-FLOW-{control_flow.__name__}"

    def stop(*_args: object, **_kwargs: object) -> None:
        raise control_flow(secret)

    monkeypatch.setattr(adapter, "_verify_ws01a2_contract_surface", stop)
    root, generation_id, _ = rendered_generation
    with pytest.raises(control_flow, match=secret):
        if operation == "verify":
            adapter.verify_r2f_v2_generation(generation_id, repository_root=root)
        else:
            adapter.build_source_snapshot(generation_id, repository_root=root)


def test_adapter_narrow_private_reason_mapping_has_no_exception_chain(
    rendered_generation: tuple[Path, str, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    secret = "PRIVATE-DIAGNOSTIC-SECRET-2D80"

    def fail(*_args: object, **_kwargs: object) -> None:
        try:
            raise ValueError(secret)
        except ValueError:
            adapter._raise("WS01_BR_SOURCE_BINDING_MISMATCH")

    monkeypatch.setattr(adapter, "_verify_ws01a2_contract_surface", fail)
    _assert_failure_result(
        _verify_result(rendered_generation),
        "WS01_BR_SOURCE_BINDING_MISMATCH",
        secret,
        rendered_generation[0],
    )


def test_actual_contract_surface_failure_leaks_no_contract_or_fixture_detail(
    rendered_generation: tuple[Path, str, Path],
) -> None:
    root, generation_id, _ = rendered_generation
    secret = "CONTRACT-SURFACE-SECRET-C102"
    contract_path = (
        root
        / "src/investment_orchestrator/observability/weekly_shadow_01_contracts.py"
    )
    contract_path.write_bytes(contract_path.read_bytes() + f"\n# {secret}\n".encode())
    _assert_failure_result(
        adapter.verify_r2f_v2_generation(generation_id, repository_root=root),
        "WS01_BR_INTERNAL_INVARIANT_FAILURE",
        secret,
        contract_path,
        generation_id,
        root,
    )


def test_public_namespace_is_exact_and_contains_no_mutable_registry() -> None:
    public = {name for name in vars(adapter) if not name.startswith("_")}
    assert public == set(adapter.__all__)
    assert adapter.__all__ == (
        "verify_r2f_v2_generation",
        "build_source_snapshot",
    )
    assert isinstance(adapter._INPUT_PATHS, MappingProxyType)
    assert isinstance(adapter._SOURCE_VERSIONS, MappingProxyType)
