from __future__ import annotations

import ast
from dataclasses import replace
import hashlib
from pathlib import Path

import pytest

from investment_orchestrator.common import schema_validation
from investment_orchestrator.common.paths import repo_root
from investment_orchestrator.mmi.contracts import (
    MMI_SOURCE_CATALOG,
    MmiSourceRole,
    begin_mmi_projection_run,
)
from investment_orchestrator.mmi.policy_projection import (
    build_mmi_policy_projection,
)
from investment_orchestrator.mmi.portfolio_projection import (
    build_mmi_portfolio_snapshot_projection,
)
from investment_orchestrator.mmi.source_capture import (
    capture_current_mmi_source,
)
from investment_orchestrator.observability import (
    ltetf_target_architecture_gap_report as ltetf,
)
from investment_orchestrator.observability.ltetf_target_architecture_prerequisite_catalog import (
    CATALOG_IDENTITY_SHA256,
    canonical_catalog_bytes,
    catalog_identity_sha256,
)


MMI_PRODUCTION_PATHS = (
    "src/investment_orchestrator/mmi/__init__.py",
    (
        "src/investment_orchestrator/mmi/"
        "analyst_visible_evidence_view.py"
    ),
    (
        "src/investment_orchestrator/mmi/"
        "analyst_visible_evidence_view_v2.py"
    ),
    "src/investment_orchestrator/mmi/canonical.py",
    "src/investment_orchestrator/mmi/contracts.py",
    "src/investment_orchestrator/mmi/evidence_bundle.py",
    "src/investment_orchestrator/mmi/grounded_prompt.py",
    "src/investment_orchestrator/mmi/grounded_prompt_v2.py",
    (
        "src/investment_orchestrator/mmi/"
        "legacy_step1_compatibility_candidate_v1.py"
    ),
    "src/investment_orchestrator/mmi/policy_projection.py",
    "src/investment_orchestrator/mmi/portfolio_projection.py",
    "src/investment_orchestrator/mmi/raw_response_envelope.py",
    "src/investment_orchestrator/mmi/raw_response_envelope_v2.py",
    "src/investment_orchestrator/mmi/source_capture.py",
    (
        "src/investment_orchestrator/mmi/"
        "validated_grounded_analysis_response.py"
    ),
    (
        "src/investment_orchestrator/mmi/"
        "validated_grounded_analysis_response_v2.py"
    ),
)
H2_COMPARISON_REPORT_RELATIVE_PATH = (
    "src/investment_orchestrator/offline/"
    "mmi_legacy_step1_comparison_report_v1.py"
)
H2C_CAPTURE_SESSION_RELATIVE_PATH = (
    "src/investment_orchestrator/offline/"
    "mmi_h2c_manual_capture_session.py"
)
H2C_RECEIPT_RELATIVE_PATH = (
    "src/investment_orchestrator/offline/"
    "mmi_h2c_dual_side_manual_handoff_context_receipt_v1.py"
)
H2C_CASE_BUNDLE_RELATIVE_PATH = (
    "src/investment_orchestrator/offline/"
    "mmi_h2c_case_bundle_v1.py"
)
H2C_CONSUME_ENGINE_RELATIVE_PATH = (
    "src/investment_orchestrator/offline/"
    "mmi_h2c_consume_persisted_case_v1.py"
)
H2C_PERSISTED_RECEIPT_V2_RELATIVE_PATH = (
    "src/investment_orchestrator/offline/"
    "mmi_h2c_dual_side_persisted_case_receipt_v2.py"
)
H2C_PREPARED_CASE_RELATIVE_PATH = (
    "src/investment_orchestrator/offline/"
    "mmi_h2c_prepared_case_v1.py"
)
H2C_PREPARE_ENGINE_RELATIVE_PATH = (
    "src/investment_orchestrator/offline/"
    "mmi_h2c_prepare_persisted_case_v1.py"
)
H2C_CLI_RELATIVE_PATH = (
    "src/investment_orchestrator/cli/run_mmi_h2c_capture.py"
)
H2C_PREPARE_CLI_RELATIVE_PATH = (
    "src/investment_orchestrator/cli/run_mmi_h2c_prepare.py"
)
H2C_ARCHIVED_SOURCE_RELATIVE_PATH = (
    "src/investment_orchestrator/offline/"
    "mmi_h2c_archived_source_v1.py"
)
EXPECTED_EXTERNAL_CONSUMERS = (
    "src/investment_orchestrator/cli/observe_ltetf_target_architecture_gaps.py",
    "src/investment_orchestrator/cli/weekly_shadow_01_report_publisher_cli.py",
)
PROHIBITED_IMPORT_PREFIXES = (
    "investment_orchestrator.observability",
    "investment_orchestrator.workflow",
    "investment_orchestrator.state",
    "investment_orchestrator.permissions",
    "investment_orchestrator.orders",
    "investment_orchestrator.broker",
    "openai",
    "anthropic",
    "langchain",
    "google.generativeai",
    "cohere",
    "requests",
    "httpx",
    "urllib.request",
    "subprocess",
)


