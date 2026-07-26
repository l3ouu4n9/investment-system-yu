"""Bounded no-follow capture for closed MMI local source roles."""

from __future__ import annotations

from dataclasses import dataclass
import errno
import hashlib
import os
from pathlib import Path
import re
import stat
from typing import Final

from investment_orchestrator.common.schema_validation import (
    validate_artifact_schema,
)
from investment_orchestrator.mmi.canonical import (
    MMI_SOURCE_RECORD_IDENTITY_DOMAIN,
    record_identity_sha256,
)
from investment_orchestrator.mmi.contracts import (
    AUTHORITY_EFFECT_NONE,
    MMI_SOURCE_CATALOG,
    MmiCapturedSource,
    MmiProjectionResultCategory,
    MmiSourceCaptureResult,
    MmiSourceRole,
    MmiSourceSpec,
    _create_mmi_captured_source,
)


_SHA256_RE: Final = re.compile(r"^[0-9a-f]{64}$")
_READ_CHUNK_BYTES: Final = 65_536
_SOURCE_RECORD_MAXIMUM_CANONICAL_BYTES: Final = 8_192
_PRODUCTION_MODULE_SUFFIX: Final = (
    "src",
    "investment_orchestrator",
    "mmi",
    "source_capture.py",
)
_PRODUCTION_MODULE_FILE: Final = __file__


@dataclass(frozen=True, slots=True)
class _FileWitness:
    device: int
    inode: int
    mode: int
    size: int
    modification_time_ns: int
    change_time_ns: int


@dataclass(frozen=True, slots=True)
class _OpenedComponent:
    parent_fd: int
    name: str
    opened_fd: int
    witness: _FileWitness
    expected_kind: str
    unstable_code: str


@dataclass(frozen=True, slots=True)
class _RootAnchor:
    opened_fd: int
    witness: _FileWitness


@dataclass(frozen=True, slots=True)
class _MarkerSpec:
    path_components: tuple[str, ...]
    maximum_bytes: int
    required_fragments: tuple[bytes, ...]


_PRODUCTION_MARKERS: Final = (
    _MarkerSpec(
        path_components=("pyproject.toml",),
        maximum_bytes=262_144,
        required_fragments=(
            b"[project]",
            b'name = "investment-orchestrator"',
        ),
    ),
    _MarkerSpec(
        path_components=("src", "investment_orchestrator", "__init__.py"),
        maximum_bytes=65_536,
        required_fragments=(
            b'"""investment_orchestrator package."""',
        ),
    ),
    _MarkerSpec(
        path_components=_PRODUCTION_MODULE_SUFFIX,
        maximum_bytes=1_048_576,
        required_fragments=(
            b"def capture_current_mmi_source(",
            b"_PRODUCTION_MODULE_SUFFIX",
        ),
    ),
)


