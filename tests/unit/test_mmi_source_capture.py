from __future__ import annotations

import copy
from dataclasses import replace
import hashlib
import inspect
import os
from pathlib import Path
import stat
from types import SimpleNamespace

import pytest

from investment_orchestrator.common import schema_validation
from investment_orchestrator.common.schema_validation import (
    validate_artifact_schema,
)
from investment_orchestrator.mmi.canonical import (
    MMI_SOURCE_RECORD_IDENTITY_DOMAIN,
    record_identity_sha256,
)
from investment_orchestrator.mmi.contracts import (
    MMI_SOURCE_CATALOG,
    MmiCapturedSource,
    MmiProjectionResultCategory,
    MmiSourceRole,
    _mmi_captured_source_provenance_is_valid,
)
from investment_orchestrator.mmi import source_capture
from investment_orchestrator.mmi.source_capture import (
    _capture_mmi_source_at_root,
    capture_current_mmi_source,
)


def _install_source(root: Path, raw: bytes = b"as_of: '2026-07-25'\n") -> Path:
    path = root / "inputs" / "current" / "strategy_settings.yaml"
    path.parent.mkdir(parents=True)
    path.write_bytes(raw)
    return path


def _capture(root: Path, raw: bytes):
    return _capture_mmi_source_at_root(
        root,
        role=MmiSourceRole.STRATEGY_SETTINGS,
        expected_source_sha256=hashlib.sha256(raw).hexdigest(),
    )


def _install_checkout(root: Path, raw: bytes) -> Path:
    (root / "pyproject.toml").parent.mkdir(parents=True, exist_ok=True)
    (root / "pyproject.toml").write_bytes(
        b'[project]\nname = "investment-orchestrator"\n'
    )
    package_init = root / "src/investment_orchestrator/__init__.py"
    package_init.parent.mkdir(parents=True, exist_ok=True)
    package_init.write_bytes(
        b'"""investment_orchestrator package."""\n'
    )
    module = (
        root
        / "src/investment_orchestrator/mmi/source_capture.py"
    )
    module.parent.mkdir(parents=True, exist_ok=True)
    module.write_bytes(
        b"_PRODUCTION_MODULE_SUFFIX = ()\n"
        b"def capture_current_mmi_source(\n"
        b"    role, *, expected_source_sha256\n"
        b"):\n"
        b"    raise NotImplementedError\n"
    )
    _install_source(root, raw)
    return module


def test_source_catalog_is_exact_closed_and_code_owned() -> None:
    assert tuple(MMI_SOURCE_CATALOG) == (
        MmiSourceRole.STRATEGY_SETTINGS,
        MmiSourceRole.PORTFOLIO_SNAPSHOT,
    )
    strategy = MMI_SOURCE_CATALOG[MmiSourceRole.STRATEGY_SETTINGS]
    assert strategy.source_id == "MMI_STRATEGY_SETTINGS"
    assert strategy.path_components == (
        "inputs",
        "current",
        "strategy_settings.yaml",
    )
    assert str(strategy.repository_relative_locator) == (
        "inputs/current/strategy_settings.yaml"
    )
    assert strategy.maximum_bytes == 262_144
    portfolio = MMI_SOURCE_CATALOG[MmiSourceRole.PORTFOLIO_SNAPSHOT]
    assert portfolio.source_id == "MMI_PORTFOLIO_SNAPSHOT"
    assert portfolio.path_components == (
        "inputs",
        "current",
        "portfolio_snapshot.txt",
    )
    assert portfolio.maximum_bytes == 1_048_576
    with pytest.raises(TypeError):
        MMI_SOURCE_CATALOG[MmiSourceRole.STRATEGY_SETTINGS] = portfolio


def test_p1a_capture_surface_has_no_root_path_source_id_or_bound_parameters() -> None:
    signature = inspect.signature(capture_current_mmi_source)
    assert tuple(signature.parameters) == (
        "role",
        "expected_source_sha256",
    )
    assert signature.parameters["expected_source_sha256"].kind is (
        inspect.Parameter.KEYWORD_ONLY
    )
    with pytest.raises(TypeError):
        capture_current_mmi_source(
            MmiSourceRole.STRATEGY_SETTINGS,
            expected_source_sha256="0" * 64,
            relative_path="../secret",  # type: ignore[call-arg]
        )


def test_portfolio_role_is_reserved_but_not_capturable_in_p1a(tmp_path: Path) -> None:
    result = _capture_mmi_source_at_root(
        tmp_path,
        role=MmiSourceRole.PORTFOLIO_SNAPSHOT,
        expected_source_sha256="0" * 64,
    )
    assert result.status is MmiProjectionResultCategory.PROJECTION_BLOCKED
    assert result.reason_codes == ("MMI_SOURCE_ROLE_NOT_AVAILABLE_IN_P1A",)
    assert result.source is None
    assert result.authority_effect == "NONE"


@pytest.mark.parametrize(
    ("expected_hash", "code"),
    [
        (None, "MMI_SOURCE_EXPECTED_SHA256_REQUIRED"),
        ("", "MMI_SOURCE_EXPECTED_SHA256_REQUIRED"),
        ("0" * 63, "MMI_SOURCE_EXPECTED_SHA256_INVALID"),
        ("A" * 64, "MMI_SOURCE_EXPECTED_SHA256_INVALID"),
        ("g" * 64, "MMI_SOURCE_EXPECTED_SHA256_INVALID"),
        (123, "MMI_SOURCE_EXPECTED_SHA256_INVALID"),
    ],
)
def test_expected_hash_is_mandatory_and_strictly_lowercase(
    tmp_path: Path,
    expected_hash: object,
    code: str,
) -> None:
    result = _capture_mmi_source_at_root(
        tmp_path,
        role=MmiSourceRole.STRATEGY_SETTINGS,
        expected_source_sha256=expected_hash,  # type: ignore[arg-type]
    )
    assert result.status is MmiProjectionResultCategory.PROJECTION_BLOCKED
    assert result.reason_codes == (code,)
    assert result.source is None