def _mmi_sources() -> dict[str, str]:
    root = repo_root()
    return {
        relative: (root / relative).read_text(encoding="utf-8")
        for relative in MMI_PRODUCTION_PATHS
    }


def _call_name(node: ast.Call) -> str:
    value = node.func
    parts: list[str] = []
    while isinstance(value, ast.Attribute):
        parts.append(value.attr)
        value = value.value
    if isinstance(value, ast.Name):
        parts.append(value.id)
    return ".".join(reversed(parts))


def test_mmi_package_initialization_has_no_import_or_reexport() -> None:
    source = _mmi_sources()[MMI_PRODUCTION_PATHS[0]]
    tree = ast.parse(source)
    assert len(tree.body) == 2
    assert isinstance(tree.body[0], ast.Expr)
    assert isinstance(tree.body[0].value, ast.Constant)
    assert isinstance(tree.body[0].value.value, str)
    assignment = tree.body[1]
    assert isinstance(assignment, ast.Assign)
    assert [target.id for target in assignment.targets if isinstance(target, ast.Name)] == [
        "__all__"
    ]
    assert isinstance(assignment.value, ast.Tuple)
    assert assignment.value.elts == []
    assert not any(isinstance(node, (ast.Import, ast.ImportFrom)) for node in ast.walk(tree))


