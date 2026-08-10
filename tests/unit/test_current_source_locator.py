"""Canonical CURRENT production input locator ownership.

These tests prove only what the locator owns: the authoritative lexical
production checkout root and the two canonical relative source paths.  They
deliberately do not re-test secure filesystem observation — ``openat2``, bounded
reads, raw hashing, expected-hash comparison and record provenance stay owned
and tested by ``tests/unit/test_mmi_source_capture.py``.
"""

from __future__ import annotations

import ast
import hashlib
from pathlib import Path, PurePosixPath

import pytest

from investment_orchestrator.common.paths import repo_root
from investment_orchestrator.mmi import source_capture
from investment_orchestrator.mmi.contracts import (
    MMI_SOURCE_CATALOG,
    MmiProjectionResultCategory,
    MmiSourceRole,
)
from investment_orchestrator.production_inputs import (
    current_source_locator as locator,
)


PROHIBITED_LOCATOR_IMPORT_PREFIXES = (
    "investment_orchestrator.mmi",
    "investment_orchestrator.workflow",
    "investment_orchestrator.state",
    "investment_orchestrator.permissions",
    "investment_orchestrator.research",
    "investment_orchestrator.orders",
    "investment_orchestrator.broker",
    "investment_orchestrator.observability",
    "ctypes",
)


def _locator_imports() -> tuple[str, ...]:
    tree = ast.parse(Path(locator.__file__).read_text(encoding="utf-8"))
    imported: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            imported.append(module)
            imported.extend(
                f"{module}.{alias.name}" for alias in node.names
            )
    return tuple(imported)


def test_canonical_current_relative_paths_are_exact() -> None:
    assert locator.STRATEGY_SETTINGS_PATH_COMPONENTS == (
        "inputs",
        "current",
        "strategy_settings.yaml",
    )
    assert locator.PORTFOLIO_SNAPSHOT_PATH_COMPONENTS == (
        "inputs",
        "current",
        "portfolio_snapshot.txt",
    )
    assert str(
        PurePosixPath(*locator.STRATEGY_SETTINGS_PATH_COMPONENTS)
    ) == "inputs/current/strategy_settings.yaml"
    assert str(
        PurePosixPath(*locator.PORTFOLIO_SNAPSHOT_PATH_COMPONENTS)
    ) == "inputs/current/portfolio_snapshot.txt"


def test_mmi_source_catalog_reuses_the_canonical_locator_paths() -> None:
    strategy = MMI_SOURCE_CATALOG[MmiSourceRole.STRATEGY_SETTINGS]
    portfolio = MMI_SOURCE_CATALOG[MmiSourceRole.PORTFOLIO_SNAPSHOT]
    # Object identity, not equality: there is exactly one path owner.
    assert (
        strategy.path_components
        is locator.STRATEGY_SETTINGS_PATH_COMPONENTS
    )
    assert (
        portfolio.path_components
        is locator.PORTFOLIO_SNAPSHOT_PATH_COMPONENTS
    )
    assert tuple(MMI_SOURCE_CATALOG) == (
        MmiSourceRole.STRATEGY_SETTINGS,
        MmiSourceRole.PORTFOLIO_SNAPSHOT,
    )
    # MMI keeps its own role, identifier and bound semantics.
    assert strategy.source_id == "MMI_STRATEGY_SETTINGS"
    assert portfolio.source_id == "MMI_PORTFOLIO_SNAPSHOT"
    assert strategy.maximum_bytes == 262_144
    assert portfolio.maximum_bytes == 1_048_576
    # No duplicate filesystem-path literal survives in the MMI contracts owner.
    contracts_source = (
        repo_root() / "src/investment_orchestrator/mmi/contracts.py"
    ).read_text(encoding="utf-8")
    assert "strategy_settings.yaml" not in contracts_source
    assert "portfolio_snapshot.txt" not in contracts_source


def test_locator_public_surface_is_exactly_paths_and_layout_error() -> None:
    """No unused public root-yielding function; only demonstrated consumers.

    The locator owns the shared lexical-root algorithm privately and the two
    canonical path tuples publicly.  It does not itself expose a root-yielding
    production entry point: normal MMI production capture supplies its own
    trusted module locator to the private helper.
    """
    public_callables = {
        name: value
        for name, value in vars(locator).items()
        if not name.startswith("_")
        and callable(value)
        and getattr(value, "__module__", None) == locator.__name__
    }
    assert set(public_callables) == {"ProductionCheckoutLayoutError"}
    # The only public data is the two canonical path tuples.
    assert {
        name
        for name, value in vars(locator).items()
        if not name.startswith("_") and isinstance(value, tuple)
    } == {
        "PORTFOLIO_SNAPSHOT_PATH_COMPONENTS",
        "STRATEGY_SETTINGS_PATH_COMPONENTS",
    }
    assert not hasattr(locator, "production_checkout_root")


