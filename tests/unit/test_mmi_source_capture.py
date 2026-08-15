from __future__ import annotations

from collections.abc import Callable
import copy
from dataclasses import fields, replace
import errno
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
    _capture_mmi_source_absence_at_root,
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


def _install_portfolio_source(root: Path, raw: bytes) -> Path:
    path = root / "inputs" / "current" / "portfolio_snapshot.txt"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(raw)
    return path


def _capture_portfolio(root: Path, raw: bytes):
    return _capture_mmi_source_at_root(
        root,
        role=MmiSourceRole.PORTFOLIO_SNAPSHOT,
        expected_source_sha256=hashlib.sha256(raw).hexdigest(),
    )


def _install_role_source(
    root: Path,
    role: MmiSourceRole,
    raw: bytes,
) -> Path:
    if role is MmiSourceRole.STRATEGY_SETTINGS:
        return _install_source(root, raw)
    assert role is MmiSourceRole.PORTFOLIO_SNAPSHOT
    return _install_portfolio_source(root, raw)


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


def _track_capture_descriptors(
    monkeypatch: pytest.MonkeyPatch,
    *,
    before_openat2: Callable[[], None] | None = None,
    after_openat2: Callable[[int], None] | None = None,
) -> tuple[list[int], list[int]]:
    original_root = source_capture._open_root
    original_relative = source_capture._open_relative
    original_openat2 = source_capture._invoke_fixed_source_openat2
    original_close = source_capture.os.close
    opened: list[int] = []
    closed: list[int] = []

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

    def tracked_openat2(
        *,
        root_fd: int,
        root_relative_path: bytes,
        how: source_capture._OpenHow,
    ) -> int:
        if before_openat2 is not None:
            before_openat2()
        descriptor = original_openat2(
            root_fd=root_fd,
            root_relative_path=root_relative_path,
            how=how,
        )
        opened.append(descriptor)
        if after_openat2 is not None:
            after_openat2(descriptor)
        return descriptor

    def tracked_close(file_fd: int) -> None:
        closed.append(file_fd)
        original_close(file_fd)

    monkeypatch.setattr(source_capture, "_open_root", tracked_root)
    monkeypatch.setattr(source_capture, "_open_relative", tracked_relative)
    monkeypatch.setattr(
        source_capture,
        "_invoke_fixed_source_openat2",
        tracked_openat2,
    )
    monkeypatch.setattr(source_capture.os, "close", tracked_close)
    return opened, closed


def _replace_fixed_path_before_openat2(
    repository_root: Path,
    role: MmiSourceRole,
    raw: bytes,
    case: str,
) -> None:
    leaf = (
        repository_root
        / MMI_SOURCE_CATALOG[role].repository_relative_locator
    )
    if case == "repository-root":
        repository_root.rename(
            repository_root.with_name(f"{repository_root.name}.detached")
        )
        _install_role_source(repository_root, role, raw)
    elif case == "authenticated-ancestor":
        ancestor = repository_root.parent
        ancestor.rename(ancestor.with_name(f"{ancestor.name}.detached"))
        _install_role_source(repository_root, role, raw)
    elif case == "current":
        current = leaf.parent
        current.rename(current.with_name("current.detached"))
        _install_role_source(repository_root, role, raw)
    elif case == "leaf":
        leaf.rename(leaf.with_name(f"{leaf.name}.detached"))
        leaf.write_bytes(raw)
    elif case == "repository-root-symlink":
        detached = repository_root.with_name(
            f"{repository_root.name}.detached"
        )
        repository_root.rename(detached)
        repository_root.symlink_to(detached, target_is_directory=True)
    elif case == "authenticated-ancestor-symlink":
        ancestor = repository_root.parent
        detached = ancestor.with_name(f"{ancestor.name}.detached")
        ancestor.rename(detached)
        ancestor.symlink_to(detached, target_is_directory=True)
    elif case == "current-symlink":
        current = leaf.parent
        detached = current.with_name("current.detached")
        current.rename(detached)
        current.symlink_to(detached, target_is_directory=True)
    elif case == "leaf-symlink":
        detached = leaf.with_name(f"{leaf.name}.detached")
        leaf.rename(detached)
        leaf.symlink_to(detached)
    elif case == "missing-component":
        leaf.parent.rename(leaf.parent.with_name("current.detached"))
    elif case == "intermediate-regular":
        current = leaf.parent
        current.rename(current.with_name("current.detached"))
        current.write_bytes(b"not a directory\n")
    elif case == "leaf-nonregular":
        leaf.rename(leaf.with_name(f"{leaf.name}.detached"))
        leaf.mkdir()
    elif case == "leaf-fifo":
        leaf.rename(leaf.with_name(f"{leaf.name}.detached"))
        os.mkfifo(leaf)
    else:
        raise AssertionError(case)