def test_exact_byte_capture_binds_content_without_origin_authentication(
    tmp_path: Path,
) -> None:
    raw = b"as_of: '2026-07-25'\nvalue: \xf0\x9f\x8c\x90\n"
    _install_source(tmp_path, raw)
    result = _capture(tmp_path, raw)
    assert result.status is MmiProjectionResultCategory.PROJECTION_VALID_COMPLETE
    assert result.reason_codes == ()
    assert result.authority_effect == "NONE"
    assert result.source is not None
    assert result.source.raw_bytes == raw
    record = dict(result.source.source_record)
    assert record == {
        "schema_version": "mmi_source_record_v1",
        "source_role": "STRATEGY_SETTINGS",
        "source_id": "MMI_STRATEGY_SETTINGS",
        "repository_relative_locator": (
            "inputs/current/strategy_settings.yaml"
        ),
        "maximum_bytes": 262_144,
        "observed_size_bytes": len(raw),
        "expected_sha256": hashlib.sha256(raw).hexdigest(),
        "observed_sha256": hashlib.sha256(raw).hexdigest(),
        "content_binding_status": "EXPECTED_SHA256_MATCHED",
        "operator_origin_authentication": "NOT_ESTABLISHED",
        "stable_read_status": "STABLE_BEFORE_AND_AFTER",
        "regular_file_status": "REGULAR_FILE",
        "authority_effect": "NONE",
        "source_record_identity_sha256": record[
            "source_record_identity_sha256"
        ],
    }
    validate_artifact_schema(
        record,
        schema_name="mmi_source_record_v1.schema.json",
    )
    assert record["source_record_identity_sha256"] == record_identity_sha256(
        record,
        identity_field="source_record_identity_sha256",
        domain=MMI_SOURCE_RECORD_IDENTITY_DOMAIN,
        maximum_bytes=8_192,
    )
    assert not any(
        str(tmp_path) in str(value)
        for value in record.values()
    )
    assert _mmi_captured_source_provenance_is_valid(result.source)
    assert copy.copy(result.source) is result.source
    assert copy.deepcopy(result.source) is result.source
    with pytest.raises(TypeError):
        replace(result.source, raw_bytes=raw + b"changed")


def test_captured_source_direct_construction_and_forged_seal_are_rejected(
    tmp_path: Path,
) -> None:
    raw = b"as_of: '2026-07-25'\n"
    _install_source(tmp_path, raw)
    captured = _capture(tmp_path, raw)
    assert captured.source is not None
    with pytest.raises(TypeError):
        MmiCapturedSource(
            role=MmiSourceRole.STRATEGY_SETTINGS,
            raw_bytes=raw,
            source_record=captured.source.source_record,
        )

    forged = object.__new__(MmiCapturedSource)
    object.__setattr__(
        forged,
        "role",
        MmiSourceRole.STRATEGY_SETTINGS,
    )
    object.__setattr__(forged, "raw_bytes", raw)
    object.__setattr__(
        forged,
        "source_record",
        captured.source.source_record,
    )
    object.__setattr__(forged, "_provenance_seal", b"\x00" * 32)
    assert not _mmi_captured_source_provenance_is_valid(forged)

    reconstructed = object.__new__(MmiCapturedSource)
    object.__setattr__(
        reconstructed,
        "role",
        captured.source.role,
    )
    object.__setattr__(
        reconstructed,
        "raw_bytes",
        captured.source.raw_bytes,
    )
    object.__setattr__(
        reconstructed,
        "source_record",
        captured.source.source_record,
    )
    object.__setattr__(
        reconstructed,
        "_provenance_token",
        captured.source._provenance_token,
    )
    object.__setattr__(
        reconstructed,
        "_provenance_seal",
        captured.source._provenance_seal,
    )
    assert not _mmi_captured_source_provenance_is_valid(reconstructed)


def test_observed_hash_mismatch_produces_no_source_record(tmp_path: Path) -> None:
    raw = b"value: exact\n"
    _install_source(tmp_path, raw)
    result = _capture_mmi_source_at_root(
        tmp_path,
        role=MmiSourceRole.STRATEGY_SETTINGS,
        expected_source_sha256="0" * 64,
    )
    assert result.status is MmiProjectionResultCategory.PROJECTION_BLOCKED
    assert result.reason_codes == ("MMI_SOURCE_EXPECTED_SHA256_MISMATCH",)
    assert result.source is None


def test_missing_and_nonregular_sources_fail_closed(tmp_path: Path) -> None:
    missing = _capture_mmi_source_at_root(
        tmp_path,
        role=MmiSourceRole.STRATEGY_SETTINGS,
        expected_source_sha256="0" * 64,
    )
    assert missing.reason_codes == ("MMI_SOURCE_MISSING",)

    leaf = tmp_path / "inputs/current/strategy_settings.yaml"
    leaf.mkdir(parents=True)
    nonregular = _capture_mmi_source_at_root(
        tmp_path,
        role=MmiSourceRole.STRATEGY_SETTINGS,
        expected_source_sha256="0" * 64,
    )
    assert nonregular.reason_codes == ("MMI_SOURCE_NOT_REGULAR_FILE",)


@pytest.mark.parametrize("symlink_component", ["inputs", "current", "leaf"])
def test_symlink_at_every_source_component_is_rejected(
    tmp_path: Path,
    symlink_component: str,
) -> None:
    external = tmp_path / "external"
    target = external / "current" / "strategy_settings.yaml"
    target.parent.mkdir(parents=True)
    raw = b"value: external\n"
    target.write_bytes(raw)
    if symlink_component == "inputs":
        (tmp_path / "inputs").symlink_to(external, target_is_directory=True)
    elif symlink_component == "current":
        (tmp_path / "inputs").mkdir()
        (tmp_path / "inputs/current").symlink_to(
            external / "current",
            target_is_directory=True,
        )
    else:
        (tmp_path / "inputs/current").mkdir(parents=True)
        (tmp_path / "inputs/current/strategy_settings.yaml").symlink_to(
            target
        )
    result = _capture_mmi_source_at_root(
        tmp_path,
        role=MmiSourceRole.STRATEGY_SETTINGS,
        expected_source_sha256=hashlib.sha256(raw).hexdigest(),
    )
    assert result.status is MmiProjectionResultCategory.PROJECTION_BLOCKED
    assert result.reason_codes == ("MMI_SOURCE_SYMLINK_REJECTED",)
    assert result.source is None