def test_mmi_import_graph_is_closed_to_stdlib_yaml_schema_validation_and_mmi() -> None:
    inventory = ltetf._scan_production_inventory(repo_root())
    imports_by_path = dict(inventory.imports_by_path)
    assert tuple(
        path for path in inventory.production_paths if "/mmi/" in path
    ) == MMI_PRODUCTION_PATHS
    for path in MMI_PRODUCTION_PATHS:
        imports = imports_by_path[path]
        assert not any(
            imported == prefix or imported.startswith(f"{prefix}.")
            for imported in imports
            for prefix in PROHIBITED_IMPORT_PREFIXES
        )
        for imported in imports:
            root_name = imported.split(".", 1)[0]
            assert (
                root_name
                in {
                    "__future__",
                    "collections",
                    "dataclasses",
                    "datetime",
                    "decimal",
                    "enum",
                    "errno",
                    "hashlib",
                    "hmac",
                    "json",
                    "os",
                    "pathlib",
                    "re",
                    "secrets",
                    "stat",
                    "struct",
                    "types",
                    "typing",
                    "yaml",
                    "zoneinfo",
                }
                or imported
                == "investment_orchestrator.common.schema_validation"
                or imported
                == (
                    "investment_orchestrator.common.schema_validation."
                    "validate_artifact_schema"
                )
                or imported
                == (
                    "investment_orchestrator.parsers."
                    "portfolio_snapshot_existing_orders"
                )
                or imported
                == (
                    "investment_orchestrator.parsers."
                    "portfolio_snapshot_existing_orders."
                    "parse_existing_buy_open_orders_summary"
                )
                or (
                    path
                    == (
                        "src/investment_orchestrator/mmi/"
                        "contracts.py"
                    )
                    and imported == "base64"
                )
                or (
                    path in {
                        "src/investment_orchestrator/mmi/"
                        "raw_response_envelope.py",
                        "src/investment_orchestrator/mmi/"
                        "raw_response_envelope_v2.py",
                    }
                    and imported == "base64"
                )
                or (
                    path
                    == (
                        "src/investment_orchestrator/mmi/"
                        "source_capture.py"
                    )
                    and imported == "ctypes"
                )
                or (
                    path in {
                        (
                            "src/investment_orchestrator/mmi/"
                            "analyst_visible_evidence_view.py"
                        ),
                        (
                            "src/investment_orchestrator/mmi/"
                            "analyst_visible_evidence_view_v2.py"
                        ),
                    }
                    and imported == "investment_orchestrator.mmi"
                )
                or imported.startswith("investment_orchestrator.mmi.")
            ), (path, imported)

    json_importers = tuple(
        path
        for path, imports in inventory.imports_by_path
        if path in MMI_PRODUCTION_PATHS and "json" in imports
    )
    assert json_importers == (
        "src/investment_orchestrator/mmi/canonical.py",
        "src/investment_orchestrator/mmi/contracts.py",
        "src/investment_orchestrator/mmi/grounded_prompt_v2.py",
        (
            "src/investment_orchestrator/mmi/"
            "validated_grounded_analysis_response.py"
        ),
        (
            "src/investment_orchestrator/mmi/"
            "validated_grounded_analysis_response_v2.py"
        ),
    )

    external_mmi_readers = tuple(
        path
        for path, imports in inventory.imports_by_path
        if path not in MMI_PRODUCTION_PATHS
        and any(
            imported == "investment_orchestrator.mmi"
            or imported.startswith("investment_orchestrator.mmi.")
            for imported in imports
        )
    )
    # Only the dormant H2 owner and explicit report-only H2c owners may read
    # MMI outside the package; none is an admission or action consumer.  The
    # H2c case-bundle owner structurally envelopes MMI artifact mappings; its
    # only consumer is the explicit foreground capture session asserted below.
    # The Phase A preparation engine reads the same live chain report-only and
    # has no production consumer of its own.
    assert set(external_mmi_readers) == {
        H2C_CASE_BUNDLE_RELATIVE_PATH,
        H2C_CONSUME_ENGINE_RELATIVE_PATH,
        H2C_RECEIPT_RELATIVE_PATH,
        H2C_PERSISTED_RECEIPT_V2_RELATIVE_PATH,
        H2C_CAPTURE_SESSION_RELATIVE_PATH,
        H2C_PREPARE_ENGINE_RELATIVE_PATH,
        H2C_PREPARED_CASE_RELATIVE_PATH,
        H2_COMPARISON_REPORT_RELATIVE_PATH,
        H2C_ARCHIVED_SOURCE_RELATIVE_PATH,
    }
    assert {
        imported
        for imported in imports_by_path[H2_COMPARISON_REPORT_RELATIVE_PATH]
        if imported.startswith("investment_orchestrator.mmi")
    } == {
        "investment_orchestrator.mmi.canonical",
        "investment_orchestrator.mmi.canonical."
        "MAX_MMI_LEGACY_STEP1_COMPARISON_REPORT_V1_CANONICAL_BYTES",
        "investment_orchestrator.mmi.canonical.MmiCanonicalizationError",
        "investment_orchestrator.mmi.canonical."
        "_MMI_LEGACY_STEP1_COMPARISON_REPORT_V1_IDENTITY_DOMAIN",
        "investment_orchestrator.mmi.canonical.canonical_json_bytes",
        "investment_orchestrator.mmi.canonical.record_identity_sha256",
        "investment_orchestrator.mmi.contracts",
        "investment_orchestrator.mmi.contracts.AUTHORITY_EFFECT_NONE",
        "investment_orchestrator.mmi.legacy_step1_compatibility_candidate_v1",
        "investment_orchestrator.mmi.legacy_step1_compatibility_candidate_v1."
        "MmiLegacyStep1CompatibilityCandidateV1Error",
        "investment_orchestrator.mmi.legacy_step1_compatibility_candidate_v1."
        "validate_mmi_legacy_step1_compatibility_candidate_v1",
    }