def test_source_catalog_is_exact_closed_and_code_owned() -> None:
    assert tuple(MMI_SOURCE_CATALOG) == (
        MmiSourceRole.STRATEGY_SETTINGS,
        MmiSourceRole.PORTFOLIO_SNAPSHOT,
        MmiSourceRole.LONG_HORIZON_RESEARCH,
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
    long_horizon = MMI_SOURCE_CATALOG[MmiSourceRole.LONG_HORIZON_RESEARCH]
    assert long_horizon.source_id == "MMI_LONG_HORIZON_RESEARCH"
    assert long_horizon.path_components == (
        "inputs",
        "current",
        "long_horizon_research.json",
    )
    assert long_horizon.maximum_bytes == 262_144
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


def test_portfolio_role_capture_is_exact_hash_bound_and_provenance_valid(
    tmp_path: Path,
) -> None:
    raw = b"# updated 2026-07-25\n"
    path = _install_portfolio_source(tmp_path, raw)
    (path.parent / "portfolio_snapshot.backup.txt").write_bytes(
        b"different source must not be discovered\n"
    )
    result = _capture_mmi_source_at_root(
        tmp_path,
        role=MmiSourceRole.PORTFOLIO_SNAPSHOT,
        expected_source_sha256=hashlib.sha256(raw).hexdigest(),
    )
    assert result.status is MmiProjectionResultCategory.PROJECTION_VALID_COMPLETE
    assert result.reason_codes == ()
    assert result.source is not None
    assert result.authority_effect == "NONE"
    assert result.source.role is MmiSourceRole.PORTFOLIO_SNAPSHOT
    assert result.source.raw_bytes == raw
    assert _mmi_captured_source_provenance_is_valid(result.source)
    record = dict(result.source.source_record)
    assert record["source_role"] == "PORTFOLIO_SNAPSHOT"
    assert record["source_id"] == "MMI_PORTFOLIO_SNAPSHOT"
    assert record["repository_relative_locator"] == (
        "inputs/current/portfolio_snapshot.txt"
    )
    assert record["maximum_bytes"] == 1_048_576
    assert record["observed_size_bytes"] == len(raw)
    assert record["expected_sha256"] == hashlib.sha256(raw).hexdigest()
    assert record["observed_sha256"] == hashlib.sha256(raw).hexdigest()
    assert record["operator_origin_authentication"] == "NOT_ESTABLISHED"
    assert record["authority_effect"] == "NONE"
    assert record["source_record_identity_sha256"] == record_identity_sha256(
        record,
        identity_field="source_record_identity_sha256",
        domain=MMI_SOURCE_RECORD_IDENTITY_DOMAIN,
        maximum_bytes=8_192,
    )
    validate_artifact_schema(
        record,
        schema_name="mmi_source_record_v1.schema.json",
    )


def test_formal_source_record_schema_accepts_only_two_correlated_variants(
    tmp_path: Path,
) -> None:
    strategy_raw = b"as_of: '2026-07-25'\n"
    portfolio_raw = b"# updated 2026-07-25\n"
    _install_source(tmp_path, strategy_raw)
    _install_portfolio_source(tmp_path, portfolio_raw)
    strategy_capture = _capture(tmp_path, strategy_raw)
    portfolio_capture = _capture_portfolio(tmp_path, portfolio_raw)
    assert strategy_capture.source is not None
    assert portfolio_capture.source is not None
    strategy_record = dict(strategy_capture.source.source_record)
    portfolio_record = dict(portfolio_capture.source.source_record)
    for record in (strategy_record, portfolio_record):
        validate_artifact_schema(
            record,
            schema_name="mmi_source_record_v1.schema.json",
        )

    correlated_fields = (
        (
            "source_role",
            "STRATEGY_SETTINGS",
            "PORTFOLIO_SNAPSHOT",
        ),
        (
            "source_id",
            "MMI_STRATEGY_SETTINGS",
            "MMI_PORTFOLIO_SNAPSHOT",
        ),
        (
            "repository_relative_locator",
            "inputs/current/strategy_settings.yaml",
            "inputs/current/portfolio_snapshot.txt",
        ),
        ("maximum_bytes", 262_144, 1_048_576),
    )
    for selection in range(1, (1 << len(correlated_fields)) - 1):
        candidate = copy.deepcopy(strategy_record)
        for index, (
            field,
            strategy_value,
            portfolio_value,
        ) in enumerate(correlated_fields):
            candidate[field] = (
                portfolio_value
                if selection & (1 << index)
                else strategy_value
            )
        candidate["source_record_identity_sha256"] = (
            record_identity_sha256(
                candidate,
                identity_field="source_record_identity_sha256",
                domain=MMI_SOURCE_RECORD_IDENTITY_DOMAIN,
                maximum_bytes=8_192,
            )
        )
        with pytest.raises(schema_validation.ArtifactSchemaError):
            validate_artifact_schema(
                candidate,
                schema_name="mmi_source_record_v1.schema.json",
            )

    unknown_cases = (
        ("source_role", "UNKNOWN_SOURCE_ROLE"),
        ("source_id", "MMI_UNKNOWN_SOURCE"),
        (
            "repository_relative_locator",
            "inputs/current/arbitrary.txt",
        ),
        ("maximum_bytes", 123_456),
    )
    for field, replacement in unknown_cases:
        candidate = copy.deepcopy(strategy_record)
        candidate[field] = replacement
        candidate["source_record_identity_sha256"] = (
            record_identity_sha256(
                candidate,
                identity_field="source_record_identity_sha256",
                domain=MMI_SOURCE_RECORD_IDENTITY_DOMAIN,
                maximum_bytes=8_192,
            )
        )
        with pytest.raises(schema_validation.ArtifactSchemaError):
            validate_artifact_schema(
                candidate,
                schema_name="mmi_source_record_v1.schema.json",
            )


def test_every_successful_role_capture_uses_the_formal_source_schema(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    strategy_raw = b"as_of: '2026-07-25'\n"
    portfolio_raw = b"# updated 2026-07-25\n"
    _install_source(tmp_path, strategy_raw)
    _install_portfolio_source(tmp_path, portfolio_raw)
    original_validate = source_capture.validate_artifact_schema
    validated_roles: list[str] = []

    def tracked_validate(
        payload: object,
        *,
        schema_name: str,
    ) -> None:
        assert schema_name == "mmi_source_record_v1.schema.json"
        assert type(payload) is dict
        role = payload.get("source_role")
        assert type(role) is str
        validated_roles.append(role)
        original_validate(payload, schema_name=schema_name)

    monkeypatch.setattr(
        source_capture,
        "validate_artifact_schema",
        tracked_validate,
    )
    strategy_capture = _capture(tmp_path, strategy_raw)
    portfolio_capture = _capture_portfolio(tmp_path, portfolio_raw)
    assert strategy_capture.valid
    assert portfolio_capture.valid
    assert validated_roles == [
        "STRATEGY_SETTINGS",
        "PORTFOLIO_SNAPSHOT",
    ]
    assert strategy_capture.source is not None
    assert portfolio_capture.source is not None
    for captured in (
        strategy_capture.source,
        portfolio_capture.source,
    ):
        original_validate(
            dict(captured.source_record),
            schema_name="mmi_source_record_v1.schema.json",
        )


def test_portfolio_formal_schema_failure_cannot_establish_provenance(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    raw = b"# updated 2026-07-25\n"
    _install_portfolio_source(tmp_path, raw)

    def reject_formal_schema(
        _payload: object,
        *,
        schema_name: str,
    ) -> None:
        assert schema_name == "mmi_source_record_v1.schema.json"
        raise schema_validation.ArtifactSchemaError(
            "forced portfolio schema rejection"
        )

    monkeypatch.setattr(
        source_capture,
        "validate_artifact_schema",
        reject_formal_schema,
    )
    result = _capture_portfolio(tmp_path, raw)
    assert result.status is (
        MmiProjectionResultCategory.PROJECTION_CONTRACT_FAILURE
    )
    assert result.reason_codes == ("MMI_SOURCE_RECORD_CONTRACT_FAILURE",)
    assert result.source is None
    assert result.authority_effect == "NONE"


def test_portfolio_capture_retains_mandatory_hash_and_one_mib_bound(
    tmp_path: Path,
) -> None:
    missing_hash = _capture_mmi_source_at_root(
        tmp_path,
        role=MmiSourceRole.PORTFOLIO_SNAPSHOT,
        expected_source_sha256="",
    )
    assert missing_hash.reason_codes == (
        "MMI_SOURCE_EXPECTED_SHA256_REQUIRED",
    )
    oversized = b"x" * 1_048_577
    _install_portfolio_source(tmp_path, oversized)
    result = _capture_portfolio(tmp_path, oversized)
    assert result.status is MmiProjectionResultCategory.PROJECTION_BLOCKED
    assert result.reason_codes == ("MMI_SOURCE_OVERSIZED",)
    assert result.source is None


def test_portfolio_capture_rejects_symlink_and_unstable_leaf(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    raw = b"# updated 2026-07-25\n"
    external = tmp_path / "external-portfolio.txt"
    external.write_bytes(raw)
    leaf = tmp_path / "symlink" / "inputs/current/portfolio_snapshot.txt"
    leaf.parent.mkdir(parents=True)
    leaf.symlink_to(external)
    symlinked = _capture_mmi_source_at_root(
        tmp_path / "symlink",
        role=MmiSourceRole.PORTFOLIO_SNAPSHOT,
        expected_source_sha256=hashlib.sha256(raw).hexdigest(),
    )
    assert symlinked.status is (
        MmiProjectionResultCategory.PROJECTION_BLOCKED
    )
    assert symlinked.reason_codes == ("MMI_SOURCE_SYMLINK_REJECTED",)
    assert symlinked.source is None

    unstable_root = tmp_path / "unstable"
    unstable_leaf = _install_portfolio_source(unstable_root, raw)
    original_read = source_capture._read_exact_bounded
    first_source_read = True

    def mutate_after_first_read(
        file_fd: int,
        *,
        expected_size: int,
    ) -> bytes:
        nonlocal first_source_read
        observed = original_read(
            file_fd,
            expected_size=expected_size,
        )
        if first_source_read:
            first_source_read = False
            unstable_leaf.write_bytes(b"# updated 2026-07-24\n")
        return observed

    monkeypatch.setattr(
        source_capture,
        "_read_exact_bounded",
        mutate_after_first_read,
    )
    unstable = _capture_portfolio(unstable_root, raw)
    assert unstable.status is (
        MmiProjectionResultCategory.PROJECTION_BLOCKED
    )
    assert unstable.reason_codes == ("MMI_SOURCE_UNSTABLE",)
    assert unstable.source is None
    assert unstable.authority_effect == "NONE"


def test_unknown_and_caller_coerced_source_roles_remain_blocked(
    tmp_path: Path,
) -> None:
    for role in ("PORTFOLIO_SNAPSHOT", object()):
        result = _capture_mmi_source_at_root(
            tmp_path,
            role=role,  # type: ignore[arg-type]
            expected_source_sha256="0" * 64,
        )
        assert result.status is MmiProjectionResultCategory.PROJECTION_BLOCKED
        assert result.reason_codes == ("MMI_SOURCE_ROLE_INVALID",)
        assert result.source is None


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


def test_unrelated_directory_churn_preserves_the_bound_production_path(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    raw = b"as_of: '2026-07-25'\n"
    checkout = tmp_path / "checkout"
    module = _install_checkout(checkout, raw)
    sibling = checkout / "unrelated-existing-sibling"
    sibling.mkdir()
    retained_name = "unrelated-retained-sibling"
    retained = checkout / retained_name
    original_verify = source_capture._verify_complete_opened_path
    original_root = source_capture._open_root
    original_relative = source_capture._open_relative
    original_complete = source_capture._open_complete_fixed_source_path
    original_close = source_capture.os.close
    opened: list[int] = []
    closed: list[int] = []
    initial_checkout_entries: frozenset[str] | None = None
    observed_checkout_entries: frozenset[str] | None = None
    retained_created = False
    churned = False

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

    def tracked_complete(
        root_anchor: source_capture._RootAnchor,
        *,
        repository_root: Path,
        spec: object,
        leaf_flags: int,
    ) -> int:
        descriptor = original_complete(
            root_anchor,
            repository_root=repository_root,
            spec=spec,
            leaf_flags=leaf_flags,
        )
        opened.append(descriptor)
        return descriptor

    def churn_then_verify(
        root_anchor: source_capture._RootAnchor,
        opened_components: list[source_capture._OpenedComponent],
    ) -> None:
        nonlocal churned
        nonlocal initial_checkout_entries
        nonlocal observed_checkout_entries
        nonlocal retained_created
        checkout_component = next(
            component
            for component in opened_components
            if component.name == "checkout"
            and component.expected_kind == "DIRECTORY"
        )
        initial_checkout_entries = frozenset(
            os.listdir(checkout_component.opened_fd)
        )
        assert retained_name not in initial_checkout_entries
        child = sibling / "mutable-child"
        child.write_bytes(b"first")
        child.write_bytes(b"second")
        child.unlink()
        transient = checkout / "unrelated-transient-sibling"
        transient.mkdir()
        transient.rmdir()
        os.mkdir(
            retained_name,
            dir_fd=checkout_component.opened_fd,
        )
        retained_created = True
        retained_entry = os.stat(
            retained_name,
            dir_fd=checkout_component.opened_fd,
            follow_symlinks=False,
        )
        observed_checkout_entries = frozenset(
            os.listdir(checkout_component.opened_fd)
        )
        assert stat.S_ISDIR(retained_entry.st_mode)
        assert retained_name in observed_checkout_entries
        assert observed_checkout_entries - initial_checkout_entries == {
            retained_name
        }
        assert initial_checkout_entries - observed_checkout_entries == set()
        source_spec = MMI_SOURCE_CATALOG[MmiSourceRole.STRATEGY_SETTINGS]
        assert retained_name != source_spec.path_components[0]

        checkout_entry = source_capture._witness(
            os.stat(
                checkout_component.name,
                dir_fd=checkout_component.parent_fd,
                follow_symlinks=False,
            )
        )
        checkout_opened = source_capture._witness(
            os.fstat(checkout_component.opened_fd)
        )
        assert source_capture._same_directory_binding(
            checkout_component.witness,
            checkout_entry,
        )
        assert source_capture._same_directory_binding(
            checkout_component.witness,
            checkout_opened,
        )
        churned = True
        original_verify(
            root_anchor,
            opened_components,
        )

    monkeypatch.setattr(
        source_capture,
        "_verify_complete_opened_path",
        churn_then_verify,
    )
    monkeypatch.setattr(source_capture, "_open_root", tracked_root)
    monkeypatch.setattr(source_capture, "_open_relative", tracked_relative)
    monkeypatch.setattr(
        source_capture,
        "_open_complete_fixed_source_path",
        tracked_complete,
    )
    monkeypatch.setattr(source_capture.os, "close", tracked_close)

    expected_sha256 = hashlib.sha256(raw).hexdigest()
    primary_failure: BaseException | None = None
    try:
        result = source_capture._capture_current_mmi_source_from_module_path(
            module,
            MmiSourceRole.STRATEGY_SETTINGS,
            expected_source_sha256=expected_sha256,
        )

        assert churned
        assert initial_checkout_entries is not None
        assert observed_checkout_entries == (
            initial_checkout_entries | {retained_name}
        )
        assert retained.is_dir()
        assert result.status is (
            MmiProjectionResultCategory.PROJECTION_VALID_COMPLETE
        )
        assert result.source is not None
        assert result.source.raw_bytes == raw
        record = dict(result.source.source_record)
        assert record["repository_relative_locator"] == (
            "inputs/current/strategy_settings.yaml"
        )
        assert record["expected_sha256"] == expected_sha256
        assert record["observed_sha256"] == expected_sha256
        assert record["operator_origin_authentication"] == (
            "NOT_ESTABLISHED"
        )
        validate_artifact_schema(
            record,
            schema_name="mmi_source_record_v1.schema.json",
        )
        assert record["source_record_identity_sha256"] == (
            record_identity_sha256(
                record,
                identity_field="source_record_identity_sha256",
                domain=MMI_SOURCE_RECORD_IDENTITY_DOMAIN,
                maximum_bytes=8_192,
            )
        )
        assert _mmi_captured_source_provenance_is_valid(result.source)
        assert result.authority_effect == "NONE"
        assert sorted(opened) == sorted(closed)
        assert len(closed) == len(set(closed))
    except BaseException as exc:
        primary_failure = exc
        raise
    finally:
        if retained_created:
            try:
                retained.rmdir()
            except OSError:
                if primary_failure is None:
                    raise
    assert not retained.exists()


@pytest.mark.parametrize(
    "replacement",
    (
        "repository-root",
        "repository-root-symlink",
        "current-directory",
        "source-leaf",
    ),
)
def test_directory_churn_tolerance_does_not_weaken_path_replacement(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    replacement: str,
) -> None:
    raw = b"as_of: '2026-07-25'\n"
    checkout = tmp_path / "checkout"
    detached_checkout = tmp_path / "checkout.detached"
    module = _install_checkout(checkout, raw)
    leaf = checkout / "inputs/current/strategy_settings.yaml"
    original_verify = source_capture._verify_complete_opened_path
    original_root = source_capture._open_root
    original_relative = source_capture._open_relative
    original_close = source_capture.os.close
    opened: list[int] = []
    closed: list[int] = []
    replaced = False

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
    ) -> None:
        nonlocal replaced
        if replacement == "repository-root":
            checkout.rename(detached_checkout)
            _install_checkout(checkout, raw)
        elif replacement == "repository-root-symlink":
            checkout.rename(detached_checkout)
            checkout.symlink_to(
                detached_checkout,
                target_is_directory=True,
            )
        elif replacement == "current-directory":
            current = checkout / "inputs/current"
            current.rename(checkout / "inputs/current.detached")
            _install_source(checkout, raw)
        else:
            leaf.rename(leaf.with_suffix(".detached"))
            leaf.write_bytes(raw)
        replaced = True
        original_verify(
            root_anchor,
            opened_components,
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

    assert replaced
    assert result.status is MmiProjectionResultCategory.PROJECTION_BLOCKED
    assert result.reason_codes == ("MMI_SOURCE_PATH_UNSTABLE",)
    assert result.source is None
    assert result.authority_effect == "NONE"
    assert sorted(opened) == sorted(closed)
    assert len(closed) == len(set(closed))


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
    ) -> None:
        nonlocal verify_calls
        verify_calls += 1
        raise AssertionError("final path verification unexpectedly ran")

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
@pytest.mark.parametrize(
    "role",
    (
        MmiSourceRole.STRATEGY_SETTINGS,
        MmiSourceRole.PORTFOLIO_SNAPSHOT,
    ),
)
def test_complete_chain_verification_is_final_path_operation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    schema_cache_mode: str,
    role: MmiSourceRole,
) -> None:
    raw = (
        b"as_of: '2026-07-25'\n"
        if role is MmiSourceRole.STRATEGY_SETTINGS
        else b"# updated 2026-07-25\n"
    )
    checkout = tmp_path / "checkout"
    module = _install_checkout(
        checkout,
        (
            raw
            if role is MmiSourceRole.STRATEGY_SETTINGS
            else b"as_of: '2026-07-25'\n"
        ),
    )
    if role is MmiSourceRole.PORTFOLIO_SNAPSHOT:
        _install_portfolio_source(checkout, raw)
    original_available = (
        source_capture._required_filesystem_primitives_available
    )
    original_capture = source_capture._capture_fixed_source_bytes
    original_final_snapshot = (
        source_capture._capture_final_bound_source_snapshot
    )
    original_openat2 = source_capture._invoke_fixed_source_openat2
    original_read = source_capture._read_exact_bounded
    original_relative = source_capture._open_relative
    original_os_stat = source_capture.os.stat
    original_validate = source_capture.validate_artifact_schema
    original_cached_load = schema_validation.load_artifact_schema
    original_schema_path = schema_validation.schema_path
    original_path_resolve = Path.resolve
    original_path_open = Path.open
    original_path_stat = Path.stat
    original_path_exists = Path.exists
    capture_active = False
    final_binding_complete = False
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
        operations.append((name, final_binding_complete))
        if final_binding_complete:
            raise AssertionError(
                f"{name} occurred after final source binding"
            )

    def tracked_capture(*args: object, **kwargs: object):
        nonlocal capture_active
        capture_active = True
        try:
            return original_capture(*args, **kwargs)
        finally:
            capture_active = False

    def tracked_final_snapshot(
        *args: object,
        **kwargs: object,
    ) -> source_capture._FinalSourceSnapshot:
        nonlocal final_binding_complete
        snapshot = original_final_snapshot(*args, **kwargs)
        final_binding_complete = True
        return snapshot

    def guarded_openat2(
        *,
        root_fd: int,
        root_relative_path: bytes,
        how: source_capture._OpenHow,
    ) -> int:
        observe("openat2")
        return original_openat2(
            root_fd=root_fd,
            root_relative_path=root_relative_path,
            how=how,
        )

    def guarded_read(file_fd: int, *, expected_size: int) -> bytes:
        observe("bounded descriptor read")
        return original_read(file_fd, expected_size=expected_size)

    def guarded_os_stat(
        path: object,
        *args: object,
        **kwargs: object,
    ):
        observe("os.stat")
        return original_os_stat(path, *args, **kwargs)

    def guarded_open_relative(
        name: str,
        *,
        directory_fd: int,
        flags: int,
        unstable_code: str,
    ) -> int:
        observe("descriptor-relative os.open")
        return original_relative(
            name,
            directory_fd=directory_fd,
            flags=flags,
            unstable_code=unstable_code,
        )

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
        "_capture_final_bound_source_snapshot",
        tracked_final_snapshot,
    )
    monkeypatch.setattr(
        source_capture,
        "_invoke_fixed_source_openat2",
        guarded_openat2,
    )
    monkeypatch.setattr(
        source_capture,
        "_read_exact_bounded",
        guarded_read,
    )
    monkeypatch.setattr(
        source_capture,
        "_open_relative",
        guarded_open_relative,
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
        role,
        expected_source_sha256=hashlib.sha256(raw).hexdigest(),
    )

    assert final_binding_complete
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
    assert [
        name
        for name, _after_final in operations
        if name == "openat2"
    ] == ["openat2"]
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
    original_complete = source_capture._open_complete_fixed_source_path
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
    ) -> None:
        nonlocal mutated
        assert validation_calls == 1
        checkout.rename(detached)
        checkout.symlink_to(detached, target_is_directory=True)
        mutated = True
        original_verify(
            root_anchor,
            opened_components,
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

    def tracked_complete(
        root_anchor: source_capture._RootAnchor,
        *,
        repository_root: Path,
        spec: object,
        leaf_flags: int,
    ) -> int:
        descriptor = original_complete(
            root_anchor,
            repository_root=repository_root,
            spec=spec,
            leaf_flags=leaf_flags,
        )
        opened.append(descriptor)
        return descriptor

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
    monkeypatch.setattr(
        source_capture,
        "_open_complete_fixed_source_path",
        tracked_complete,
    )
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
    "role",
    (
        MmiSourceRole.STRATEGY_SETTINGS,
        MmiSourceRole.PORTFOLIO_SNAPSHOT,
    ),
)
def test_final_bound_read_rejects_100_same_size_boundary_mutations(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    role: MmiSourceRole,
) -> None:
    original_verify = source_capture._verify_complete_opened_path
    original_root = source_capture._open_root
    original_relative = source_capture._open_relative
    original_complete = source_capture._open_complete_fixed_source_path
    original_close = source_capture.os.close
    opened: list[int] = []
    closed: list[int] = []
    active_leaf: Path | None = None
    active_mutated = b""
    mutation_done = False

    def mutate_at_old_verification_boundary(
        root_anchor: source_capture._RootAnchor,
        opened_components: list[source_capture._OpenedComponent],
    ) -> None:
        nonlocal mutation_done
        original_verify(root_anchor, opened_components)
        assert active_leaf is not None
        assert not mutation_done
        active_leaf.write_bytes(active_mutated)
        mutation_done = True

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

    def tracked_complete(
        root_anchor: source_capture._RootAnchor,
        *,
        repository_root: Path,
        spec: object,
        leaf_flags: int,
    ) -> int:
        descriptor = original_complete(
            root_anchor,
            repository_root=repository_root,
            spec=spec,
            leaf_flags=leaf_flags,
        )
        opened.append(descriptor)
        return descriptor

    monkeypatch.setattr(
        source_capture,
        "_verify_complete_opened_path",
        mutate_at_old_verification_boundary,
    )
    monkeypatch.setattr(source_capture, "_open_root", tracked_root)
    monkeypatch.setattr(source_capture, "_open_relative", tracked_relative)
    monkeypatch.setattr(
        source_capture,
        "_open_complete_fixed_source_path",
        tracked_complete,
    )
    monkeypatch.setattr(source_capture.os, "close", tracked_close)

    totals = {"MMI_SOURCE_UNSTABLE": 0}
    for iteration in range(100):
        root = tmp_path / role.value.casefold() / f"run-{iteration:03d}"
        raw = f"value: {iteration:04d}\n".encode("ascii")
        mutated = f"value: {iteration + 1000:04d}\n".encode("ascii")
        assert len(mutated) == len(raw)
        if role is MmiSourceRole.STRATEGY_SETTINGS:
            active_leaf = _install_source(root, raw)
        else:
            active_leaf = _install_portfolio_source(root, raw)
        active_mutated = mutated
        mutation_done = False
        open_start = len(opened)
        close_start = len(closed)

        result = _capture_mmi_source_at_root(
            root,
            role=role,
            expected_source_sha256=hashlib.sha256(raw).hexdigest(),
        )

        run_opened = opened[open_start:]
        run_closed = closed[close_start:]
        assert mutation_done
        assert active_leaf.read_bytes() == mutated
        assert result.status is (
            MmiProjectionResultCategory.PROJECTION_BLOCKED
        )
        assert result.reason_codes == ("MMI_SOURCE_UNSTABLE",)
        assert result.source is None
        assert result.authority_effect == "NONE"
        assert sorted(run_opened) == sorted(run_closed)
        assert len(run_closed) == len(set(run_closed))
        totals["MMI_SOURCE_UNSTABLE"] += 1

    assert totals == {"MMI_SOURCE_UNSTABLE": 100}


@pytest.mark.parametrize(
    "role",
    (
        MmiSourceRole.STRATEGY_SETTINGS,
        MmiSourceRole.PORTFOLIO_SNAPSHOT,
    ),
)
def test_final_bound_read_accepts_exact_same_inode_restoration(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    role: MmiSourceRole,
) -> None:
    raw = b"value: 0001\n"
    mutated = b"value: 9001\n"
    assert len(mutated) == len(raw)
    if role is MmiSourceRole.STRATEGY_SETTINGS:
        leaf = _install_source(tmp_path, raw)
    else:
        leaf = _install_portfolio_source(tmp_path, raw)
    original_verify = source_capture._verify_complete_opened_path
    original_root = source_capture._open_root
    original_relative = source_capture._open_relative
    original_complete = source_capture._open_complete_fixed_source_path
    original_close = source_capture.os.close
    opened: list[int] = []
    closed: list[int] = []
    restored = False

    def mutate_restore_at_old_verification_boundary(
        root_anchor: source_capture._RootAnchor,
        opened_components: list[source_capture._OpenedComponent],
    ) -> None:
        nonlocal restored
        original_verify(root_anchor, opened_components)
        leaf.write_bytes(mutated)
        leaf.write_bytes(raw)
        restored = True

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

    def tracked_complete(
        root_anchor: source_capture._RootAnchor,
        *,
        repository_root: Path,
        spec: object,
        leaf_flags: int,
    ) -> int:
        descriptor = original_complete(
            root_anchor,
            repository_root=repository_root,
            spec=spec,
            leaf_flags=leaf_flags,
        )
        opened.append(descriptor)
        return descriptor

    monkeypatch.setattr(
        source_capture,
        "_verify_complete_opened_path",
        mutate_restore_at_old_verification_boundary,
    )
    monkeypatch.setattr(source_capture, "_open_root", tracked_root)
    monkeypatch.setattr(source_capture, "_open_relative", tracked_relative)
    monkeypatch.setattr(
        source_capture,
        "_open_complete_fixed_source_path",
        tracked_complete,
    )
    monkeypatch.setattr(source_capture.os, "close", tracked_close)

    result = _capture_mmi_source_at_root(
        tmp_path,
        role=role,
        expected_source_sha256=hashlib.sha256(raw).hexdigest(),
    )

    assert restored
    assert leaf.read_bytes() == raw
    assert result.status is (
        MmiProjectionResultCategory.PROJECTION_VALID_COMPLETE
    )
    assert result.reason_codes == ()
    assert result.source is not None
    assert result.source.raw_bytes == raw
    assert result.source.source_record["observed_size_bytes"] == len(raw)
    assert result.source.source_record["observed_sha256"] == (
        hashlib.sha256(raw).hexdigest()
    )
    assert result.authority_effect == "NONE"
    assert sorted(opened) == sorted(closed)
    assert len(closed) == len(set(closed))


@pytest.mark.parametrize(
    "role",
    (
        MmiSourceRole.STRATEGY_SETTINGS,
        MmiSourceRole.PORTFOLIO_SNAPSHOT,
    ),
)
def test_final_bound_open_rejects_leaf_replacement_after_path_verification(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    role: MmiSourceRole,
) -> None:
    raw = b"value: stable\n"
    if role is MmiSourceRole.STRATEGY_SETTINGS:
        leaf = _install_source(tmp_path, raw)
    else:
        leaf = _install_portfolio_source(tmp_path, raw)
    original_verify = source_capture._verify_complete_opened_path
    original_root = source_capture._open_root
    original_relative = source_capture._open_relative
    original_complete = source_capture._open_complete_fixed_source_path
    original_close = source_capture.os.close
    opened: list[int] = []
    closed: list[int] = []
    replaced = False

    def replace_at_old_verification_boundary(
        root_anchor: source_capture._RootAnchor,
        opened_components: list[source_capture._OpenedComponent],
    ) -> None:
        nonlocal replaced
        original_verify(root_anchor, opened_components)
        leaf.rename(leaf.with_suffix(".detached"))
        leaf.write_bytes(raw)
        replaced = True

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

    def tracked_complete(
        root_anchor: source_capture._RootAnchor,
        *,
        repository_root: Path,
        spec: object,
        leaf_flags: int,
    ) -> int:
        descriptor = original_complete(
            root_anchor,
            repository_root=repository_root,
            spec=spec,
            leaf_flags=leaf_flags,
        )
        opened.append(descriptor)
        return descriptor

    monkeypatch.setattr(
        source_capture,
        "_verify_complete_opened_path",
        replace_at_old_verification_boundary,
    )
    monkeypatch.setattr(source_capture, "_open_root", tracked_root)
    monkeypatch.setattr(source_capture, "_open_relative", tracked_relative)
    monkeypatch.setattr(
        source_capture,
        "_open_complete_fixed_source_path",
        tracked_complete,
    )
    monkeypatch.setattr(source_capture.os, "close", tracked_close)

    result = _capture_mmi_source_at_root(
        tmp_path,
        role=role,
        expected_source_sha256=hashlib.sha256(raw).hexdigest(),
    )

    assert replaced
    assert result.status is MmiProjectionResultCategory.PROJECTION_BLOCKED
    assert result.reason_codes == ("MMI_SOURCE_PATH_UNSTABLE",)
    assert result.source is None
    assert result.authority_effect == "NONE"
    assert sorted(opened) == sorted(closed)
    assert len(closed) == len(set(closed))


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
    original_complete = source_capture._open_complete_fixed_source_path
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

    def tracked_complete(
        root_anchor: source_capture._RootAnchor,
        *,
        repository_root: Path,
        spec: object,
        leaf_flags: int,
    ) -> int:
        descriptor = original_complete(
            root_anchor,
            repository_root=repository_root,
            spec=spec,
            leaf_flags=leaf_flags,
        )
        opened.append(descriptor)
        return descriptor

    monkeypatch.setattr(
        source_capture,
        "_read_exact_bounded",
        mutating_read,
    )
    monkeypatch.setattr(source_capture, "_open_root", tracked_root)
    monkeypatch.setattr(source_capture, "_open_relative", tracked_relative)
    monkeypatch.setattr(
        source_capture,
        "_open_complete_fixed_source_path",
        tracked_complete,
    )
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


def test_openat2_wrapper_uses_exact_root_anchored_kernel_contract(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    raw = b"value: exact\n"
    leaf = _install_source(tmp_path, raw)
    root_flags = (
        os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC | os.O_NOFOLLOW
    )
    root_fd = os.open(os.path.sep, root_flags)
    final_fd: int | None = None
    observations: dict[str, object] = {}

    class FakeSyscall:
        restype: object = None

        def __call__(self, *args: object) -> int:
            observations["syscall_number"] = args[0].value
            observations["root_fd"] = args[1].value
            observations["path"] = args[2].value
            observations["how"] = args[3]._obj
            observations["size"] = args[4].value
            return os.open(
                leaf,
                os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW,
            )

    fake_syscall = FakeSyscall()
    fake_libc = SimpleNamespace(syscall=fake_syscall)

    def fake_cdll(name: object, *, use_errno: bool) -> object:
        observations["cdll"] = (name, use_errno)
        return fake_libc

    monkeypatch.setattr(source_capture.ctypes, "CDLL", fake_cdll)
    try:
        root_anchor = source_capture._RootAnchor(
            opened_fd=root_fd,
            witness=source_capture._witness(os.fstat(root_fd)),
        )
        final_fd = source_capture._open_complete_fixed_source_path(
            root_anchor,
            repository_root=tmp_path,
            spec=MMI_SOURCE_CATALOG[MmiSourceRole.STRATEGY_SETTINGS],
            leaf_flags=(
                os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW
            ),
        )
        how = observations["how"]
        assert type(how) is source_capture._OpenHow
        assert source_capture.ctypes.sizeof(how) == 24
        assert how.flags == (
            os.O_RDONLY
            | os.O_CLOEXEC
            | os.O_NOFOLLOW
            | os.O_NONBLOCK
        )
        assert how.mode == 0
        assert how.resolve == (
            source_capture._RESOLVE_IN_ROOT
            | source_capture._RESOLVE_NO_SYMLINKS
            | source_capture._RESOLVE_NO_MAGICLINKS
        )
        assert observations == {
            "cdll": (None, True),
            "syscall_number": 437,
            "root_fd": root_fd,
            "path": os.fsencode(os.fspath(leaf).lstrip(os.path.sep)),
            "how": how,
            "size": 24,
        }
        assert os.get_inheritable(final_fd) is False
        assert os.read(final_fd, len(raw) + 1) == raw
    finally:
        if final_fd is not None:
            os.close(final_fd)
        os.close(root_fd)


@pytest.mark.parametrize(
    "capability_errno",
    (
        errno.ENOSYS,
        errno.E2BIG,
        errno.EINVAL,
        errno.EOPNOTSUPP,
        errno.EPERM,
    ),
)
def test_openat2_capability_errors_fail_closed_without_sequential_fallback(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capability_errno: int,
) -> None:
    raw = b"value: exact\n"
    _install_source(tmp_path, raw)
    opened, closed = _track_capture_descriptors(monkeypatch)
    original_relative = source_capture._open_relative
    relative_calls: list[str] = []
    relative_calls_at_syscall: list[int] = []

    def tracked_relative(
        name: str,
        *,
        directory_fd: int,
        flags: int,
        unstable_code: str,
    ) -> int:
        relative_calls.append(name)
        return original_relative(
            name,
            directory_fd=directory_fd,
            flags=flags,
            unstable_code=unstable_code,
        )

    class FailingSyscall:
        restype: object = None

        def __call__(self, *_args: object) -> int:
            relative_calls_at_syscall.append(len(relative_calls))
            return -1

    monkeypatch.setattr(source_capture, "_open_relative", tracked_relative)
    monkeypatch.setattr(
        source_capture.ctypes,
        "CDLL",
        lambda _name, *, use_errno: SimpleNamespace(
            syscall=FailingSyscall()
        ),
    )
    monkeypatch.setattr(
        source_capture.ctypes,
        "get_errno",
        lambda: capability_errno,
    )

    result = _capture(tmp_path, raw)

    assert result.status is MmiProjectionResultCategory.PROJECTION_BLOCKED
    assert result.reason_codes == (
        "MMI_SOURCE_FILESYSTEM_PRIMITIVES_UNAVAILABLE",
    )
    assert result.source is None
    assert result.authority_effect == "NONE"
    assert relative_calls_at_syscall == [len(relative_calls)]
    assert sorted(opened) == sorted(closed)
    assert len(closed) == len(set(closed))


def test_openat2_missing_syscall_symbol_fails_closed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    raw = b"value: exact\n"
    _install_source(tmp_path, raw)
    opened, closed = _track_capture_descriptors(monkeypatch)
    monkeypatch.setattr(
        source_capture.ctypes,
        "CDLL",
        lambda _name, *, use_errno: SimpleNamespace(),
    )

    result = _capture(tmp_path, raw)

    assert result.status is MmiProjectionResultCategory.PROJECTION_BLOCKED
    assert result.reason_codes == (
        "MMI_SOURCE_FILESYSTEM_PRIMITIVES_UNAVAILABLE",
    )
    assert result.source is None
    assert result.authority_effect == "NONE"
    assert sorted(opened) == sorted(closed)
    assert len(closed) == len(set(closed))


@pytest.mark.parametrize("inheritable_result", ("error", "true"))
def test_openat2_final_descriptor_closes_when_cloexec_cannot_be_established(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    inheritable_result: str,
) -> None:
    raw = b"value: exact\n"
    _install_source(tmp_path, raw)
    opened, closed = _track_capture_descriptors(monkeypatch)
    original_get_inheritable = source_capture.os.get_inheritable

    def failing_get_inheritable(file_fd: int) -> bool:
        if file_fd == opened[-1]:
            if inheritable_result == "error":
                raise OSError(errno.EBADF, "injected inheritable failure")
            return True
        return original_get_inheritable(file_fd)

    monkeypatch.setattr(
        source_capture.os,
        "get_inheritable",
        failing_get_inheritable,
    )

    result = _capture(tmp_path, raw)

    assert result.status is MmiProjectionResultCategory.PROJECTION_BLOCKED
    assert result.reason_codes == (
        "MMI_SOURCE_FILESYSTEM_PRIMITIVES_UNAVAILABLE",
    )
    assert result.source is None
    assert result.authority_effect == "NONE"
    assert sorted(opened) == sorted(closed)
    assert len(closed) == len(set(closed))


@pytest.mark.parametrize(
    ("system_name", "machine"),
    (("Linux", "unsupported"), ("NotLinux", "x86_64")),
)
def test_openat2_unsupported_platform_fails_before_any_open(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    system_name: str,
    machine: str,
) -> None:
    raw = b"value: exact\n"
    _install_source(tmp_path, raw)
    root_opened = False

    def forbidden_root_open(_repository_root: Path, _flags: int) -> int:
        nonlocal root_opened
        root_opened = True
        raise AssertionError("unsupported architecture opened a path")

    monkeypatch.setattr(
        source_capture.os,
        "uname",
        lambda: SimpleNamespace(
            sysname=system_name,
            machine=machine,
        ),
    )
    monkeypatch.setattr(source_capture, "_open_root", forbidden_root_open)

    result = _capture(tmp_path, raw)

    assert not root_opened
    assert result.status is MmiProjectionResultCategory.PROJECTION_BLOCKED
    assert result.reason_codes == (
        "MMI_SOURCE_FILESYSTEM_PRIMITIVES_UNAVAILABLE",
    )
    assert result.source is None
    assert result.authority_effect == "NONE"


@pytest.mark.parametrize(
    "role",
    (
        MmiSourceRole.STRATEGY_SETTINGS,
        MmiSourceRole.PORTFOLIO_SNAPSHOT,
    ),
)
@pytest.mark.parametrize(
    "replacement",
    (
        "repository-root",
        "authenticated-ancestor",
        "current",
        "leaf",
    ),
)
def test_complete_path_open_rejects_each_fixed_entry_change(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    role: MmiSourceRole,
    replacement: str,
) -> None:
    raw = b"value: fixed\n"
    root = (
        tmp_path
        / replacement
        / "authenticated-ancestor"
        / "repository"
    )
    _install_role_source(root, role, raw)
    mutated = False

    def mutate() -> None:
        nonlocal mutated
        _replace_fixed_path_before_openat2(
            root,
            role,
            raw,
            replacement,
        )
        mutated = True

    opened, closed = _track_capture_descriptors(
        monkeypatch,
        before_openat2=mutate,
    )

    result = _capture_mmi_source_at_root(
        root,
        role=role,
        expected_source_sha256=hashlib.sha256(raw).hexdigest(),
    )

    assert mutated
    assert result.status is MmiProjectionResultCategory.PROJECTION_BLOCKED
    assert result.reason_codes == ("MMI_SOURCE_PATH_UNSTABLE",)
    assert result.source is None
    assert result.authority_effect == "NONE"
    assert sorted(opened) == sorted(closed)
    assert len(closed) == len(set(closed))


@pytest.mark.parametrize(
    "role",
    (
        MmiSourceRole.STRATEGY_SETTINGS,
        MmiSourceRole.PORTFOLIO_SNAPSHOT,
    ),
)
@pytest.mark.parametrize(
    "replacement",
    ("repository-root", "current"),
)
def test_complete_path_open_rejects_100_detached_tree_races_per_role(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    role: MmiSourceRole,
    replacement: str,
) -> None:
    active_root: Path | None = None
    active_raw = b""
    mutation_count = 0

    def mutate() -> None:
        nonlocal mutation_count
        assert active_root is not None
        _replace_fixed_path_before_openat2(
            active_root,
            role,
            active_raw,
            replacement,
        )
        mutation_count += 1

    opened, closed = _track_capture_descriptors(
        monkeypatch,
        before_openat2=mutate,
    )
    totals = {"MMI_SOURCE_PATH_UNSTABLE": 0}
    successes = 0

    for iteration in range(100):
        active_root = (
            tmp_path
            / role.value.casefold()
            / replacement
            / f"run-{iteration:03d}"
            / "repository"
        )
        active_raw = f"value: {iteration:04d}\n".encode("ascii")
        _install_role_source(active_root, role, active_raw)
        open_start = len(opened)
        close_start = len(closed)

        result = _capture_mmi_source_at_root(
            active_root,
            role=role,
            expected_source_sha256=hashlib.sha256(active_raw).hexdigest(),
        )

        if result.source is not None:
            successes += 1
        run_opened = opened[open_start:]
        run_closed = closed[close_start:]
        assert result.status is MmiProjectionResultCategory.PROJECTION_BLOCKED
        assert result.reason_codes == ("MMI_SOURCE_PATH_UNSTABLE",)
        assert result.source is None
        assert result.authority_effect == "NONE"
        assert sorted(run_opened) == sorted(run_closed)
        assert len(run_closed) == len(set(run_closed))
        totals["MMI_SOURCE_PATH_UNSTABLE"] += 1

    assert mutation_count == 100
    assert totals == {"MMI_SOURCE_PATH_UNSTABLE": 100}
    assert successes == 0
    assert sorted(opened) == sorted(closed)


@pytest.mark.parametrize(
    "role",
    (
        MmiSourceRole.STRATEGY_SETTINGS,
        MmiSourceRole.PORTFOLIO_SNAPSHOT,
    ),
)
@pytest.mark.parametrize(
    "replacement",
    (
        "repository-root-symlink",
        "authenticated-ancestor-symlink",
        "current-symlink",
        "leaf-symlink",
        "missing-component",
        "intermediate-regular",
        "leaf-nonregular",
        "leaf-fifo",
    ),
)
def test_complete_path_open_rejects_symlink_missing_and_wrong_kind_components(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    role: MmiSourceRole,
    replacement: str,
) -> None:
    raw = b"value: fixed\n"
    root = (
        tmp_path
        / replacement
        / "authenticated-ancestor"
        / "repository"
    )
    _install_role_source(root, role, raw)
    mutated = False

    def mutate() -> None:
        nonlocal mutated
        _replace_fixed_path_before_openat2(
            root,
            role,
            raw,
            replacement,
        )
        mutated = True

    opened, closed = _track_capture_descriptors(
        monkeypatch,
        before_openat2=mutate,
    )

    result = _capture_mmi_source_at_root(
        root,
        role=role,
        expected_source_sha256=hashlib.sha256(raw).hexdigest(),
    )

    assert mutated
    assert result.status is MmiProjectionResultCategory.PROJECTION_BLOCKED
    assert result.reason_codes == ("MMI_SOURCE_PATH_UNSTABLE",)
    assert result.source is None
    assert result.authority_effect == "NONE"
    assert sorted(opened) == sorted(closed)
    assert len(closed) == len(set(closed))


@pytest.mark.parametrize(
    "role",
    (
        MmiSourceRole.STRATEGY_SETTINGS,
        MmiSourceRole.PORTFOLIO_SNAPSHOT,
    ),
)
@pytest.mark.parametrize(
    "read_case",
    ("during-read-mutation", "short", "overlong", "error"),
)
def test_final_openat2_descriptor_read_failures_are_all_or_none(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    role: MmiSourceRole,
    read_case: str,
) -> None:
    raw = (
        b"A" * (source_capture._READ_CHUNK_BYTES + 17)
        if read_case == "during-read-mutation"
        else b"value: exact\n"
    )
    mutated = b"B" * len(raw)
    leaf = _install_role_source(tmp_path, role, raw)
    final_descriptor: int | None = None
    final_read_calls = 0
    original_read = source_capture.os.read

    def remember_final_descriptor(file_fd: int) -> None:
        nonlocal final_descriptor
        final_descriptor = file_fd

    def controlled_read(file_fd: int, count: int) -> bytes:
        nonlocal final_read_calls
        if file_fd != final_descriptor:
            return original_read(file_fd, count)
        final_read_calls += 1
        if read_case == "short":
            return b""
        if read_case == "overlong":
            return raw + b"x"
        if read_case == "error":
            raise OSError(errno.EIO, "injected final read error")
        observed = original_read(file_fd, count)
        if final_read_calls == 1:
            leaf.write_bytes(mutated)
        return observed

    opened, closed = _track_capture_descriptors(
        monkeypatch,
        after_openat2=remember_final_descriptor,
    )
    monkeypatch.setattr(source_capture.os, "read", controlled_read)

    result = _capture_mmi_source_at_root(
        tmp_path,
        role=role,
        expected_source_sha256=hashlib.sha256(raw).hexdigest(),
    )

    assert final_descriptor is not None
    assert final_read_calls >= 1
    assert result.status is MmiProjectionResultCategory.PROJECTION_BLOCKED
    assert result.reason_codes == ("MMI_SOURCE_UNSTABLE",)
    assert result.source is None
    assert result.authority_effect == "NONE"
    assert sorted(opened) == sorted(closed)
    assert len(closed) == len(set(closed))


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


def test_absence_prover_confirms_a_missing_leaf_under_an_intact_checkout(
    tmp_path: Path,
) -> None:
    # A valid checkout with validating markers and an intact ``inputs/current``
    # parent chain, where only the portfolio leaf is absent.
    module = _install_checkout(tmp_path, b"value: exact\n")
    leaf = tmp_path / "inputs" / "current" / "portfolio_snapshot.txt"
    assert not leaf.exists()

    result = _capture_mmi_source_absence_at_root(
        tmp_path,
        role=MmiSourceRole.PORTFOLIO_SNAPSHOT,
        _production_module_path=module,
    )

    assert result.status is MmiProjectionResultCategory.PROJECTION_VALID_COMPLETE
    assert result.reason_codes == ("MMI_SOURCE_CONFIRMED_ABSENT",)
    assert result.valid
    assert result.source is None
    assert result.authority_effect == "NONE"
    # Proving absence captures nothing and writes nothing.
    assert not leaf.exists()


def test_absence_prover_rejects_a_missing_intermediate_parent(
    tmp_path: Path,
) -> None:
    module = _install_checkout(tmp_path, b"value: exact\n")
    (tmp_path / "inputs" / "current" / "strategy_settings.yaml").unlink()

    def _absence():
        return _capture_mmi_source_absence_at_root(
            tmp_path,
            role=MmiSourceRole.PORTFOLIO_SNAPSHOT,
            _production_module_path=module,
        )

    def _present_source():
        return _capture_mmi_source_at_root(
            tmp_path,
            role=MmiSourceRole.PORTFOLIO_SNAPSHOT,
            expected_source_sha256="0" * 64,
            _production_module_path=module,
        )

    # Baseline: the leaf is absent but every parent component is intact.
    assert _absence().reason_codes == ("MMI_SOURCE_CONFIRMED_ABSENT",)
    intact_parents_present_source = _present_source().reason_codes

    # Only the innermost required directory is removed.
    (tmp_path / "inputs" / "current").rmdir()
    inner_missing = _absence()
    inner_missing_present_source = _present_source().reason_codes

    # Both required directories are removed.
    (tmp_path / "inputs").rmdir()
    outer_missing = _absence()

    for blocked in (inner_missing, outer_missing):
        assert blocked.status is MmiProjectionResultCategory.PROJECTION_BLOCKED
        assert blocked.reason_codes == ("MMI_SOURCE_PARENT_DIRECTORY_MISSING",)
        assert "MMI_SOURCE_CONFIRMED_ABSENT" not in blocked.reason_codes
        assert not blocked.valid
        assert blocked.source is None

    # The rejected sentinel approach cannot make this distinction: expected-SHA
    # capture reports the identical MMI_SOURCE_MISSING code whether the parent
    # chain is intact or broken, which is why absence needs its own prover.
    assert intact_parents_present_source == ("MMI_SOURCE_MISSING",)
    assert inner_missing_present_source == ("MMI_SOURCE_MISSING",)


def test_absence_prover_rejects_a_leaf_present_at_any_observation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _install_checkout(tmp_path, b"value: exact\n")
    leaf = tmp_path / "inputs" / "current" / "portfolio_snapshot.txt"

    def _absence():
        return _capture_mmi_source_absence_at_root(
            tmp_path,
            role=MmiSourceRole.PORTFOLIO_SNAPSHOT,
            _production_module_path=module,
        )

    # A leaf that is present before the observation begins.
    leaf.write_bytes(b"ticker,budget\n")
    already_present = _absence()
    leaf.unlink()

    # A leaf that appears after the first observation saw genuine absence.  The
    # real path verification still runs; creating the leaf inside it proves the
    # second observation is load-bearing, because the first one alone would have
    # returned confirmed absence.
    original_verify = source_capture._verify_complete_opened_path
    observed_first_absence: list[bool] = []

    def _verify_then_create(root_anchor, opened_components):
        observed_first_absence.append(not leaf.exists())
        original_verify(root_anchor, opened_components)
        leaf.write_bytes(b"ticker,budget\n")

    monkeypatch.setattr(
        source_capture,
        "_verify_complete_opened_path",
        _verify_then_create,
    )
    appeared_mid_observation = _absence()

    assert observed_first_absence == [True]
    for blocked in (already_present, appeared_mid_observation):
        assert blocked.status is MmiProjectionResultCategory.PROJECTION_BLOCKED
        assert blocked.reason_codes == ("MMI_SOURCE_PRESENT",)
        assert "MMI_SOURCE_CONFIRMED_ABSENT" not in blocked.reason_codes
        assert not blocked.valid
        assert blocked.source is None


def test_absence_prover_rejects_missing_and_content_invalid_markers(
    tmp_path: Path,
) -> None:
    """A broken production checkout is never reported as confirmed absence.

    Both marker phases are proven: a marker that cannot be acquired at all, and
    a marker that is acquired but whose bytes no longer validate.  The second
    case is the one that proves the absence prover really reads marker content
    rather than merely opening the marker path.
    """
    raw = b"value: exact\n"

    missing_checkout = tmp_path / "missing-marker"
    missing_module = _install_checkout(missing_checkout, raw)
    (missing_checkout / "pyproject.toml").unlink()
    missing = (
        source_capture._capture_current_mmi_source_absence_from_module_path(
            missing_module,
            MmiSourceRole.PORTFOLIO_SNAPSHOT,
        )
    )

    invalid_checkout = tmp_path / "content-invalid-marker"
    invalid_module = _install_checkout(invalid_checkout, raw)
    (invalid_checkout / "pyproject.toml").write_bytes(
        b'[project]\nname = "not-investment-orchestrator"\n'
    )
    invalid = (
        source_capture._capture_current_mmi_source_absence_from_module_path(
            invalid_module,
            MmiSourceRole.PORTFOLIO_SNAPSHOT,
        )
    )

    # Both checkouts hold a genuinely absent portfolio leaf, so an unproven
    # marker is the only thing standing between them and confirmed absence.
    assert not (missing_checkout / "inputs/current/portfolio_snapshot.txt").exists()
    assert not (invalid_checkout / "inputs/current/portfolio_snapshot.txt").exists()
    for blocked in (missing, invalid):
        assert blocked.status is MmiProjectionResultCategory.PROJECTION_BLOCKED
        assert blocked.reason_codes == (
            "MMI_SOURCE_REPOSITORY_MARKER_INVALID",
        )
        assert "MMI_SOURCE_CONFIRMED_ABSENT" not in blocked.reason_codes
        assert not blocked.valid
        assert blocked.source is None


def test_absence_prover_rejects_an_unbound_production_module_path(
    tmp_path: Path,
) -> None:
    """An intact checkout stays untrusted unless it holds the executing module.

    The lexical module path must witness exactly the module file opened under
    the walked repository root.  A byte-identical file at another path, and a
    module path that cannot be observed at all, both fail closed rather than
    returning confirmed absence.
    """
    module = _install_checkout(tmp_path, b"value: exact\n")
    leaf = tmp_path / "inputs" / "current" / "portfolio_snapshot.txt"
    assert not leaf.exists()

    def _absence(module_path: Path):
        return _capture_mmi_source_absence_at_root(
            tmp_path,
            role=MmiSourceRole.PORTFOLIO_SNAPSHOT,
            _production_module_path=module_path,
        )

    # Control: this same intact checkout confirms absence when the module path
    # is the module actually opened under the walked root.
    assert _absence(module).reason_codes == ("MMI_SOURCE_CONFIRMED_ABSENT",)

    # A byte-identical decoy is still a different file.
    decoy = tmp_path / "decoy_source_capture.py"
    decoy.write_bytes(module.read_bytes())
    foreign = _absence(decoy)
    unobservable = _absence(tmp_path / "absent_source_capture.py")

    for blocked in (foreign, unobservable):
        assert blocked.status is MmiProjectionResultCategory.PROJECTION_BLOCKED
        assert blocked.reason_codes == (
            "MMI_SOURCE_REPOSITORY_ROOT_UNTRUSTED",
        )
        assert "MMI_SOURCE_CONFIRMED_ABSENT" not in blocked.reason_codes
        assert not blocked.valid
        assert blocked.source is None


def test_absence_prover_closes_every_descriptor_on_a_controlled_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Every descriptor the absence prover opens is closed exactly once.

    The controlled failure is taken late, after the filesystem root, every
    production marker, and the required parent chain are already open, so the
    closure claim covers the full retained set rather than a trivial prefix.
    """
    module = _install_checkout(tmp_path, b"value: exact\n")
    leaf = tmp_path / "inputs" / "current" / "portfolio_snapshot.txt"
    leaf.write_bytes(b"ticker,budget\n")

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
    result = _capture_mmi_source_absence_at_root(
        tmp_path,
        role=MmiSourceRole.PORTFOLIO_SNAPSHOT,
        _production_module_path=module,
    )

    # Reaching MMI_SOURCE_PRESENT proves the root, marker, and parent
    # descriptors were all opened before the controlled failure was taken.
    assert result.reason_codes == ("MMI_SOURCE_PRESENT",)
    assert opened
    assert sorted(opened) == sorted(closed)
    assert closed == list(reversed(opened))


def _install_lh2_checkout(root: Path, raw: bytes) -> tuple[Path, Path]:
    """Install a trusted checkout whose long-horizon research leaf holds ``raw``."""
    module = _install_checkout(root, b"as_of: '2026-07-25'\n")
    leaf = (
        root
        / MMI_SOURCE_CATALOG[
            MmiSourceRole.LONG_HORIZON_RESEARCH
        ].repository_relative_locator
    )
    leaf.write_bytes(raw)
    return module, leaf


def _stable_digest(root: Path, module: Path):
    return source_capture._capture_mmi_stable_source_digest_at_root(
        root,
        role=MmiSourceRole.LONG_HORIZON_RESEARCH,
        _production_module_path=module,
    )


def test_stable_digest_binds_the_fixed_catalog_source_and_its_exact_bytes(
    tmp_path: Path,
) -> None:
    """The digest describes the code-owned leaf and nothing else.

    The operation accepts only a role, so no caller can redirect it at another
    file, and both persisted values describe the same exact captured bytes.
    """
    raw = b'{"declared": "bytes", "never": "parsed"}\n'
    module, leaf = _install_lh2_checkout(tmp_path, raw)

    digest = _stable_digest(tmp_path, module)

    assert digest.role is MmiSourceRole.LONG_HORIZON_RESEARCH
    assert digest.observed_sha256 == hashlib.sha256(raw).hexdigest()
    assert digest.observed_size_bytes == len(raw)
    assert leaf.read_bytes() == raw

    # A decoy elsewhere in the checkout cannot be selected: there is no source
    # argument, and the locator is owned by the closed catalog.
    (tmp_path / "decoy_long_horizon_research.json").write_bytes(b"decoy\n")
    (tmp_path / "inputs" / "decoy.json").write_bytes(b"decoy\n")
    assert _stable_digest(tmp_path, module) == digest

    # The returned value is exactly three fields: no raw bytes, no source
    # record, no captured source, no expected digest, no status or authority.
    assert tuple(field.name for field in fields(digest)) == (
        "role",
        "observed_sha256",
        "observed_size_bytes",
    )


def test_stable_digest_fails_closed_when_the_source_changes_between_samples(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A same-inode, same-length rewrite between the two samples fails closed.

    Identity and size checks alone cannot see this mutation, so passing proves
    the byte-equality oracle is load-bearing for the manual digest exactly as
    it is for bound capture.
    """
    raw = b'{"sample": "first"}\n'
    mutated_raw = b'{"sample": "secnd"}\n'
    assert len(mutated_raw) == len(raw) and mutated_raw != raw
    module, leaf = _install_lh2_checkout(tmp_path, raw)
    mutated = False

    def mutate() -> None:
        nonlocal mutated
        leaf.write_bytes(mutated_raw)
        mutated = True

    opened, closed = _track_capture_descriptors(
        monkeypatch,
        before_openat2=mutate,
    )

    with pytest.raises(source_capture.MmiStableSourceDigestError) as caught:
        _stable_digest(tmp_path, module)

    assert mutated
    assert caught.value.code == "MMI_SOURCE_UNSTABLE"
    assert sorted(opened) == sorted(closed)


def test_stable_digest_fails_closed_on_path_substitution_and_closes_descriptors(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A byte-identical replacement at the fixed path is still not the source.

    The substituted leaf carries the same bytes but a different identity, so
    the digest fails closed, and every descriptor opened before the controlled
    failure is closed exactly once.
    """
    raw = b'{"bound": "path"}\n'
    module, leaf = _install_lh2_checkout(tmp_path, raw)
    substituted = False

    def substitute() -> None:
        nonlocal substituted
        current = leaf.parent
        current.rename(current.with_name("current.detached"))
        replacement = current
        replacement.mkdir()
        (replacement / leaf.name).write_bytes(raw)
        substituted = True

    opened, closed = _track_capture_descriptors(
        monkeypatch,
        before_openat2=substitute,
    )

    with pytest.raises(source_capture.MmiStableSourceDigestError) as caught:
        _stable_digest(tmp_path, module)

    assert substituted
    assert caught.value.code == "MMI_SOURCE_PATH_UNSTABLE"
    assert opened
    assert sorted(opened) == sorted(closed)
    assert len(closed) == len(set(closed))


def test_nullable_expected_sha_never_reaches_the_bound_capture_contract(
    tmp_path: Path,
) -> None:
    """Bound capture still requires, compares, and binds an expected digest.

    The manual digest caller is the only one that may omit an expected SHA;
    the bound contract rejects an absent one before any descriptor is opened,
    still fails the early mismatch, and still authenticates a matching one.
    """
    raw = b"as_of: '2026-07-25'\n"
    module = _install_checkout(tmp_path, raw)

    for absent in (None, ""):
        required = _capture_mmi_source_at_root(
            tmp_path,
            role=MmiSourceRole.STRATEGY_SETTINGS,
            expected_source_sha256=absent,
            _production_module_path=module,
        )
        assert required.status is (
            MmiProjectionResultCategory.PROJECTION_BLOCKED
        )
        assert required.reason_codes == (
            "MMI_SOURCE_EXPECTED_SHA256_REQUIRED",
        )
        assert required.source is None

    mismatch = _capture_mmi_source_at_root(
        tmp_path,
        role=MmiSourceRole.STRATEGY_SETTINGS,
        expected_source_sha256="0" * 64,
        _production_module_path=module,
    )
    assert mismatch.reason_codes == ("MMI_SOURCE_EXPECTED_SHA256_MISMATCH",)
    assert mismatch.source is None

    bound = _capture_mmi_source_at_root(
        tmp_path,
        role=MmiSourceRole.STRATEGY_SETTINGS,
        expected_source_sha256=hashlib.sha256(raw).hexdigest(),
        _production_module_path=module,
    )
    assert bound.status is MmiProjectionResultCategory.PROJECTION_VALID_COMPLETE
    assert bound.source is not None
    assert bound.source.raw_bytes == raw
    assert bound.source.source_record["expected_sha256"] == (
        bound.source.source_record["observed_sha256"]
    )
    assert _mmi_captured_source_provenance_is_valid(bound.source)