def test_lexical_root_derivation_ignores_cwd_and_env(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The shared private algorithm is cwd/env-independent, given a module locator.

    This exercises exactly the primitive that MMI's trusted production module
    locator (and, separately, private hermetic test seams) supply their own
    module file and suffix to; the locator itself supplies neither.
    """
    module_file = Path(locator.__file__)
    components = (
        "src",
        "investment_orchestrator",
        "production_inputs",
        "current_source_locator.py",
    )
    root, resolved = locator._lexical_checkout_root(
        module_file,
        module_path_components=components,
    )
    assert root == repo_root()
    assert resolved == module_file

    unrelated = tmp_path / "unrelated"
    unrelated.mkdir()
    monkeypatch.chdir(unrelated)
    monkeypatch.setenv("MMI_REPOSITORY_ROOT", str(tmp_path / "wrong"))
    monkeypatch.setenv("PWD", str(tmp_path / "wrong-cwd"))
    root_again, _ = locator._lexical_checkout_root(
        module_file,
        module_path_components=components,
    )
    assert root_again == root


def test_production_root_and_paths_resolve_the_bytes_mmi_captures() -> None:
    """End-to-end equivalence with the unchanged MMI public capture root."""
    for role, components in (
        (
            MmiSourceRole.STRATEGY_SETTINGS,
            locator.STRATEGY_SETTINGS_PATH_COMPONENTS,
        ),
        (
            MmiSourceRole.PORTFOLIO_SNAPSHOT,
            locator.PORTFOLIO_SNAPSHOT_PATH_COMPONENTS,
        ),
    ):
        located = repo_root().joinpath(*components)
        raw = located.read_bytes()
        digest = hashlib.sha256(raw).hexdigest()
        result = source_capture.capture_current_mmi_source(
            role,
            expected_source_sha256=digest,
        )
        assert result.status is (
            MmiProjectionResultCategory.PROJECTION_VALID_COMPLETE
        )
        assert result.source is not None
        assert result.source.raw_bytes == raw
        assert result.source.source_record["observed_sha256"] == digest
        assert result.source.source_record[
            "repository_relative_locator"
        ] == str(PurePosixPath(*components))


def test_locator_root_resolution_is_fail_closed() -> None:
    supported = repo_root().joinpath(
        "src",
        "investment_orchestrator",
        "production_inputs",
        "current_source_locator.py",
    )
    components = (
        "src",
        "investment_orchestrator",
        "production_inputs",
        "current_source_locator.py",
    )
    root, module_path = locator._lexical_checkout_root(
        supported,
        module_path_components=components,
    )
    assert root == repo_root()
    assert module_path == supported

    # Raw strings, because ``Path`` would silently normalise some of these.
    leaf = "production_inputs/current_source_locator.py"
    rejected = (
        # relative layout
        f"src/investment_orchestrator/{leaf}",
        # installed layout without the ``src`` component
        f"/opt/site-packages/investment_orchestrator/{leaf}",
        # wrong module filename
        "/opt/project/src/investment_orchestrator/production_inputs"
        "/other_locator.py",
        # non-normalised path
        f"/opt/project/./src/investment_orchestrator/{leaf}",
        # NUL-bearing path
        f"/opt/pro\x00ject/src/investment_orchestrator/{leaf}",
        # non-str fspath and non-path objects
        f"/opt/project/src/investment_orchestrator/{leaf}".encode(),
        object(),
        None,
    )
    for candidate in rejected:
        with pytest.raises(locator.ProductionCheckoutLayoutError):
            locator._lexical_checkout_root(
                candidate,  # type: ignore[arg-type]
                module_path_components=components,
            )


def test_locator_imports_nothing_upward_and_owns_no_observation() -> None:
    imported = _locator_imports()
    assert not any(
        name == prefix or name.startswith(f"{prefix}.")
        for name in imported
        for prefix in PROHIBITED_LOCATOR_IMPORT_PREFIXES
    ), imported
    assert {name.split(".", 1)[0] for name in imported} == {
        "__future__",
        "os",
        "pathlib",
        "typing",
    }
    source = Path(locator.__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)
    called = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            value = node.func
            parts: list[str] = []
            while isinstance(value, ast.Attribute):
                parts.append(value.attr)
                value = value.value
            if isinstance(value, ast.Name):
                parts.append(value.id)
            called.add(".".join(reversed(parts)))
    assert called <= {
        "Path",
        "ProductionCheckoutLayoutError",
        "_lexical_checkout_root",
        "len",
        "os.fspath",
        "os.path.isabs",
        "os.path.normpath",
        "tuple",
        "type",
        "checkout_root.is_absolute",
    }, called
    # No observation capability of any kind lives here.  Identifiers only:
    # the module docstring names openat2 precisely to disclaim owning it.
    identifiers: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            identifiers.add(node.id)
        elif isinstance(node, ast.Attribute):
            identifiers.add(node.attr)
        elif isinstance(node, ast.arg):
            identifiers.add(node.arg)
        elif isinstance(node, ast.keyword) and node.arg is not None:
            identifiers.add(node.arg)
    assert not identifiers & {
        "openat2",
        "hashlib",
        "sha256",
        "hexdigest",
        "read",
        "read_bytes",
        "open",
        "syscall",
        "maximum_bytes",
        "expected_source_sha256",
        "MmiSourceRole",
        "MmiCapturedSource",
    }, identifiers