def test_ctypes_authority_is_exactly_source_capture_openat2_only() -> None:
    inventory = ltetf._scan_production_inventory(repo_root())
    ctypes_importers = tuple(
        path
        for path, imports in inventory.imports_by_path
        if path in MMI_PRODUCTION_PATHS and "ctypes" in imports
    )
    assert ctypes_importers == (
        "src/investment_orchestrator/mmi/source_capture.py",
    )
    for path, imports in inventory.imports_by_path:
        if path not in MMI_PRODUCTION_PATHS:
            continue
        assert "ctypes.util" not in imports
        assert not {
            imported.split(".", 1)[0]
            for imported in imports
        } & {
            "_ctypes",
            "cffi",
            "cffi_backend",
        }
    source = _mmi_sources()[
        "src/investment_orchestrator/mmi/source_capture.py"
    ]
    tree = ast.parse(source)
    ctypes_calls = {
        _call_name(node)
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and _call_name(node).startswith("ctypes.")
    }
    assert ctypes_calls <= {
        "ctypes.CDLL",
        "ctypes.byref",
        "ctypes.c_char_p",
        "ctypes.c_int",
        "ctypes.c_long",
        "ctypes.c_size_t",
        "ctypes.get_errno",
        "ctypes.sizeof",
    }
    cdll_calls = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and _call_name(node) == "ctypes.CDLL"
    ]
    assert len(cdll_calls) == 1
    assert len(cdll_calls[0].args) == 1
    assert isinstance(cdll_calls[0].args[0], ast.Constant)
    assert cdll_calls[0].args[0].value is None
    assert [
        (keyword.arg, keyword.value.value)
        for keyword in cdll_calls[0].keywords
        if isinstance(keyword.value, ast.Constant)
    ] == [("use_errno", True)]
    assert "ctypes.util" not in source
    assert "find_library" not in source


def test_schema_helper_is_imported_by_exact_symbol_only() -> None:
    for relative in (
        "src/investment_orchestrator/mmi/contracts.py",
        "src/investment_orchestrator/mmi/source_capture.py",
        "src/investment_orchestrator/mmi/policy_projection.py",
        "src/investment_orchestrator/mmi/portfolio_projection.py",
        "src/investment_orchestrator/mmi/evidence_bundle.py",
        "src/investment_orchestrator/mmi/grounded_prompt.py",
        "src/investment_orchestrator/mmi/grounded_prompt_v2.py",
        (
            "src/investment_orchestrator/mmi/"
            "legacy_step1_compatibility_candidate_v1.py"
        ),
        (
            "src/investment_orchestrator/mmi/"
            "raw_response_envelope.py"
        ),
        (
            "src/investment_orchestrator/mmi/"
            "raw_response_envelope_v2.py"
        ),
        (
            "src/investment_orchestrator/mmi/"
            "analyst_visible_evidence_view.py"
        ),
        (
            "src/investment_orchestrator/mmi/"
            "analyst_visible_evidence_view_v2.py"
        ),
        (
            "src/investment_orchestrator/mmi/"
            "validated_grounded_analysis_response.py"
        ),
        (
            "src/investment_orchestrator/mmi/"
            "validated_grounded_analysis_response_v2.py"
        ),
    ):
        tree = ast.parse(_mmi_sources()[relative])
        imports = [
            node
            for node in tree.body
            if isinstance(node, ast.ImportFrom)
            and node.module
            == "investment_orchestrator.common.schema_validation"
        ]
        assert len(imports) == 1
        assert [(alias.name, alias.asname) for alias in imports[0].names] == [
            ("validate_artifact_schema", None)
        ]
    portfolio_tree = ast.parse(
        _mmi_sources()[
            "src/investment_orchestrator/mmi/portfolio_projection.py"
        ]
    )
    parser_imports = [
        node
        for node in portfolio_tree.body
        if isinstance(node, ast.ImportFrom)
        and node.module
        == (
            "investment_orchestrator.parsers."
            "portfolio_snapshot_existing_orders"
        )
    ]
    assert len(parser_imports) == 1
    assert [
        (alias.name, alias.asname)
        for alias in parser_imports[0].names
    ] == [("parse_existing_buy_open_orders_summary", None)]
    all_source = "\n".join(_mmi_sources().values())
    assert "write_validated_json" not in all_source
    assert "write_json" not in all_source


