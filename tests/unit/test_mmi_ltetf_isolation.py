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
    (
        "src/investment_orchestrator/mmi/"
        "long_horizon_research_payload_v1.py"
    ),
    (
        "src/investment_orchestrator/mmi/"
        "long_horizon_research_payload_v2.py"
    ),
    (
        "src/investment_orchestrator/mmi/"
        "mmi_h1_legacy_step1_mapping_report_v1.py"
    ),
    "src/investment_orchestrator/mmi/mmi_h1_prepared_handoff_v1.py",
    "src/investment_orchestrator/mmi/policy_projection.py",
    "src/investment_orchestrator/mmi/portfolio_projection.py",
    "src/investment_orchestrator/mmi/raw_response_envelope.py",
    "src/investment_orchestrator/mmi/raw_response_envelope_v2.py",
    "src/investment_orchestrator/mmi/run_context_resumption.py",
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
H1_LEGACY_MAPPING_REPORT_RELATIVE_PATH = (
    "src/investment_orchestrator/mmi/"
    "mmi_h1_legacy_step1_mapping_report_v1.py"
)
H1_MAPPED_RECOGNITION_RELATIVE_PATH = (
    "src/investment_orchestrator/research/h1_mapped_recognition.py"
)
H1_PREPARED_HANDOFF_RELATIVE_PATH = (
    "src/investment_orchestrator/mmi/mmi_h1_prepared_handoff_v1.py"
)
H1_REPLACEMENT_HANDOFF_RELATIVE_PATH = (
    "src/investment_orchestrator/workflow/h1_replacement_handoff.py"
)
H1_REPLACEMENT_PREPARE_CLI_RELATIVE_PATH = (
    "src/investment_orchestrator/cli/run_h1_replacement_prepare.py"
)
H1_REPLACEMENT_CONSUME_CLI_RELATIVE_PATH = (
    "src/investment_orchestrator/cli/run_h1_replacement_consume.py"
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
H2C_ARCHIVED_CONSUME_CLI_RELATIVE_PATH = (
    "src/investment_orchestrator/cli/run_mmi_h2c_consume_archived.py"
)
H2C_ARCHIVED_SOURCE_RELATIVE_PATH = (
    "src/investment_orchestrator/offline/"
    "mmi_h2c_archived_source_v1.py"
)
EXPECTED_EXTERNAL_CONSUMERS = (
    "src/investment_orchestrator/cli/observe_ltetf_target_architecture_gaps.py",
    "src/investment_orchestrator/cli/weekly_shadow_01_report_publisher_cli.py",
)
CURRENT_SOURCE_LOCATOR_MODULE = (
    "investment_orchestrator.production_inputs.current_source_locator"
)
PRODUCTION_INPUTS_PATHS = (
    "src/investment_orchestrator/production_inputs/__init__.py",
    (
        "src/investment_orchestrator/production_inputs/"
        "current_source_locator.py"
    ),
)
# The approved downward dependency: exactly these two MMI owners may consume
# exactly these locator names.  MMI keeps every secure-I/O and provenance
# behaviour; the locator owns only the checkout root and the two source paths.
MMI_CURRENT_SOURCE_LOCATOR_CONSUMERS = (
    "src/investment_orchestrator/mmi/contracts.py",
    "src/investment_orchestrator/mmi/source_capture.py",
)
ALLOWED_CURRENT_SOURCE_LOCATOR_IMPORTS = frozenset(
    {
        CURRENT_SOURCE_LOCATOR_MODULE,
        f"{CURRENT_SOURCE_LOCATOR_MODULE}.LONG_HORIZON_RESEARCH_PATH_COMPONENTS",
        f"{CURRENT_SOURCE_LOCATOR_MODULE}.PORTFOLIO_SNAPSHOT_PATH_COMPONENTS",
        f"{CURRENT_SOURCE_LOCATOR_MODULE}.STRATEGY_SETTINGS_PATH_COMPONENTS",
        f"{CURRENT_SOURCE_LOCATOR_MODULE}.ProductionCheckoutLayoutError",
        f"{CURRENT_SOURCE_LOCATOR_MODULE}._lexical_checkout_root",
    }
)
PROHIBITED_PRODUCTION_INPUTS_IMPORT_PREFIXES = (
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


def _discovered_mmi_production_paths(
    inventory: "ltetf.ProductionInventory",
) -> tuple[str, ...]:
    """Live-discovered MMI production paths, sourced from the same scanner
    used for the whole-repository inventory -- never from the declared,
    potentially-stale ``MMI_PRODUCTION_PATHS`` tuple."""
    return tuple(
        path for path in inventory.production_paths if "/mmi/" in path
    )


def _assert_mmi_import_graph_is_closed(
    mmi_paths: tuple[str, ...],
    imports_by_path: dict[str, tuple[str, ...]],
) -> None:
    """Prohibited-import isolation oracle.

    Operates on whatever ``mmi_paths`` it is given -- it does not read
    ``MMI_PRODUCTION_PATHS`` itself, so it stays able to catch a violation in
    an undeclared MMI module even while inventory completeness is red.
    """
    for path in mmi_paths:
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
                or imported
                == (
                    "investment_orchestrator.validators."
                    "validate_research_handoff"
                )
                or imported
                == (
                    "investment_orchestrator.validators."
                    "validate_research_handoff.BASE_ROLE_KEYS"
                )
                or imported
                == (
                    "investment_orchestrator.validators."
                    "validate_research_handoff."
                    "LEGACY_RESEARCH_HANDOFF_STRICT_VALIDATOR_CONTRACT_VERSION"
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
                or (
                    path in MMI_CURRENT_SOURCE_LOCATOR_CONSUMERS
                    and imported in ALLOWED_CURRENT_SOURCE_LOCATOR_IMPORTS
                )
                or imported.startswith("investment_orchestrator.mmi.")
            ), (path, imported)


def test_mmi_production_inventory_matches_declared_mmi_production_paths() -> None:
    """MMI inventory completeness: the declared, frozen ``MMI_PRODUCTION_PATHS``
    must exactly equal the live-discovered MMI production paths.  Kept
    independent of the prohibited-import isolation oracle below: this test
    may be red on its own (e.g. a new MMI module was added but not yet
    declared here) without preventing that oracle from evaluating the actual,
    live-discovered module set."""
    inventory = ltetf._scan_production_inventory(repo_root())
    assert _discovered_mmi_production_paths(inventory) == MMI_PRODUCTION_PATHS


def test_mmi_inventory_drift_does_not_mask_prohibited_import_isolation() -> None:
    """Masking-independence proof.

    Shows that a stale declared ``MMI_PRODUCTION_PATHS`` (which would fail
    the completeness invariant above) cannot hide a prohibited import in the
    live-discovered MMI production set: the prohibited-import oracle is
    evaluated against live discovery, not the declared tuple, so it still
    independently catches a representative prohibited import.
    """
    inventory = ltetf._scan_production_inventory(repo_root())
    mmi_paths = _discovered_mmi_production_paths(inventory)
    imports_by_path = dict(inventory.imports_by_path)

    stale_declared = MMI_PRODUCTION_PATHS[:-1]
    assert stale_declared != mmi_paths

    tainted_path = mmi_paths[0]
    tainted_imports_by_path = dict(imports_by_path)
    tainted_imports_by_path[tainted_path] = imports_by_path[tainted_path] + (
        "subprocess",
    )

    with pytest.raises(AssertionError):
        _assert_mmi_import_graph_is_closed(mmi_paths, tainted_imports_by_path)


def test_mmi_import_graph_is_closed_to_stdlib_yaml_schema_validation_and_mmi() -> None:
    inventory = ltetf._scan_production_inventory(repo_root())
    imports_by_path = dict(inventory.imports_by_path)
    mmi_paths = _discovered_mmi_production_paths(inventory)
    _assert_mmi_import_graph_is_closed(mmi_paths, imports_by_path)


def test_mmi_json_importer_inventory_is_closed() -> None:
    """Closed inventory of which live-discovered MMI modules import ``json``.

    Independent of the prohibited-import and external-reader invariants: a
    change to this set signals json-importer drift on its own, without
    depending on either of those other checks passing first.
    """
    inventory = ltetf._scan_production_inventory(repo_root())
    mmi_paths = _discovered_mmi_production_paths(inventory)
    json_importers = tuple(
        path
        for path, imports in inventory.imports_by_path
        if path in mmi_paths and "json" in imports
    )
    assert json_importers == (
        "src/investment_orchestrator/mmi/canonical.py",
        "src/investment_orchestrator/mmi/contracts.py",
        "src/investment_orchestrator/mmi/grounded_prompt_v2.py",
        (
            "src/investment_orchestrator/mmi/"
            "long_horizon_research_payload_v1.py"
        ),
        (
            "src/investment_orchestrator/mmi/"
            "long_horizon_research_payload_v2.py"
        ),
        "src/investment_orchestrator/mmi/mmi_h1_prepared_handoff_v1.py",
        "src/investment_orchestrator/mmi/run_context_resumption.py",
        (
            "src/investment_orchestrator/mmi/"
            "validated_grounded_analysis_response.py"
        ),
        (
            "src/investment_orchestrator/mmi/"
            "validated_grounded_analysis_response_v2.py"
        ),
    )


def test_mmi_external_readers_match_approved_allowlist() -> None:
    """MMI external-reader closed architectural permission list.

    This is a CLOSED APPROVAL LIST, not a drift snapshot: only these paths
    may import MMI from outside the package, and none may be an admission or
    action consumer.  It is independent of inventory completeness, the
    prohibited-import scan, and the json-importer inventory above -- none of
    those passing or failing determines this result, and this result does
    not gate them either.

    As of this test, there is a known, already-tracked architecture
    violation: eight additional production modules (added across recent,
    unrelated feature commits) import ``investment_orchestrator.mmi.*``
    without ever having been reviewed against this allowlist.  This test is
    EXPECTED TO FAIL red -- actual external readers (19) exceed this
    approved set (11) -- until that debt is resolved by an explicitly
    reviewed and approved allowlist change.  Do not add those eight paths
    here without that review, and do not weaken this equality to a subset
    check to silence the failure.
    """
    inventory = ltetf._scan_production_inventory(repo_root())
    imports_by_path = dict(inventory.imports_by_path)
    mmi_paths = _discovered_mmi_production_paths(inventory)

    external_mmi_readers = tuple(
        path
        for path, imports in inventory.imports_by_path
        if path not in mmi_paths
        and any(
            imported == "investment_orchestrator.mmi"
            or imported.startswith("investment_orchestrator.mmi.")
            for imported in imports
        )
    )
    # Only closed report-only H2 owners and explicit report-only H2c owners
    # may read MMI outside the package; none is an admission or action
    # consumer.  The H1 mapping adapter now lives inside the MMI package
    # itself, so it is no longer an external reader.  The H2c case-bundle
    # owner structurally envelopes MMI artifact mappings; its only consumer
    # is the explicit foreground capture session asserted below.  The Phase A
    # preparation engine reads the same live chain report-only and has no
    # production consumer of its own.  P2b adds exactly one more external
    # reader: the current-lane H1 replacement prepare/consume composer, whose
    # only consumers are its two foreground CLIs.
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
        H1_MAPPED_RECOGNITION_RELATIVE_PATH,
        H1_REPLACEMENT_HANDOFF_RELATIVE_PATH,
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


def test_current_source_locator_is_a_leaf_owner_with_no_upward_import() -> None:
    """The locator sits strictly below MMI and owns no observation capability.

    ``mmi.contracts`` and ``mmi.source_capture`` may read the canonical checkout
    root and the two source paths from it.  Nothing else may consume it, and it
    may consume nothing upward — in particular no ``ctypes``, so the sole
    ``openat2`` authority remains ``mmi/source_capture.py``.
    """
    inventory = ltetf._scan_production_inventory(repo_root())
    imports_by_path = dict(inventory.imports_by_path)
    assert tuple(
        path
        for path in inventory.production_paths
        if "/production_inputs/" in path
    ) == PRODUCTION_INPUTS_PATHS
    for path in PRODUCTION_INPUTS_PATHS:
        imports = imports_by_path[path]
        assert not any(
            imported == prefix or imported.startswith(f"{prefix}.")
            for imported in imports
            for prefix in PROHIBITED_PRODUCTION_INPUTS_IMPORT_PREFIXES
        ), (path, imports)
        assert not any(
            imported == prefix or imported.startswith(f"{prefix}.")
            for imported in imports
            for prefix in PROHIBITED_IMPORT_PREFIXES
        ), (path, imports)
        assert {imported.split(".", 1)[0] for imported in imports} <= {
            "__future__",
            "os",
            "pathlib",
            "typing",
        }, (path, imports)
    locator_consumers = tuple(
        path
        for path, imports in inventory.imports_by_path
        if any(
            imported == CURRENT_SOURCE_LOCATOR_MODULE
            or imported.startswith(f"{CURRENT_SOURCE_LOCATOR_MODULE}.")
            for imported in imports
        )
    )
    assert set(locator_consumers) == set(
        MMI_CURRENT_SOURCE_LOCATOR_CONSUMERS
    )
    for path in MMI_CURRENT_SOURCE_LOCATOR_CONSUMERS:
        assert {
            imported
            for imported in imports_by_path[path]
            if imported.startswith("investment_orchestrator.production_inputs")
        } <= ALLOWED_CURRENT_SOURCE_LOCATOR_IMPORTS


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
        "src/investment_orchestrator/mmi/mmi_h1_prepared_handoff_v1.py",
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
        H1_REPLACEMENT_HANDOFF_RELATIVE_PATH,
    )
    assert consumers(
        "investment_orchestrator.mmi.grounded_prompt_v2"
    ) == (
        "src/investment_orchestrator/mmi/raw_response_envelope_v2.py",
        H2C_CONSUME_ENGINE_RELATIVE_PATH,
        H2C_CAPTURE_SESSION_RELATIVE_PATH,
        H2C_PREPARE_ENGINE_RELATIVE_PATH,
        H1_REPLACEMENT_HANDOFF_RELATIVE_PATH,
    )
    assert consumers(
        "investment_orchestrator.mmi.raw_response_envelope_v2"
    ) == (
        "src/investment_orchestrator/mmi/"
        "validated_grounded_analysis_response_v2.py",
        H2C_CONSUME_ENGINE_RELATIVE_PATH,
        H2C_CAPTURE_SESSION_RELATIVE_PATH,
        H1_MAPPED_RECOGNITION_RELATIVE_PATH,
        H1_REPLACEMENT_HANDOFF_RELATIVE_PATH,
    )
    assert consumers(
        "investment_orchestrator.mmi."
        "validated_grounded_analysis_response_v2"
    ) == (
        "src/investment_orchestrator/mmi/"
        "legacy_step1_compatibility_candidate_v1.py",
        H2C_CONSUME_ENGINE_RELATIVE_PATH,
        H2C_CAPTURE_SESSION_RELATIVE_PATH,
        H1_REPLACEMENT_HANDOFF_RELATIVE_PATH,
    )
    # The H1 mapping adapter is report-only; H2c remains foreground-only.
    assert consumers(
        "investment_orchestrator.mmi."
        "legacy_step1_compatibility_candidate_v1"
    ) == (
        H1_LEGACY_MAPPING_REPORT_RELATIVE_PATH,
        H2C_CONSUME_ENGINE_RELATIVE_PATH,
        H2C_CAPTURE_SESSION_RELATIVE_PATH,
        H2_COMPARISON_REPORT_RELATIVE_PATH,
        H1_REPLACEMENT_HANDOFF_RELATIVE_PATH,
    )
    # P2b: the current H1 mapping contract, the new prepared-handoff
    # contract, and the composer itself each have an exact closed consumer
    # set that stops at the two foreground CLIs.
    assert set(consumers(
        "investment_orchestrator.mmi.mmi_h1_legacy_step1_mapping_report_v1"
    )) == {
        H1_MAPPED_RECOGNITION_RELATIVE_PATH,
        H1_REPLACEMENT_HANDOFF_RELATIVE_PATH,
    }
    assert consumers(
        "investment_orchestrator.mmi.mmi_h1_prepared_handoff_v1"
    ) == (H1_REPLACEMENT_HANDOFF_RELATIVE_PATH,)
    assert set(consumers(
        "investment_orchestrator.workflow.h1_replacement_handoff"
    )) == {
        H1_REPLACEMENT_CONSUME_CLI_RELATIVE_PATH,
        H1_REPLACEMENT_PREPARE_CLI_RELATIVE_PATH,
    }
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
        "investment_orchestrator.common.stable_read"
    )) == {
        H2C_CONSUME_ENGINE_RELATIVE_PATH,
        H2C_ARCHIVED_SOURCE_RELATIVE_PATH,
        H1_REPLACEMENT_HANDOFF_RELATIVE_PATH,
    }
    # D4b: the dormant prepared-case contract gains exactly one production
    # consumer, the Phase A engine, which itself stays consumer-free.
    assert set(consumers(
        "investment_orchestrator.offline.mmi_h2c_prepared_case_v1"
    )) == {H2C_CONSUME_ENGINE_RELATIVE_PATH, H2C_PREPARE_ENGINE_RELATIVE_PATH, H2C_ARCHIVED_SOURCE_RELATIVE_PATH}
    assert consumers(
        "investment_orchestrator.offline.mmi_h2c_prepare_persisted_case_v1"
    ) == (H2C_PREPARE_CLI_RELATIVE_PATH,)
    assert consumers(
        "investment_orchestrator.offline.mmi_h2c_consume_persisted_case_v1"
    ) == (H2C_ARCHIVED_CONSUME_CLI_RELATIVE_PATH,)
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
        MmiSourceRole.LONG_HORIZON_RESEARCH: (
            "MMI_LONG_HORIZON_RESEARCH",
            "inputs/current/long_horizon_research.json",
            262_144,
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
    # 155 + exactly the two canonical CURRENT-source locator modules + P2a's
    # one current-lane validated-resumption owner + P2b's four current-lane
    # H1 replacement modules (contract owner, composer, two CLIs).
    assert len(inventory.production_paths) == 162
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