class _CaptureFailure(RuntimeError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class _CaptureContractFailure(RuntimeError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def _capture_result(
    status: MmiProjectionResultCategory,
    *reason_codes: str,
    source: MmiCapturedSource | None = None,
) -> MmiSourceCaptureResult:
    return MmiSourceCaptureResult(
        status=status,
        authority_effect=AUTHORITY_EFFECT_NONE,
        reason_codes=tuple(reason_codes),
        source=source,
    )


def _required_filesystem_primitives_available() -> bool:
    required_flags = ("O_DIRECTORY", "O_CLOEXEC", "O_NOFOLLOW")
    if any(not hasattr(os, name) for name in required_flags):
        return False
    if os.open not in os.supports_dir_fd or os.stat not in os.supports_dir_fd:
        return False
    if os.stat not in os.supports_follow_symlinks:
        return False
    return hasattr(os.stat_result, "st_mtime_ns") and hasattr(
        os.stat_result, "st_ctime_ns"
    )


def _witness(value: os.stat_result) -> _FileWitness:
    try:
        return _FileWitness(
            device=value.st_dev,
            inode=value.st_ino,
            mode=value.st_mode,
            size=value.st_size,
            modification_time_ns=value.st_mtime_ns,
            change_time_ns=value.st_ctime_ns,
        )
    except (AttributeError, TypeError, ValueError):
        raise _CaptureFailure(
            "MMI_SOURCE_FILESYSTEM_PRIMITIVES_UNAVAILABLE"
        ) from None


def _same_file_identity(
    first: _FileWitness,
    second: _FileWitness,
) -> bool:
    return (
        first.device == second.device
        and first.inode == second.inode
    )


def _entry_stat(
    name: str,
    *,
    directory_fd: int,
    missing_code: str = "MMI_SOURCE_MISSING",
) -> os.stat_result:
    try:
        return os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
    except OSError as exc:
        if exc.errno == errno.ENOENT:
            raise _CaptureFailure(missing_code) from None
        if exc.errno in {errno.ELOOP, errno.EMLINK}:
            raise _CaptureFailure("MMI_SOURCE_SYMLINK_REJECTED") from None
        raise _CaptureFailure("MMI_SOURCE_UNREADABLE") from None


def _open_relative(
    name: str,
    *,
    directory_fd: int,
    flags: int,
    unstable_code: str,
) -> int:
    try:
        return os.open(name, flags, dir_fd=directory_fd)
    except OSError as exc:
        if exc.errno in {
            errno.ENOENT,
            errno.ENOTDIR,
            errno.ELOOP,
            errno.EMLINK,
        }:
            raise _CaptureFailure(unstable_code) from None
        raise _CaptureFailure("MMI_SOURCE_UNREADABLE") from None


def _open_root(repository_root: Path, flags: int) -> int:
    try:
        return os.open(os.fspath(repository_root), flags)
    except OSError as exc:
        if exc.errno in {
            errno.ELOOP,
            errno.EMLINK,
            errno.ENOTDIR,
        }:
            raise _CaptureFailure("MMI_SOURCE_SYMLINK_REJECTED") from None
        raise _CaptureFailure(
            "MMI_SOURCE_REPOSITORY_ROOT_UNAVAILABLE"
        ) from None


def _fstat(file_fd: int) -> os.stat_result:
    try:
        return os.fstat(file_fd)
    except OSError:
        raise _CaptureFailure("MMI_SOURCE_UNREADABLE") from None


def _read_exact_bounded(file_fd: int, *, expected_size: int) -> bytes:
    remaining = expected_size + 1
    chunks: list[bytes] = []
    observed = 0
    while remaining:
        try:
            chunk = os.read(file_fd, min(_READ_CHUNK_BYTES, remaining))
        except OSError:
            raise _CaptureFailure("MMI_SOURCE_UNREADABLE") from None
        if not chunk:
            break
        chunks.append(chunk)
        observed += len(chunk)
        remaining -= len(chunk)
    if observed > expected_size:
        raise _CaptureFailure("MMI_SOURCE_OVERLONG_READ")
    if observed < expected_size:
        raise _CaptureFailure("MMI_SOURCE_SHORT_READ")
    return b"".join(chunks)


def _open_directory_component(
    parent_fd: int,
    name: str,
    *,
    directory_flags: int,
    descriptors: list[int],
    opened_components: list[_OpenedComponent],
    missing_code: str = "MMI_SOURCE_MISSING",
) -> int:
    entry = _entry_stat(
        name,
        directory_fd=parent_fd,
        missing_code=missing_code,
    )
    if stat.S_ISLNK(entry.st_mode):
        raise _CaptureFailure("MMI_SOURCE_SYMLINK_REJECTED")
    if not stat.S_ISDIR(entry.st_mode):
        raise _CaptureFailure(
            "MMI_SOURCE_INTERMEDIATE_COMPONENT_NOT_DIRECTORY"
        )
    opened_fd = _open_relative(
        name,
        directory_fd=parent_fd,
        flags=directory_flags,
        unstable_code="MMI_SOURCE_PATH_UNSTABLE",
    )
    descriptors.append(opened_fd)
    opened = _fstat(opened_fd)
    if not stat.S_ISDIR(opened.st_mode):
        raise _CaptureFailure(
            "MMI_SOURCE_INTERMEDIATE_COMPONENT_NOT_DIRECTORY"
        )
    entry_witness = _witness(entry)
    opened_witness = _witness(opened)
    if entry_witness != opened_witness:
        raise _CaptureFailure("MMI_SOURCE_PATH_UNSTABLE")
    opened_components.append(
        _OpenedComponent(
            parent_fd=parent_fd,
            name=name,
            opened_fd=opened_fd,
            witness=opened_witness,
            expected_kind="DIRECTORY",
            unstable_code="MMI_SOURCE_PATH_UNSTABLE",
        )
    )
    return opened_fd


def _open_regular_component(
    parent_fd: int,
    name: str,
    *,
    leaf_flags: int,
    maximum_bytes: int,
    descriptors: list[int],
    opened_components: list[_OpenedComponent],
    missing_code: str = "MMI_SOURCE_MISSING",
    unstable_code: str = "MMI_SOURCE_PATH_UNSTABLE",
) -> tuple[int, _FileWitness]:
    entry = _entry_stat(
        name,
        directory_fd=parent_fd,
        missing_code=missing_code,
    )
    if stat.S_ISLNK(entry.st_mode):
        raise _CaptureFailure("MMI_SOURCE_SYMLINK_REJECTED")
    if not stat.S_ISREG(entry.st_mode):
        raise _CaptureFailure("MMI_SOURCE_NOT_REGULAR_FILE")
    entry_witness = _witness(entry)
    if entry_witness.size < 0:
        raise _CaptureFailure("MMI_SOURCE_SIZE_INVALID")
    if entry_witness.size > maximum_bytes:
        raise _CaptureFailure("MMI_SOURCE_OVERSIZED")
    opened_fd = _open_relative(
        name,
        directory_fd=parent_fd,
        flags=leaf_flags,
        unstable_code=unstable_code,
    )
    descriptors.append(opened_fd)
    opened = _fstat(opened_fd)
    if not stat.S_ISREG(opened.st_mode):
        raise _CaptureFailure("MMI_SOURCE_NOT_REGULAR_FILE")
    opened_witness = _witness(opened)
    if entry_witness != opened_witness:
        if (
            unstable_code == "MMI_SOURCE_UNSTABLE"
            and _same_file_identity(entry_witness, opened_witness)
        ):
            raise _CaptureFailure("MMI_SOURCE_UNSTABLE")
        raise _CaptureFailure("MMI_SOURCE_PATH_UNSTABLE")
    opened_components.append(
        _OpenedComponent(
            parent_fd=parent_fd,
            name=name,
            opened_fd=opened_fd,
            witness=opened_witness,
            expected_kind="REGULAR_FILE",
            unstable_code=unstable_code,
        )
    )
    return opened_fd, opened_witness


def _open_absolute_repository_root(
    repository_root: Path,
    *,
    directory_flags: int,
    descriptors: list[int],
    opened_components: list[_OpenedComponent],
) -> tuple[_RootAnchor, int]:
    if not repository_root.is_absolute():
        raise _CaptureFailure("MMI_SOURCE_REPOSITORY_ROOT_UNAVAILABLE")
    parts = repository_root.parts
    if not parts or parts[0] != os.path.sep:
        raise _CaptureFailure("MMI_SOURCE_REPOSITORY_ROOT_UNAVAILABLE")
    root_fd = _open_root(Path(os.path.sep), directory_flags)
    descriptors.append(root_fd)
    try:
        root_entry = os.stat(os.path.sep, follow_symlinks=False)
    except OSError:
        raise _CaptureFailure(
            "MMI_SOURCE_REPOSITORY_ROOT_UNAVAILABLE"
        ) from None
    root_opened = _fstat(root_fd)
    root_witness = _witness(root_opened)
    if (
        not stat.S_ISDIR(root_entry.st_mode)
        or not stat.S_ISDIR(root_opened.st_mode)
        or _witness(root_entry) != root_witness
    ):
        raise _CaptureFailure("MMI_SOURCE_PATH_UNSTABLE")
    anchor = _RootAnchor(opened_fd=root_fd, witness=root_witness)
    current_fd = root_fd
    for component in parts[1:]:
        if component in {"", ".", ".."}:
            raise _CaptureFailure("MMI_SOURCE_REPOSITORY_ROOT_UNAVAILABLE")
        current_fd = _open_directory_component(
            current_fd,
            component,
            directory_flags=directory_flags,
            descriptors=descriptors,
            opened_components=opened_components,
            missing_code="MMI_SOURCE_REPOSITORY_ROOT_UNAVAILABLE",
        )
    return anchor, current_fd


def _open_fixed_regular_path(
    root_fd: int,
    path_components: tuple[str, ...],
    *,
    maximum_bytes: int,
    directory_flags: int,
    leaf_flags: int,
    descriptors: list[int],
    opened_components: list[_OpenedComponent],
    missing_code: str,
    unstable_code: str = "MMI_SOURCE_PATH_UNSTABLE",
) -> tuple[int, _FileWitness]:
    if not path_components or any(
        component in {"", ".", ".."} or "/" in component
        for component in path_components
    ):
        raise _CaptureFailure("MMI_SOURCE_INTERNAL_INVARIANT_FAILED")
    current_fd = root_fd
    for component in path_components[:-1]:
        current_fd = _open_directory_component(
            current_fd,
            component,
            directory_flags=directory_flags,
            descriptors=descriptors,
            opened_components=opened_components,
            missing_code=missing_code,
        )
    return _open_regular_component(
        current_fd,
        path_components[-1],
        leaf_flags=leaf_flags,
        maximum_bytes=maximum_bytes,
        descriptors=descriptors,
        opened_components=opened_components,
        missing_code=missing_code,
        unstable_code=unstable_code,
    )


def _verify_complete_opened_path(
    root_anchor: _RootAnchor,
    opened_components: list[_OpenedComponent],
    *,
    source_content_stable: bool,
) -> None:
    path_unstable = False
    source_unstable = not source_content_stable
    try:
        root_entry = os.stat(os.path.sep, follow_symlinks=False)
    except OSError:
        root_entry = None
        path_unstable = True
    try:
        root_opened = _fstat(root_anchor.opened_fd)
    except _CaptureFailure:
        root_opened = None
        path_unstable = True
    if root_entry is not None and root_opened is not None:
        if (
            _witness(root_entry) != root_anchor.witness
            or _witness(root_opened) != root_anchor.witness
        ):
            path_unstable = True
    for component in opened_components:
        try:
            entry = os.stat(
                component.name,
                dir_fd=component.parent_fd,
                follow_symlinks=False,
            )
        except OSError:
            entry = None
            path_unstable = True
        try:
            opened = _fstat(component.opened_fd)
        except _CaptureFailure:
            opened = None
            path_unstable = True
        if entry is None or opened is None:
            continue
        if stat.S_ISLNK(entry.st_mode):
            path_unstable = True
            continue
        if component.expected_kind == "DIRECTORY":
            kind_valid = stat.S_ISDIR(entry.st_mode) and stat.S_ISDIR(
                opened.st_mode
            )
        else:
            kind_valid = stat.S_ISREG(entry.st_mode) and stat.S_ISREG(
                opened.st_mode
            )
        if not kind_valid:
            path_unstable = True
            continue
        entry_witness = _witness(entry)
        opened_witness = _witness(opened)
        if component.unstable_code != "MMI_SOURCE_UNSTABLE":
            if (
                entry_witness != component.witness
                or opened_witness != component.witness
            ):
                path_unstable = True
            continue
        if (
            not _same_file_identity(entry_witness, component.witness)
            or not _same_file_identity(
                opened_witness,
                component.witness,
            )
            or not _same_file_identity(entry_witness, opened_witness)
        ):
            path_unstable = True
        elif (
            entry_witness != component.witness
            or opened_witness != component.witness
        ):
            source_unstable = True
    if path_unstable:
        raise _CaptureFailure("MMI_SOURCE_PATH_UNSTABLE")
    if source_unstable:
        raise _CaptureFailure("MMI_SOURCE_UNSTABLE")


def _source_leaf_content_is_stable(
    leaf_fd: int,
    *,
    expected_size: int,
    captured: bytes,
) -> bool:
    try:
        os.lseek(leaf_fd, 0, os.SEEK_SET)
        observed = _read_exact_bounded(
            leaf_fd,
            expected_size=expected_size,
        )
    except (OSError, _CaptureFailure):
        return False
    return observed == captured


def _validate_marker_bytes(spec: _MarkerSpec, raw_bytes: bytes) -> None:
    if not raw_bytes or any(
        fragment not in raw_bytes for fragment in spec.required_fragments
    ):
        raise _CaptureFailure("MMI_SOURCE_REPOSITORY_MARKER_INVALID")


def _lexical_production_checkout(
    module_file: str | Path,
) -> tuple[Path, Path]:
    try:
        raw = os.fspath(module_file)
    except TypeError:
        raise _CaptureFailure(
            "MMI_SOURCE_PRODUCTION_LAYOUT_UNSUPPORTED"
        ) from None
    if (
        type(raw) is not str
        or not os.path.isabs(raw)
        or "\x00" in raw
        or os.path.normpath(raw) != raw
    ):
        raise _CaptureFailure(
            "MMI_SOURCE_PRODUCTION_LAYOUT_UNSUPPORTED"
        )
    module_path = Path(raw)
    if tuple(module_path.parts[-len(_PRODUCTION_MODULE_SUFFIX) :]) != (
        _PRODUCTION_MODULE_SUFFIX
    ):
        raise _CaptureFailure(
            "MMI_SOURCE_PRODUCTION_LAYOUT_UNSUPPORTED"
        )
    repository_root = module_path
    for _component in _PRODUCTION_MODULE_SUFFIX:
        repository_root = repository_root.parent
    if not repository_root.is_absolute():
        raise _CaptureFailure(
            "MMI_SOURCE_PRODUCTION_LAYOUT_UNSUPPORTED"
        )
    return repository_root, module_path


def _capture_fixed_source_bytes(
    repository_root: Path,
    *,
    spec: MmiSourceSpec,
    expected_source_sha256: str,
    production_module_path: Path | None = None,
) -> MmiSourceCaptureResult:
    if not _required_filesystem_primitives_available():
        raise _CaptureFailure("MMI_SOURCE_FILESYSTEM_PRIMITIVES_UNAVAILABLE")
    directory_flags = (
        os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC | os.O_NOFOLLOW
    )
    leaf_flags = os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW
    descriptors: list[int] = []
    opened_components: list[_OpenedComponent] = []
    success: MmiSourceCaptureResult | None = None
    pending_failure: _CaptureFailure | _CaptureContractFailure | None = None
    try:
        root_anchor, repository_root_fd = _open_absolute_repository_root(
            repository_root,
            directory_flags=directory_flags,
            descriptors=descriptors,
            opened_components=opened_components,
        )
        marker_materials: list[
            tuple[_MarkerSpec, int, _FileWitness]
        ] = []
        module_component: _OpenedComponent | None = None
        if production_module_path is not None:
            for marker_spec in _PRODUCTION_MARKERS:
                marker_fd, marker_witness = _open_fixed_regular_path(
                    repository_root_fd,
                    marker_spec.path_components,
                    maximum_bytes=marker_spec.maximum_bytes,
                    directory_flags=directory_flags,
                    leaf_flags=leaf_flags,
                    descriptors=descriptors,
                    opened_components=opened_components,
                    missing_code="MMI_SOURCE_REPOSITORY_MARKER_INVALID",
                )
                marker_materials.append(
                    (marker_spec, marker_fd, marker_witness)
                )
                if marker_spec.path_components == _PRODUCTION_MODULE_SUFFIX:
                    module_component = opened_components[-1]
            if module_component is None:
                raise _CaptureFailure(
                    "MMI_SOURCE_INTERNAL_INVARIANT_FAILED"
                )

        leaf_fd, leaf_witness = _open_fixed_regular_path(
            repository_root_fd,
            spec.path_components,
            maximum_bytes=spec.maximum_bytes,
            directory_flags=directory_flags,
            leaf_flags=leaf_flags,
            descriptors=descriptors,
            opened_components=opened_components,
            missing_code="MMI_SOURCE_MISSING",
            unstable_code="MMI_SOURCE_UNSTABLE",
        )
        for marker_spec, marker_fd, marker_witness in marker_materials:
            marker_bytes = _read_exact_bounded(
                marker_fd,
                expected_size=marker_witness.size,
            )
            _validate_marker_bytes(marker_spec, marker_bytes)
        captured = _read_exact_bounded(
            leaf_fd,
            expected_size=leaf_witness.size,
        )
        observed_sha256 = hashlib.sha256(captured).hexdigest()
        if observed_sha256 != expected_source_sha256:
            raise _CaptureFailure(
                "MMI_SOURCE_EXPECTED_SHA256_MISMATCH"
            )
        try:
            record = _build_source_record(
                spec,
                raw_bytes=captured,
                expected_source_sha256=expected_source_sha256,
            )
            validate_artifact_schema(
                record,
                schema_name="mmi_source_record_v1.schema.json",
            )
        except Exception:
            raise _CaptureContractFailure(
                "MMI_SOURCE_RECORD_CONTRACT_FAILURE"
            ) from None
        if production_module_path is not None:
            if module_component is None:
                raise _CaptureFailure(
                    "MMI_SOURCE_INTERNAL_INVARIANT_FAILED"
                )
            try:
                module_lexical = os.stat(
                    os.fspath(production_module_path),
                    follow_symlinks=False,
                )
            except OSError:
                raise _CaptureFailure(
                    "MMI_SOURCE_REPOSITORY_ROOT_UNTRUSTED"
                ) from None
            if stat.S_ISLNK(module_lexical.st_mode):
                raise _CaptureFailure(
                    "MMI_SOURCE_REPOSITORY_ROOT_UNTRUSTED"
                )
            module_lexical_witness = _witness(module_lexical)
            if module_lexical_witness != module_component.witness:
                raise _CaptureFailure(
                    "MMI_SOURCE_REPOSITORY_ROOT_UNTRUSTED"
                )
        # This must remain the final pathname-based authenticity operation.
        # Every root, marker, intermediate, and source component is retained
        # open and revalidated descriptor-relative here.  After it succeeds,
        # only provenance sealing, in-memory result construction, and
        # descriptor closure are permitted.
        source_content_stable = _source_leaf_content_is_stable(
            leaf_fd,
            expected_size=leaf_witness.size,
            captured=captured,
        )
        _verify_complete_opened_path(
            root_anchor,
            opened_components,
            source_content_stable=source_content_stable,
        )
        try:
            source = _create_mmi_captured_source(
                role=spec.role,
                raw_bytes=captured,
                source_record=record,
            )
            success = _capture_result(
                MmiProjectionResultCategory.PROJECTION_VALID_COMPLETE,
                source=source,
            )
        except Exception:
            raise _CaptureContractFailure(
                "MMI_SOURCE_RECORD_CONTRACT_FAILURE"
            ) from None
    except _CaptureFailure as exc:
        pending_failure = exc
    except _CaptureContractFailure as exc:
        pending_failure = exc
    except (OSError, TypeError, ValueError):
        pending_failure = _CaptureFailure("MMI_SOURCE_UNREADABLE")
    finally:
        close_failed = False
        for descriptor in reversed(descriptors):
            try:
                os.close(descriptor)
            except OSError:
                close_failed = True
        if close_failed and pending_failure is None:
            pending_failure = _CaptureFailure(
                "MMI_SOURCE_DESCRIPTOR_CLOSE_FAILED"
            )
    if pending_failure is not None:
        raise pending_failure
    if success is None:
        raise _CaptureFailure("MMI_SOURCE_INTERNAL_INVARIANT_FAILED")
    return success


def _build_source_record(
    spec: MmiSourceSpec,
    *,
    raw_bytes: bytes,
    expected_source_sha256: str,
) -> dict[str, object]:
    observed_sha256 = hashlib.sha256(raw_bytes).hexdigest()
    record: dict[str, object] = {
        "schema_version": "mmi_source_record_v1",
        "source_role": spec.role.value,
        "source_id": spec.source_id,
        "repository_relative_locator": str(spec.repository_relative_locator),
        "maximum_bytes": spec.maximum_bytes,
        "observed_size_bytes": len(raw_bytes),
        "expected_sha256": expected_source_sha256,
        "observed_sha256": observed_sha256,
        "content_binding_status": "EXPECTED_SHA256_MATCHED",
        "operator_origin_authentication": "NOT_ESTABLISHED",
        "stable_read_status": "STABLE_BEFORE_AND_AFTER",
        "regular_file_status": "REGULAR_FILE",
        "authority_effect": AUTHORITY_EFFECT_NONE,
    }
    record["source_record_identity_sha256"] = record_identity_sha256(
        record,
        identity_field="source_record_identity_sha256",
        domain=MMI_SOURCE_RECORD_IDENTITY_DOMAIN,
        maximum_bytes=_SOURCE_RECORD_MAXIMUM_CANONICAL_BYTES,
    )
    return record


def _capture_mmi_source_at_root(
    repository_root: Path,
    *,
    role: MmiSourceRole,
    expected_source_sha256: str,
    _production_module_path: Path | None = None,
) -> MmiSourceCaptureResult:
    """Internal test surface retaining the production role/path contract."""
    if expected_source_sha256 is None or expected_source_sha256 == "":
        return _capture_result(
            MmiProjectionResultCategory.PROJECTION_BLOCKED,
            "MMI_SOURCE_EXPECTED_SHA256_REQUIRED",
        )
    if (
        type(expected_source_sha256) is not str
        or not _SHA256_RE.fullmatch(expected_source_sha256)
    ):
        return _capture_result(
            MmiProjectionResultCategory.PROJECTION_BLOCKED,
            "MMI_SOURCE_EXPECTED_SHA256_INVALID",
        )
    if type(role) is not MmiSourceRole:
        return _capture_result(
            MmiProjectionResultCategory.PROJECTION_BLOCKED,
            "MMI_SOURCE_ROLE_INVALID",
        )
    if role is not MmiSourceRole.STRATEGY_SETTINGS:
        return _capture_result(
            MmiProjectionResultCategory.PROJECTION_BLOCKED,
            "MMI_SOURCE_ROLE_NOT_AVAILABLE_IN_P1A",
        )
    if not isinstance(repository_root, Path):
        return _capture_result(
            MmiProjectionResultCategory.PROJECTION_BLOCKED,
            "MMI_SOURCE_REPOSITORY_ROOT_INVALID",
        )
    spec = MMI_SOURCE_CATALOG[role]
    try:
        return _capture_fixed_source_bytes(
            repository_root,
            spec=spec,
            expected_source_sha256=expected_source_sha256,
            production_module_path=_production_module_path,
        )
    except _CaptureFailure as exc:
        return _capture_result(
            MmiProjectionResultCategory.PROJECTION_BLOCKED,
            exc.code,
        )
    except _CaptureContractFailure as exc:
        return _capture_result(
            MmiProjectionResultCategory.PROJECTION_CONTRACT_FAILURE,
            exc.code,
        )


def _capture_current_mmi_source_from_module_path(
    module_file: str | Path,
    role: MmiSourceRole,
    *,
    expected_source_sha256: str,
) -> MmiSourceCaptureResult:
    try:
        repository_root, module_path = _lexical_production_checkout(
            module_file
        )
    except _CaptureFailure as exc:
        return _capture_result(
            MmiProjectionResultCategory.PROJECTION_BLOCKED,
            exc.code,
        )
    return _capture_mmi_source_at_root(
        repository_root,
        role=role,
        expected_source_sha256=expected_source_sha256,
        _production_module_path=module_path,
    )


def capture_current_mmi_source(
    role: MmiSourceRole,
    *,
    expected_source_sha256: str,
) -> MmiSourceCaptureResult:
    """Capture one exact code-owned current source with mandatory hash binding."""
    return _capture_current_mmi_source_from_module_path(
        _PRODUCTION_MODULE_FILE,
        role,
        expected_source_sha256=expected_source_sha256,
    )