def test_mmi_modules_have_no_dynamic_execution_scan_or_write_capability() -> None:
    forbidden_calls = {
        "__import__",
        "eval",
        "exec",
        "glob.glob",
        "glob.iglob",
        "os.listdir",
        "os.mkdir",
        "os.makedirs",
        "os.remove",
        "os.rename",
        "os.replace",
        "os.scandir",
        "os.unlink",
        "os.walk",
        "Path.glob",
        "Path.iterdir",
        "Path.mkdir",
        "Path.open",
        "Path.resolve",
        "Path.read_text",
        "Path.read_bytes",
        "Path.rename",
        "Path.replace",
        "Path.rglob",
        "Path.touch",
        "Path.unlink",
        "Path.write_bytes",
        "Path.write_text",
        "tempfile.NamedTemporaryFile",
        "tempfile.TemporaryDirectory",
    }
    forbidden_attributes = {
        "glob",
        "rglob",
        "walk",
        "scandir",
        "listdir",
        "iterdir",
    }
    forbidden_literals = (
        "artifacts/target_architecture",
        "ltetf_operator_mandate",
        "ltetf_target_architecture",
        "weekly_shadow_01",
        "replacement_observation",
    )
    for relative, source in _mmi_sources().items():
        tree = ast.parse(source)
        calls = {_call_name(node) for node in ast.walk(tree) if isinstance(node, ast.Call)}
        assert not calls & forbidden_calls, relative
        assert not {
            node.attr
            for node in ast.walk(tree)
            if isinstance(node, ast.Attribute)
        } & forbidden_attributes, relative
        literals = [
            node.value
            for node in ast.walk(tree)
            if isinstance(node, ast.Constant) and isinstance(node.value, str)
        ]
        assert not any(
            forbidden in literal.casefold()
            for literal in literals
            for forbidden in forbidden_literals
        ), relative