def test_source_size_is_checked_before_any_unbounded_read(tmp_path: Path) -> None:
    raw = b"x" * 262_145
    _install_source(tmp_path, raw)
    result = _capture(tmp_path, raw)
    assert result.reason_codes == ("MMI_SOURCE_OVERSIZED",)
    assert result.source is None


def test_short_and_overlong_reads_fail_closed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    raw = b"abc"
    _install_source(tmp_path, raw)

    monkeypatch.setattr(source_capture.os, "read", lambda _fd, _count: b"")
    short = _capture(tmp_path, raw)
    assert short.reason_codes == ("MMI_SOURCE_SHORT_READ",)
    monkeypatch.undo()

    monkeypatch.setattr(
        source_capture.os,
        "read",
        lambda _fd, count: b"x" * (count + 1),
    )
    overlong = _capture(tmp_path, raw)
    assert overlong.reason_codes == ("MMI_SOURCE_OVERLONG_READ",)


def test_before_after_witness_change_is_blocking(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    raw = b"stable: expected\n"
    _install_source(tmp_path, raw)
    original_fstat = source_capture.os.fstat
    regular_calls = 0

    def drifting_fstat(fd: int):
        nonlocal regular_calls
        value = original_fstat(fd)
        if not stat.S_ISREG(value.st_mode):
            return value
        regular_calls += 1
        if regular_calls < 2:
            return value
        return SimpleNamespace(
            st_dev=value.st_dev,
            st_ino=value.st_ino,
            st_mode=value.st_mode,
            st_size=value.st_size,
            st_mtime_ns=value.st_mtime_ns + 1,
            st_ctime_ns=value.st_ctime_ns,
        )

    monkeypatch.setattr(source_capture.os, "fstat", drifting_fstat)
    result = _capture(tmp_path, raw)
    assert result.reason_codes == ("MMI_SOURCE_UNSTABLE",)
    assert result.source is None


@pytest.mark.parametrize(
    ("component", "expected_code"),
    [
        ("current", "MMI_SOURCE_PATH_UNSTABLE"),
        ("strategy_settings.yaml", "MMI_SOURCE_PATH_UNSTABLE"),
    ],
)
def test_replacement_between_entry_stat_and_open_is_blocking(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    component: str,
    expected_code: str,
) -> None:
    raw = b"stable: expected\n"
    leaf = _install_source(tmp_path, raw)
    original_open = source_capture._open_relative
    replaced = False

    def replacing_open(
        name: str,
        *,
        directory_fd: int,
        flags: int,
        unstable_code: str,
    ) -> int:
        nonlocal replaced
        if name == component and not replaced:
            replaced = True
            if component == "current":
                current = tmp_path / "inputs/current"
                current.rename(tmp_path / "inputs/current.detached")
                _install_source(tmp_path, raw)
            else:
                leaf.rename(leaf.with_suffix(".detached"))
                leaf.write_bytes(raw)
        return original_open(
            name,
            directory_fd=directory_fd,
            flags=flags,
            unstable_code=unstable_code,
        )

    monkeypatch.setattr(
        source_capture,
        "_open_relative",
        replacing_open,
    )
    result = _capture(tmp_path, raw)
    assert replaced
    assert result.status is MmiProjectionResultCategory.PROJECTION_BLOCKED
    assert result.reason_codes == (expected_code,)
    assert result.source is None


def test_every_opened_descriptor_is_closed_on_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    raw = b"close: all\n"
    _install_source(tmp_path, raw)
    opened: list[int] = []
    closed: list[int] = []
    original_root = source_capture._open_root
    original_relative = source_capture._open_relative
    original_close = source_capture.os.close

    def tracked_root(repository_root: Path, flags: int) -> int:
        fd = original_root(repository_root, flags)
        opened.append(fd)
        return fd

    def tracked_relative(
        name: str,
        *,
        directory_fd: int,
        flags: int,
        unstable_code: str,
    ) -> int:
        fd = original_relative(
            name,
            directory_fd=directory_fd,
            flags=flags,
            unstable_code=unstable_code,
        )
        opened.append(fd)
        return fd

    def tracked_close(fd: int) -> None:
        closed.append(fd)
        original_close(fd)

    monkeypatch.setattr(source_capture, "_open_root", tracked_root)
    monkeypatch.setattr(source_capture, "_open_relative", tracked_relative)
    monkeypatch.setattr(source_capture.os, "close", tracked_close)
    result = _capture_mmi_source_at_root(
        tmp_path,
        role=MmiSourceRole.STRATEGY_SETTINGS,
        expected_source_sha256="0" * 64,
    )
    assert result.reason_codes == ("MMI_SOURCE_EXPECTED_SHA256_MISMATCH",)
    assert sorted(opened) == sorted(closed)
    assert len(opened) == (
        1
        + len(tmp_path.parts[1:])
        + len(
            MMI_SOURCE_CATALOG[
                MmiSourceRole.STRATEGY_SETTINGS
            ].path_components
        )
    )


def test_schema_failure_precedes_final_verification_and_closes_all_fds(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    raw = b"schema: tentative\n"
    _install_source(tmp_path, raw)
    original_root = source_capture._open_root
    original_relative = source_capture._open_relative
    original_close = source_capture.os.close
    opened: list[int] = []
    closed: list[int] = []
    verify_calls = 0

    def fail_schema(
        _payload: object,
        *,
        schema_name: str,
    ) -> None:
        assert schema_name == "mmi_source_record_v1.schema.json"
        raise ValueError("deterministic cold-cache schema failure")

    def fail_if_verified(
        _root_anchor: source_capture._RootAnchor,
        _opened_components: list[source_capture._OpenedComponent],
        *,
        source_content_stable: bool,
    ) -> None:
        nonlocal verify_calls
        verify_calls += 1
        raise AssertionError(source_content_stable)

    def tracked_root(repository_root: Path, flags: int) -> int:
        descriptor = original_root(repository_root, flags)
        opened.append(descriptor)
        return descriptor

    def tracked_relative(
        name: str,
        *,
        directory_fd: int,
        flags: int,
        unstable_code: str,
    ) -> int:
        descriptor = original_relative(
            name,
            directory_fd=directory_fd,
            flags=flags,
            unstable_code=unstable_code,
        )
        opened.append(descriptor)
        return descriptor

    def tracked_close(file_fd: int) -> None:
        closed.append(file_fd)
        original_close(file_fd)

    monkeypatch.setattr(
        source_capture,
        "validate_artifact_schema",
        fail_schema,
    )
    monkeypatch.setattr(
        source_capture,
        "_verify_complete_opened_path",
        fail_if_verified,
    )
    monkeypatch.setattr(source_capture, "_open_root", tracked_root)
    monkeypatch.setattr(source_capture, "_open_relative", tracked_relative)
    monkeypatch.setattr(source_capture.os, "close", tracked_close)

    result = _capture(tmp_path, raw)

    assert result.status is (
        MmiProjectionResultCategory.PROJECTION_CONTRACT_FAILURE
    )
    assert result.reason_codes == ("MMI_SOURCE_RECORD_CONTRACT_FAILURE",)
    assert result.source is None
    assert result.authority_effect == "NONE"
    assert verify_calls == 0
    assert sorted(opened) == sorted(closed)
    assert len(closed) == len(set(closed))


def test_production_source_checkout_root_is_authenticated_without_cwd(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    raw = b"as_of: '2026-07-25'\n"
    checkout = tmp_path / "checkout"
    module = _install_checkout(checkout, raw)
    unrelated_cwd = tmp_path / "unrelated"
    unrelated_cwd.mkdir()
    monkeypatch.chdir(unrelated_cwd)
    monkeypatch.setenv("MMI_REPOSITORY_ROOT", str(tmp_path / "wrong"))
    monkeypatch.setenv("PWD", str(tmp_path / "wrong-cwd"))

    result = source_capture._capture_current_mmi_source_from_module_path(
        module,
        MmiSourceRole.STRATEGY_SETTINGS,
        expected_source_sha256=hashlib.sha256(raw).hexdigest(),
    )
    assert result.status is MmiProjectionResultCategory.PROJECTION_VALID_COMPLETE
    assert result.source is not None
    assert result.source.raw_bytes == raw


def test_public_capture_uses_import_time_module_locator(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    raw = Path("inputs/current/strategy_settings.yaml").read_bytes()
    monkeypatch.setattr(
        source_capture,
        "__file__",
        "/tmp/other/src/investment_orchestrator/mmi/source_capture.py",
    )
    result = capture_current_mmi_source(
        MmiSourceRole.STRATEGY_SETTINGS,
        expected_source_sha256=hashlib.sha256(raw).hexdigest(),
    )
    assert result.status is MmiProjectionResultCategory.PROJECTION_VALID_COMPLETE
    assert result.source is not None
    assert result.source.raw_bytes == raw


def test_production_root_rejects_symlinked_module_and_repository_ancestor(
    tmp_path: Path,
) -> None:
    raw = b"as_of: '2026-07-25'\n"
    checkout = tmp_path / "checkout"
    module = _install_checkout(checkout, raw)
    real_module = module.with_name("source_capture_real.py")
    module.rename(real_module)
    module.symlink_to(real_module.name)
    symlinked_module = (
        source_capture._capture_current_mmi_source_from_module_path(
            module,
            MmiSourceRole.STRATEGY_SETTINGS,
            expected_source_sha256=hashlib.sha256(raw).hexdigest(),
        )
    )
    assert symlinked_module.status is (
        MmiProjectionResultCategory.PROJECTION_BLOCKED
    )
    assert symlinked_module.reason_codes == (
        "MMI_SOURCE_SYMLINK_REJECTED",
    )

    real_checkout = tmp_path / "real-checkout"
    _install_checkout(real_checkout, raw)
    linked_checkout = tmp_path / "linked-checkout"
    linked_checkout.symlink_to(real_checkout, target_is_directory=True)
    linked_module = (
        linked_checkout
        / "src/investment_orchestrator/mmi/source_capture.py"
    )
    symlinked_ancestor = (
        source_capture._capture_current_mmi_source_from_module_path(
            linked_module,
            MmiSourceRole.STRATEGY_SETTINGS,
            expected_source_sha256=hashlib.sha256(raw).hexdigest(),
        )
    )
    assert symlinked_ancestor.status is (
        MmiProjectionResultCategory.PROJECTION_BLOCKED
    )
    assert symlinked_ancestor.reason_codes == (
        "MMI_SOURCE_SYMLINK_REJECTED",
    )


@pytest.mark.parametrize(
    "module_path",
    [
        Path("/opt/site-packages/investment_orchestrator/mmi/source_capture.py"),
        Path("/opt/project/src/investment_orchestrator/mmi/not_capture.py"),
        Path("src/investment_orchestrator/mmi/source_capture.py"),
    ],
)
def test_production_root_rejects_wrong_or_installed_layout(
    module_path: Path,
) -> None:
    result = source_capture._capture_current_mmi_source_from_module_path(
        module_path,
        MmiSourceRole.STRATEGY_SETTINGS,
        expected_source_sha256="0" * 64,
    )
    assert result.status is MmiProjectionResultCategory.PROJECTION_BLOCKED
    assert result.reason_codes == (
        "MMI_SOURCE_PRODUCTION_LAYOUT_UNSUPPORTED",
    )


def test_production_root_rejects_missing_and_replaced_markers(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    raw = b"as_of: '2026-07-25'\n"
    missing_checkout = tmp_path / "missing-marker"
    missing_module = _install_checkout(missing_checkout, raw)
    (missing_checkout / "pyproject.toml").unlink()
    missing = source_capture._capture_current_mmi_source_from_module_path(
        missing_module,
        MmiSourceRole.STRATEGY_SETTINGS,
        expected_source_sha256=hashlib.sha256(raw).hexdigest(),
    )
    assert missing.status is MmiProjectionResultCategory.PROJECTION_BLOCKED
    assert missing.reason_codes == (
        "MMI_SOURCE_REPOSITORY_MARKER_INVALID",
    )

    replaced_checkout = tmp_path / "replaced-marker"
    replaced_module = _install_checkout(replaced_checkout, raw)
    marker = replaced_checkout / "pyproject.toml"
    original_read = source_capture._read_exact_bounded
    calls = 0

    def replace_marker_after_read(
        file_fd: int,
        *,
        expected_size: int,
    ) -> bytes:
        nonlocal calls
        observed = original_read(file_fd, expected_size=expected_size)
        calls += 1
        if calls == 1:
            marker.rename(marker.with_suffix(".detached"))
            marker.write_bytes(observed)
        return observed

    monkeypatch.setattr(
        source_capture,
        "_read_exact_bounded",
        replace_marker_after_read,
    )
    replaced = (
        source_capture._capture_current_mmi_source_from_module_path(
            replaced_module,
            MmiSourceRole.STRATEGY_SETTINGS,
            expected_source_sha256=hashlib.sha256(raw).hexdigest(),
        )
    )
    assert replaced.status is MmiProjectionResultCategory.PROJECTION_BLOCKED
    assert replaced.reason_codes == ("MMI_SOURCE_PATH_UNSTABLE",)


@pytest.mark.parametrize(
    "replacement",
    (
        "copied-directory",
        "copied-ancestor",
        "symlink-detached-checkout",
        "symlink-other-valid-checkout",
    ),
)
def test_final_chain_verification_rejects_checkout_replacement(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    replacement: str,
) -> None:
    raw = b"as_of: '2026-07-25'\n"
    checkout_parent = (
        tmp_path / "workspace"
        if replacement == "copied-ancestor"
        else tmp_path
    )
    checkout = checkout_parent / "checkout"
    module = _install_checkout(checkout, raw)
    detached = (
        tmp_path / "workspace.detached"
        if replacement == "copied-ancestor"
        else tmp_path / "checkout.detached"
    )
    other = tmp_path / "other-valid-checkout"
    original_verify = source_capture._verify_complete_opened_path
    original_root = source_capture._open_root
    original_relative = source_capture._open_relative
    original_close = source_capture.os.close
    opened: list[int] = []
    closed: list[int] = []
    mutated = False

    def tracked_root(repository_root: Path, flags: int) -> int:
        descriptor = original_root(repository_root, flags)
        opened.append(descriptor)
        return descriptor

    def tracked_relative(
        name: str,
        *,
        directory_fd: int,
        flags: int,
        unstable_code: str,
    ) -> int:
        descriptor = original_relative(
            name,
            directory_fd=directory_fd,
            flags=flags,
            unstable_code=unstable_code,
        )
        opened.append(descriptor)
        return descriptor

    def tracked_close(file_fd: int) -> None:
        closed.append(file_fd)
        original_close(file_fd)

    def replace_then_verify(
        root_anchor: source_capture._RootAnchor,
        opened_components: list[source_capture._OpenedComponent],
        *,
        source_content_stable: bool,
    ) -> None:
        nonlocal mutated
        if replacement == "copied-ancestor":
            checkout_parent.rename(detached)
            _install_checkout(checkout, raw)
        elif replacement == "copied-directory":
            checkout.rename(detached)
            _install_checkout(checkout, raw)
        elif replacement == "symlink-detached-checkout":
            checkout.rename(detached)
            checkout.symlink_to(detached, target_is_directory=True)
        else:
            checkout.rename(detached)
            _install_checkout(other, raw)
            checkout.symlink_to(other, target_is_directory=True)
        mutated = True
        original_verify(
            root_anchor,
            opened_components,
            source_content_stable=source_content_stable,
        )

    monkeypatch.setattr(
        source_capture,
        "_verify_complete_opened_path",
        replace_then_verify,
    )
    monkeypatch.setattr(source_capture, "_open_root", tracked_root)
    monkeypatch.setattr(source_capture, "_open_relative", tracked_relative)
    monkeypatch.setattr(source_capture.os, "close", tracked_close)

    result = source_capture._capture_current_mmi_source_from_module_path(
        module,
        MmiSourceRole.STRATEGY_SETTINGS,
        expected_source_sha256=hashlib.sha256(raw).hexdigest(),
    )

    assert mutated
    assert result.status is MmiProjectionResultCategory.PROJECTION_BLOCKED
    assert result.reason_codes == ("MMI_SOURCE_PATH_UNSTABLE",)
    assert result.source is None
    assert sorted(opened) == sorted(closed)


@pytest.mark.parametrize("schema_cache_mode", ("cold", "warm", "disabled"))
def test_complete_chain_verification_is_final_path_operation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    schema_cache_mode: str,
) -> None:
    raw = b"as_of: '2026-07-25'\n"
    checkout = tmp_path / "checkout"
    module = _install_checkout(checkout, raw)
    original_available = (
        source_capture._required_filesystem_primitives_available
    )
    original_capture = source_capture._capture_fixed_source_bytes
    original_verify = source_capture._verify_complete_opened_path
    original_os_stat = source_capture.os.stat
    original_validate = source_capture.validate_artifact_schema
    original_cached_load = schema_validation.load_artifact_schema
    original_schema_path = schema_validation.schema_path
    original_path_resolve = Path.resolve
    original_path_open = Path.open
    original_path_stat = Path.stat
    original_path_exists = Path.exists
    capture_active = False
    final_verification_complete = False
    operations: list[tuple[str, bool]] = []

    assert original_available()
    original_cached_load.cache_clear()
    if schema_cache_mode == "warm":
        original_cached_load("mmi_source_record_v1.schema.json")
    selected_load = (
        original_cached_load.__wrapped__
        if schema_cache_mode == "disabled"
        else original_cached_load
    )

    def observe(name: str) -> None:
        if not capture_active:
            return
        operations.append((name, final_verification_complete))
        if final_verification_complete:
            raise AssertionError(
                f"{name} occurred after final chain verification"
            )

    def tracked_capture(*args: object, **kwargs: object):
        nonlocal capture_active
        capture_active = True
        try:
            return original_capture(*args, **kwargs)
        finally:
            capture_active = False

    def tracked_verify(
        root_anchor: source_capture._RootAnchor,
        opened_components: list[source_capture._OpenedComponent],
        *,
        source_content_stable: bool,
    ) -> None:
        nonlocal final_verification_complete
        original_verify(
            root_anchor,
            opened_components,
            source_content_stable=source_content_stable,
        )
        final_verification_complete = True

    def guarded_os_stat(
        path: object,
        *args: object,
        **kwargs: object,
    ):
        observe("os.stat")
        return original_os_stat(path, *args, **kwargs)

    def guarded_path_resolve(
        path: Path,
        *args: object,
        **kwargs: object,
    ):
        observe("Path.resolve")
        return original_path_resolve(path, *args, **kwargs)

    def guarded_path_open(
        path: Path,
        *args: object,
        **kwargs: object,
    ):
        observe("Path.open")
        return original_path_open(path, *args, **kwargs)

    def guarded_path_stat(
        path: Path,
        *args: object,
        **kwargs: object,
    ):
        observe("Path.stat")
        return original_path_stat(path, *args, **kwargs)

    def guarded_path_exists(path: Path) -> bool:
        observe("Path.exists")
        return original_path_exists(path)

    def guarded_schema_path(schema_name: str) -> Path:
        observe("schema_path")
        return original_schema_path(schema_name)

    def guarded_load(schema_name: str):
        observe("load_artifact_schema")
        return selected_load(schema_name)

    def guarded_validate(
        payload: object,
        *,
        schema_name: str,
    ) -> None:
        observe("validate_artifact_schema")
        original_validate(payload, schema_name=schema_name)

    monkeypatch.setattr(
        source_capture,
        "_required_filesystem_primitives_available",
        lambda: True,
    )
    monkeypatch.setattr(
        source_capture,
        "_capture_fixed_source_bytes",
        tracked_capture,
    )
    monkeypatch.setattr(
        source_capture,
        "_verify_complete_opened_path",
        tracked_verify,
    )
    monkeypatch.setattr(source_capture.os, "stat", guarded_os_stat)
    monkeypatch.setattr(Path, "resolve", guarded_path_resolve)
    monkeypatch.setattr(Path, "open", guarded_path_open)
    monkeypatch.setattr(Path, "stat", guarded_path_stat)
    monkeypatch.setattr(Path, "exists", guarded_path_exists)
    monkeypatch.setattr(
        schema_validation,
        "schema_path",
        guarded_schema_path,
    )
    monkeypatch.setattr(
        schema_validation,
        "load_artifact_schema",
        guarded_load,
    )
    monkeypatch.setattr(
        source_capture,
        "validate_artifact_schema",
        guarded_validate,
    )

    result = source_capture._capture_current_mmi_source_from_module_path(
        module,
        MmiSourceRole.STRATEGY_SETTINGS,
        expected_source_sha256=hashlib.sha256(raw).hexdigest(),
    )

    assert final_verification_complete
    assert not [
        name for name, after_final in operations if after_final
    ]
    assert [
        name
        for name, _after_final in operations
        if name == "validate_artifact_schema"
    ] == ["validate_artifact_schema"]
    assert [
        name
        for name, _after_final in operations
        if name == "load_artifact_schema"
    ] == ["load_artifact_schema"]
    schema_path_calls = [
        name
        for name, _after_final in operations
        if name == "schema_path"
    ]
    path_open_calls = [
        name
        for name, _after_final in operations
        if name == "Path.open"
    ]
    if schema_cache_mode == "warm":
        assert schema_path_calls == []
        assert path_open_calls == []
    else:
        assert schema_path_calls == ["schema_path"]
        assert path_open_calls == ["Path.open"]
    assert result.status is MmiProjectionResultCategory.PROJECTION_VALID_COMPLETE
    assert result.source is not None
    assert result.source.raw_bytes == raw


def test_cold_cache_checkout_race_blocks_before_any_later_schema_lookup(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    raw = b"as_of: '2026-07-25'\n"
    checkout = tmp_path / "checkout"
    detached = tmp_path / "checkout.detached"
    module = _install_checkout(checkout, raw)
    original_verify = source_capture._verify_complete_opened_path
    original_validate = source_capture.validate_artifact_schema
    original_root = source_capture._open_root
    original_relative = source_capture._open_relative
    original_close = source_capture.os.close
    opened: list[int] = []
    closed: list[int] = []
    validation_calls = 0
    mutated = False

    def tracked_validate(
        payload: object,
        *,
        schema_name: str,
    ) -> None:
        nonlocal validation_calls
        validation_calls += 1
        original_validate(payload, schema_name=schema_name)

    def replace_checkout_then_verify(
        root_anchor: source_capture._RootAnchor,
        opened_components: list[source_capture._OpenedComponent],
        *,
        source_content_stable: bool,
    ) -> None:
        nonlocal mutated
        assert validation_calls == 1
        checkout.rename(detached)
        checkout.symlink_to(detached, target_is_directory=True)
        mutated = True
        original_verify(
            root_anchor,
            opened_components,
            source_content_stable=source_content_stable,
        )

    def tracked_root(repository_root: Path, flags: int) -> int:
        descriptor = original_root(repository_root, flags)
        opened.append(descriptor)
        return descriptor

    def tracked_relative(
        name: str,
        *,
        directory_fd: int,
        flags: int,
        unstable_code: str,
    ) -> int:
        descriptor = original_relative(
            name,
            directory_fd=directory_fd,
            flags=flags,
            unstable_code=unstable_code,
        )
        opened.append(descriptor)
        return descriptor

    def tracked_close(file_fd: int) -> None:
        closed.append(file_fd)
        original_close(file_fd)

    schema_validation.load_artifact_schema.cache_clear()
    monkeypatch.setattr(
        source_capture,
        "validate_artifact_schema",
        tracked_validate,
    )
    monkeypatch.setattr(
        source_capture,
        "_verify_complete_opened_path",
        replace_checkout_then_verify,
    )
    monkeypatch.setattr(source_capture, "_open_root", tracked_root)
    monkeypatch.setattr(source_capture, "_open_relative", tracked_relative)
    monkeypatch.setattr(source_capture.os, "close", tracked_close)

    race_open_start = len(opened)
    race_close_start = len(closed)
    raced = source_capture._capture_current_mmi_source_from_module_path(
        module,
        MmiSourceRole.STRATEGY_SETTINGS,
        expected_source_sha256=hashlib.sha256(raw).hexdigest(),
    )
    race_opened = opened[race_open_start:]
    race_closed = closed[race_close_start:]

    assert mutated
    assert raced.status is MmiProjectionResultCategory.PROJECTION_BLOCKED
    assert raced.reason_codes == ("MMI_SOURCE_PATH_UNSTABLE",)
    assert raced.source is None
    assert raced.authority_effect == "NONE"
    assert validation_calls == 1
    assert schema_validation.load_artifact_schema.cache_info().misses == 1
    assert sorted(race_opened) == sorted(race_closed)
    assert len(race_closed) == len(set(race_closed))

    monkeypatch.setattr(
        source_capture,
        "_verify_complete_opened_path",
        original_verify,
    )
    cold_raw = b"cache: cold\n"
    cold_root = tmp_path / "normal-cold"
    _install_source(cold_root, cold_raw)
    schema_validation.load_artifact_schema.cache_clear()
    cold = _capture(cold_root, cold_raw)
    assert cold.status is MmiProjectionResultCategory.PROJECTION_VALID_COMPLETE
    assert cold.source is not None
    assert schema_validation.load_artifact_schema.cache_info().misses == 1

    warm_raw = b"cache: warm\n"
    warm_root = tmp_path / "normal-warm"
    _install_source(warm_root, warm_raw)
    before_warm = schema_validation.load_artifact_schema.cache_info()
    warm = _capture(warm_root, warm_raw)
    after_warm = schema_validation.load_artifact_schema.cache_info()
    assert warm.status is MmiProjectionResultCategory.PROJECTION_VALID_COMPLETE
    assert warm.source is not None
    assert after_warm.hits == before_warm.hits + 1
    assert after_warm.misses == before_warm.misses
    assert validation_calls == 3
    assert raced.status is MmiProjectionResultCategory.PROJECTION_BLOCKED
    assert raced.source is None
    assert sorted(opened) == sorted(closed)


@pytest.mark.parametrize(
    ("race", "expected_code"),
    [
        ("replace-inputs", "MMI_SOURCE_PATH_UNSTABLE"),
        ("replace-current", "MMI_SOURCE_PATH_UNSTABLE"),
        ("detach-current", "MMI_SOURCE_PATH_UNSTABLE"),
        ("remove-inputs", "MMI_SOURCE_PATH_UNSTABLE"),
        ("replace-leaf", "MMI_SOURCE_PATH_UNSTABLE"),
        ("append-leaf", "MMI_SOURCE_UNSTABLE"),
        ("truncate-leaf", "MMI_SOURCE_UNSTABLE"),
        ("metadata-leaf", "MMI_SOURCE_UNSTABLE"),
    ],
)
def test_complete_fixed_path_is_revalidated_and_descriptors_close_on_race(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    race: str,
    expected_code: str,
) -> None:
    raw = b"as_of: '2026-07-25'\nrotation: stable\n"
    leaf = _install_source(tmp_path, raw)
    original_read = source_capture._read_exact_bounded
    original_root = source_capture._open_root
    original_relative = source_capture._open_relative
    original_close = source_capture.os.close
    opened: list[int] = []
    closed: list[int] = []
    mutated = False

    def mutate_path() -> None:
        if race == "replace-inputs":
            (tmp_path / "inputs").rename(tmp_path / "inputs.detached")
            _install_source(tmp_path, raw)
        elif race == "replace-current":
            (tmp_path / "inputs/current").rename(
                tmp_path / "inputs/current.detached"
            )
            _install_source(tmp_path, raw)
        elif race == "detach-current":
            (tmp_path / "inputs/current").rename(
                tmp_path / "inputs/current.detached"
            )
        elif race == "remove-inputs":
            (tmp_path / "inputs").rename(tmp_path / "inputs.removed")
        elif race == "replace-leaf":
            leaf.rename(leaf.with_suffix(".detached"))
            leaf.write_bytes(raw)
        elif race == "append-leaf":
            with leaf.open("ab") as stream:
                stream.write(b"x")
        elif race == "truncate-leaf":
            leaf.write_bytes(raw[:-1])
        else:
            observed = leaf.stat()
            os.utime(
                leaf,
                ns=(
                    observed.st_atime_ns,
                    observed.st_mtime_ns + 1_000_000,
                ),
            )

    def mutating_read(file_fd: int, *, expected_size: int) -> bytes:
        nonlocal mutated
        observed = original_read(file_fd, expected_size=expected_size)
        if not mutated:
            mutated = True
            mutate_path()
        return observed

    def tracked_root(repository_root: Path, flags: int) -> int:
        descriptor = original_root(repository_root, flags)
        opened.append(descriptor)
        return descriptor

    def tracked_relative(
        name: str,
        *,
        directory_fd: int,
        flags: int,
        unstable_code: str,
    ) -> int:
        descriptor = original_relative(
            name,
            directory_fd=directory_fd,
            flags=flags,
            unstable_code=unstable_code,
        )
        opened.append(descriptor)
        return descriptor

    def tracked_close(file_fd: int) -> None:
        closed.append(file_fd)
        original_close(file_fd)

    monkeypatch.setattr(
        source_capture,
        "_read_exact_bounded",
        mutating_read,
    )
    monkeypatch.setattr(source_capture, "_open_root", tracked_root)
    monkeypatch.setattr(source_capture, "_open_relative", tracked_relative)
    monkeypatch.setattr(source_capture.os, "close", tracked_close)
    result = _capture(tmp_path, raw)
    assert mutated
    assert result.status is MmiProjectionResultCategory.PROJECTION_BLOCKED
    assert result.reason_codes == (expected_code,)
    assert result.source is None
    assert sorted(opened) == sorted(closed)


def test_repeated_races_have_frozen_reason_precedence_and_no_fd_leaks(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original_read = source_capture._read_exact_bounded
    original_root = source_capture._open_root
    original_relative = source_capture._open_relative
    original_close = source_capture.os.close
    opened: list[int] = []
    closed: list[int] = []
    active_case = ""
    active_leaf: Path | None = None
    active_raw = b""
    active_mutated_raw = b""
    mutation_done = False

    def mutating_read(file_fd: int, *, expected_size: int) -> bytes:
        nonlocal mutation_done
        observed = original_read(file_fd, expected_size=expected_size)
        if active_leaf is None or mutation_done:
            return observed
        mutation_done = True
        if active_case in {"leaf", "combined"}:
            active_leaf.write_bytes(active_mutated_raw)
        if active_case in {"parent", "combined"}:
            current = active_leaf.parent
            current.rename(current.with_name("current.detached"))
            _install_source(active_leaf.parents[2], active_raw)
        return observed

    def tracked_root(repository_root: Path, flags: int) -> int:
        descriptor = original_root(repository_root, flags)
        opened.append(descriptor)
        return descriptor

    def tracked_relative(
        name: str,
        *,
        directory_fd: int,
        flags: int,
        unstable_code: str,
    ) -> int:
        descriptor = original_relative(
            name,
            directory_fd=directory_fd,
            flags=flags,
            unstable_code=unstable_code,
        )
        opened.append(descriptor)
        return descriptor

    def tracked_close(file_fd: int) -> None:
        closed.append(file_fd)
        original_close(file_fd)

    monkeypatch.setattr(
        source_capture,
        "_read_exact_bounded",
        mutating_read,
    )
    monkeypatch.setattr(source_capture, "_open_root", tracked_root)
    monkeypatch.setattr(source_capture, "_open_relative", tracked_relative)
    monkeypatch.setattr(source_capture.os, "close", tracked_close)

    expected_by_case = {
        "leaf": "MMI_SOURCE_UNSTABLE",
        "parent": "MMI_SOURCE_PATH_UNSTABLE",
        "combined": "MMI_SOURCE_PATH_UNSTABLE",
    }
    totals = {
        case: {expected_code: 0}
        for case, expected_code in expected_by_case.items()
    }
    for case, expected_code in expected_by_case.items():
        for iteration in range(100):
            root = tmp_path / case / f"run-{iteration:03d}"
            raw = f"value: {iteration:04d}\n".encode("ascii")
            mutated_raw = f"value: {iteration + 1000:04d}\n".encode(
                "ascii"
            )
            active_case = case
            active_leaf = _install_source(root, raw)
            active_raw = raw
            active_mutated_raw = mutated_raw
            mutation_done = False
            open_start = len(opened)
            close_start = len(closed)

            result = _capture(root, raw)

            run_opened = opened[open_start:]
            run_closed = closed[close_start:]
            assert mutation_done
            assert result.status is (
                MmiProjectionResultCategory.PROJECTION_BLOCKED
            )
            assert result.reason_codes == (expected_code,)
            assert result.source is None
            assert result.authority_effect == "NONE"
            assert sorted(run_opened) == sorted(run_closed)
            assert len(run_closed) == len(set(run_closed))
            totals[case][expected_code] += 1

    assert totals == {
        "leaf": {"MMI_SOURCE_UNSTABLE": 100},
        "parent": {"MMI_SOURCE_PATH_UNSTABLE": 100},
        "combined": {"MMI_SOURCE_PATH_UNSTABLE": 100},
    }


def test_unsupported_required_filesystem_primitives_have_no_fallback(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    raw = b"value: exact\n"
    _install_source(tmp_path, raw)
    monkeypatch.setattr(
        source_capture,
        "_required_filesystem_primitives_available",
        lambda: False,
    )
    result = _capture(tmp_path, raw)
    assert result.reason_codes == (
        "MMI_SOURCE_FILESYSTEM_PRIMITIVES_UNAVAILABLE",
    )
    assert result.source is None


def test_capture_performs_no_write_or_discovery_operation(
    tmp_path: Path,
) -> None:
    raw = b"value: exact\n"
    path = _install_source(tmp_path, raw)
    before = {
        item.relative_to(tmp_path).as_posix(): (
            item.stat().st_mode,
            item.read_bytes() if item.is_file() else None,
        )
        for item in tmp_path.rglob("*")
    }
    result = _capture(tmp_path, raw)
    after = {
        item.relative_to(tmp_path).as_posix(): (
            item.stat().st_mode,
            item.read_bytes() if item.is_file() else None,
        )
        for item in tmp_path.rglob("*")
    }
    assert result.valid
    assert path.read_bytes() == raw
    assert after == before