def test_v2_prompt_envelope_and_response_graph_is_exact_and_dormant() -> None:
    inventory = ltetf._scan_production_inventory(repo_root())
    imports_by_path = dict(inventory.imports_by_path)

    def consumers(module: str) -> tuple[str, ...]:
        return tuple(
            path
            for path, imports in imports_by_path.items()
            if module in imports
        )

    assert consumers(
        "investment_orchestrator.mmi.analyst_visible_evidence_view_v2"
    ) == (
        "src/investment_orchestrator/mmi/grounded_prompt_v2.py",
        "src/investment_orchestrator/mmi/"
        "legacy_step1_compatibility_candidate_v1.py",
        H2C_CONSUME_ENGINE_RELATIVE_PATH,
        H2C_CAPTURE_SESSION_RELATIVE_PATH,
        H2C_PREPARE_ENGINE_RELATIVE_PATH,
    )
    assert consumers(
        "investment_orchestrator.mmi.grounded_prompt_v2"
    ) == (
        "src/investment_orchestrator/mmi/raw_response_envelope_v2.py",
        H2C_CONSUME_ENGINE_RELATIVE_PATH,
        H2C_CAPTURE_SESSION_RELATIVE_PATH,
        H2C_PREPARE_ENGINE_RELATIVE_PATH,
    )
    assert consumers(
        "investment_orchestrator.mmi.raw_response_envelope_v2"
    ) == (
        "src/investment_orchestrator/mmi/"
        "validated_grounded_analysis_response_v2.py",
        H2C_CONSUME_ENGINE_RELATIVE_PATH,
        H2C_CAPTURE_SESSION_RELATIVE_PATH,
    )
    assert consumers(
        "investment_orchestrator.mmi."
        "validated_grounded_analysis_response_v2"
    ) == (
        "src/investment_orchestrator/mmi/"
        "legacy_step1_compatibility_candidate_v1.py",
        H2C_CONSUME_ENGINE_RELATIVE_PATH,
        H2C_CAPTURE_SESSION_RELATIVE_PATH,
    )
    # H2c: H1 and H2 gain only the explicit foreground capture consumer.
    assert consumers(
        "investment_orchestrator.mmi."
        "legacy_step1_compatibility_candidate_v1"
    ) == (
        H2C_CONSUME_ENGINE_RELATIVE_PATH,
        H2C_CAPTURE_SESSION_RELATIVE_PATH,
        H2_COMPARISON_REPORT_RELATIVE_PATH,
    )
    assert consumers(
        "investment_orchestrator.offline."
        "mmi_legacy_step1_comparison_report_v1"
    ) == (H2C_CONSUME_ENGINE_RELATIVE_PATH, H2C_CAPTURE_SESSION_RELATIVE_PATH)
    assert set(consumers(
        "investment_orchestrator.offline."
        "mmi_h2c_dual_side_manual_handoff_context_receipt_v1"
    )) == {H2C_CAPTURE_SESSION_RELATIVE_PATH, H2C_ARCHIVED_SOURCE_RELATIVE_PATH}
    assert consumers(
        "investment_orchestrator.offline.mmi_h2c_case_bundle_v1"
    ) == (H2C_CONSUME_ENGINE_RELATIVE_PATH, H2C_CAPTURE_SESSION_RELATIVE_PATH)
    assert consumers(
        "investment_orchestrator.offline."
        "mmi_h2c_dual_side_persisted_case_receipt_v2"
    ) == (H2C_CONSUME_ENGINE_RELATIVE_PATH,)
    assert set(consumers(
        "investment_orchestrator.offline._mmi_h2c_stable_read_v1"
    )) == {H2C_CONSUME_ENGINE_RELATIVE_PATH, H2C_ARCHIVED_SOURCE_RELATIVE_PATH}
    # D4b: the dormant prepared-case contract gains exactly one production
    # consumer, the Phase A engine, which itself stays consumer-free.
    assert set(consumers(
        "investment_orchestrator.offline.mmi_h2c_prepared_case_v1"
    )) == {H2C_CONSUME_ENGINE_RELATIVE_PATH, H2C_PREPARE_ENGINE_RELATIVE_PATH, H2C_ARCHIVED_SOURCE_RELATIVE_PATH}
    assert consumers(
        "investment_orchestrator.offline.mmi_h2c_prepare_persisted_case_v1"
    ) == (H2C_PREPARE_CLI_RELATIVE_PATH,)
    assert consumers(
        "investment_orchestrator.offline.mmi_h2c_manual_capture_session"
    ) == (H2C_CLI_RELATIVE_PATH,)
    assert consumers(
        "investment_orchestrator.mmi.analyst_visible_evidence_view"
    ) == (
        "src/investment_orchestrator/mmi/grounded_prompt.py",
    )
    assert consumers(
        "investment_orchestrator.mmi.grounded_prompt"
    ) == (
        "src/investment_orchestrator/mmi/raw_response_envelope.py",
    )
    assert consumers(
        "investment_orchestrator.mmi.raw_response_envelope"
    ) == (
        "src/investment_orchestrator/mmi/"
        "validated_grounded_analysis_response.py",
    )
    assert (
        "src/investment_orchestrator/mmi/"
        "validated_grounded_analysis_response_v2.py"
        in inventory.production_paths
    )


def test_source_roles_resolve_only_exact_approved_inputs() -> None:
    assert {
        role: (
            spec.source_id,
            str(spec.repository_relative_locator),
            spec.maximum_bytes,
        )
        for role, spec in MMI_SOURCE_CATALOG.items()
    } == {
        MmiSourceRole.STRATEGY_SETTINGS: (
            "MMI_STRATEGY_SETTINGS",
            "inputs/current/strategy_settings.yaml",
            262_144,
        ),
        MmiSourceRole.PORTFOLIO_SNAPSHOT: (
            "MMI_PORTFOLIO_SNAPSHOT",
            "inputs/current/portfolio_snapshot.txt",
            1_048_576,
        ),
    }


def test_reachable_schema_validation_call_graph_does_not_write(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail_write(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("write helper became reachable")

    monkeypatch.setattr(schema_validation, "write_validated_json", fail_write)
    monkeypatch.setattr(schema_validation, "write_json", fail_write)
    raw = (repo_root() / "inputs/current/strategy_settings.yaml").read_bytes()
    capture = capture_current_mmi_source(
        MmiSourceRole.STRATEGY_SETTINGS,
        expected_source_sha256=hashlib.sha256(raw).hexdigest(),
    )
    assert capture.valid
    assert capture.source is not None
    run_context = begin_mmi_projection_run()
    result = build_mmi_policy_projection(
        capture.source,
        run_context=run_context,
    )
    assert result.valid
    assert result.projection is not None
    portfolio_raw = (
        repo_root() / "inputs/current/portfolio_snapshot.txt"
    ).read_bytes()
    portfolio_capture = capture_current_mmi_source(
        MmiSourceRole.PORTFOLIO_SNAPSHOT,
        expected_source_sha256=hashlib.sha256(
            portfolio_raw
        ).hexdigest(),
    )
    assert portfolio_capture.valid
    assert portfolio_capture.source is not None
    portfolio_result = build_mmi_portfolio_snapshot_projection(
        portfolio_capture.source,
        policy_projection=result.projection,
        policy_source=capture.source,
        run_context=run_context,
    )
    assert portfolio_result.valid


def test_ltetf_inventory_classification_is_unchanged_except_inventory_content() -> None:
    inventory = ltetf._scan_production_inventory(repo_root())
    assert len(inventory.production_paths) == 152
    assert inventory.dynamic_findings == ()
    assert inventory.observer_external_consumers == EXPECTED_EXTERNAL_CONSUMERS
    assert inventory.report_artifact_readers == ()
    assert inventory.policy_artifact_consumers == ()
    assert inventory.prohibited_observer_capability_imports == ()
    assert inventory.p4a_runtime_consumers == ()
    assert inventory.broker_capability_imports == ()
    assert inventory.weekly_llm_invocation_markers == ()

    current_identity = ltetf._inventory_to_evidence_record(
        inventory
    ).content_identity_sha256
    filtered = replace(
        inventory,
        production_paths=tuple(
            path
            for path in inventory.production_paths
            if path not in MMI_PRODUCTION_PATHS
        ),
        imports_by_path=tuple(
            item
            for item in inventory.imports_by_path
            if item[0] not in MMI_PRODUCTION_PATHS
        ),
    )
    filtered_identity = ltetf._inventory_to_evidence_record(
        filtered
    ).content_identity_sha256
    assert current_identity is not None
    assert filtered_identity is not None
    assert current_identity != filtered_identity


def test_mmi_does_not_change_or_satisfy_ltetf_prerequisite_catalog() -> None:
    assert CATALOG_IDENTITY_SHA256 == (
        "b5126ecb9d3753af5ac7dcb40d7712eeb"
        "3234bdaff609c42d65d9e957dc8d71e"
    )
    assert CATALOG_IDENTITY_SHA256 == catalog_identity_sha256()
    assert b"mmi" not in canonical_catalog_bytes().lower()
    report_namespace = repo_root() / ltetf.REPORT_NAMESPACE_RELATIVE_PATH
    before = tuple(
        sorted(
            (
                path.relative_to(report_namespace).as_posix(),
                path.read_bytes() if path.is_file() else None,
            )
            for path in report_namespace.rglob("*")
        )
    )
    report = ltetf.build_gap_report(repo_root())
    assert report["prerequisite_catalog_identity_sha256"] == (
        CATALOG_IDENTITY_SHA256
    )
    assert report["authority"] == ltetf.AUTHORITY_DECLARATION
    after = tuple(
        sorted(
            (
                path.relative_to(report_namespace).as_posix(),
                path.read_bytes() if path.is_file() else None,
            )
            for path in report_namespace.rglob("*")
        )
    )
    assert after == before
