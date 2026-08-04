from __future__ import annotations

import ast
from dataclasses import FrozenInstanceError, fields
import hashlib
import inspect
import json
import os
from pathlib import Path
import stat
from typing import Any

import pytest
import yaml

from investment_orchestrator.common.paths import prompt_path, repo_root
from investment_orchestrator.llm.legacy_step1_prompt_compiler import (
    compile_legacy_step1_prompt_text,
    derive_legacy_approved_extended_etf_json,
)
import investment_orchestrator as package
import investment_orchestrator.mmi as mmi
from investment_orchestrator.mmi.analyst_visible_evidence_view_v2 import (
    build_mmi_analyst_visible_evidence_view_v2,
    validate_mmi_analyst_visible_evidence_view_v2,
)
from investment_orchestrator.mmi.contracts import (
    MmiCapturedSource,
    MmiProjectionRunContext,
    MmiSourceRole,
)
from investment_orchestrator.mmi.evidence_bundle import (
    build_mmi_authenticated_evidence_bundle,
    validate_mmi_authenticated_evidence_bundle,
)
from investment_orchestrator.mmi.grounded_prompt_v2 import (
    MmiGroundedPromptV2Error,
    build_mmi_grounded_prompt_v2,
    validate_mmi_grounded_prompt_v2,
)
from investment_orchestrator.mmi.policy_projection import (
    build_mmi_policy_projection,
    validate_mmi_policy_projection,
)
from investment_orchestrator.mmi.portfolio_projection import (
    build_mmi_portfolio_snapshot_projection,
    validate_mmi_portfolio_snapshot_projection,
)
from investment_orchestrator.mmi.source_capture import (
    _capture_mmi_source_at_root,
)
from investment_orchestrator.offline import (
    mmi_h2c_prepare_persisted_case_v1 as engine,
)
from investment_orchestrator.offline.mmi_h2c_prepared_case_v1 import (
    validate_mmi_h2c_prepared_case_v1,
)


IDENTITY_FIELD = "prepared_case_identity_sha256"
IDENTITY_DOMAIN = b"mmi_h2c_prepared_case_v1\0"
TEMPLATE_RELATIVE = "prompts/research_dual_lane.txt"
PORTFOLIO_SECTION_START = (
    "(2a) existing_buy_open_orders_summary"
    "（optional, ticker-level summary; buy-side existing open orders SSOT）"
)
PORTFOLIO_SECTION_END = (
    "(2b) sell_open_orders"
    "（optional, lot-aware open sell orders summary）"
)
OPEN_BUY_HEADER = (
    "TICKER | budget | compiled_open_order_notional(optional) | "
    "residual_cash_not_allocated(optional) | template_id | "
    "anchor_baseline_last_close | anchor_price_asof | "
    "last_refresh_date_et(optional) | highest_live_limit(optional) | "
    "lowest_live_limit(optional) | live_step_count(optional) | "
    "live_order_steps_summary(optional) | "
    "live_order_qtys_summary(optional)"
)
CASE_DIRECTORIES = ("archive", "prompts", "prepared", "responses", "artifacts")
WRITTEN_LEAVES = (
    ("archive", "strategy_settings.yaml"),
    ("archive", "portfolio_snapshot.txt"),
    ("archive", "research_dual_lane.txt"),
    ("prompts", "h1_prompt.txt"),
    ("prompts", "legacy_prompt.txt"),
)
MANIFEST_LEAF = ("prepared", "prepared_case.json")
ABSENT_LEAVES = (
    ("responses", "h1_response.raw"),
    ("responses", "legacy_response.raw"),
    ("artifacts", "case_evidence_bundle.json"),
    ("artifacts", "comparison_report.json"),
    ("artifacts", "receipt.json"),
)
CASE_TREE_NAMES = (
    frozenset(CASE_DIRECTORIES)
    | {name for _, name in WRITTEN_LEAVES}
    | {MANIFEST_LEAF[1]}
    | {name for _, name in ABSENT_LEAVES}
)


# --------------------------------------------------------------------------
# Synthetic sources.  Nothing in this module reads ``inputs/current`` from the
# working tree; every source byte below is constructed here and installed into
# a temporary checkout root.
# --------------------------------------------------------------------------
def _settings(*, benchmark: str = "QQQ") -> dict[str, object]:
    return {
        "as_of": "2026-07-26",
        "run_timestamp_et": "2026-07-26 10:00 ET",
        "benchmark": benchmark,
        "hard_cap_open_orders_budget": 38211.29,
        "target_new_buy_budget_this_run": 12000.00,
        "relative_rotation_enabled": True,
        "relative_rotation_guardrails": {
            "require_same_role_for_rotation": True,
            "min_score_gap_to_rotate": 2,
            "do_not_rotate_if_current_holding_still_role_valid": True,
            "no_rotation_on_one_rank_change_only": True,
        },
        "core_universe": ["QQQ", "VOO", "VTI", "VT"],
        "satellite_universe": ["SMH", "IGV"],
        "user_approved_extended_etf_static_list": ["QUAL", "CIBR"],
        "user_approved_extended_etf_theme_map": {
            "QUAL": {"theme_bucket": "quality_factor"},
            "CIBR": {"theme_bucket": "cybersecurity"},
        },
        "active_shortlist_size_rule": {
            "benchmark_carrier": 1,
            "diversified_core_buffer_max": 1,
            "sector_alpha_tilt_max": 1,
            "extended_etf_minority_sleeve_max": 2,
        },
        "max_new_tickers_per_week": {
            "base_universe_new_tickers_per_week": 0,
            "extended_etf_sleeve_new_tickers_per_week": 2,
        },
        "extended_etf_constraints": {
            "sleeve_budget_cap_pct_of_total_open_orders": 0.35,
            "single_extended_etf_budget_cap_pct_of_total_open_orders": 0.20,
            "activation_minimum_effective_budget_pct_of_total_open_orders": (
                0.04
            ),
            "max_same_theme_extended_etf_count": 1,
            "max_same_theme_budget_pct_of_total_open_orders": 0.25,
            "require_distinct_theme_buckets_when_multiple_extended_etfs": True,
        },
    }


def _settings_bytes(**kwargs: object) -> bytes:
    return yaml.safe_dump(
        _settings(**kwargs),  # type: ignore[arg-type]
        sort_keys=False,
        allow_unicode=True,
    ).encode("utf-8")


def _portfolio_row(ticker: str, budget: str) -> str:
    return " | ".join(
        (
            ticker,
            budget,
            "",
            "",
            "T4-E",
            "700.00",
            "2026-07-20",
            "",
            "",
            "",
            "",
            "",
            "",
        )
    )


def _portfolio_bytes(*, budget: str = "100.00") -> bytes:
    return (
        "\n".join(
            (
                "【Portfolio Snapshot】",
                "# updated 2026-07-26",
                "(1) current_holdings_base",
                "PRIVATE_BROKER | QQQ | 9 | 123.45",
                PORTFOLIO_SECTION_START,
                "- exact code-owned explanatory line",
                OPEN_BUY_HEADER,
                _portfolio_row("QQQ", budget),
                _portfolio_row("ARKK", "200.00"),
                "",
                PORTFOLIO_SECTION_END,
                "PRIVATE_ACCOUNT | QQQ | raw sell instruction",
                "(3) LTCG_ELIGIBLE_SELLABLE",
                "QQQ | 9 | 2020-01-01 | private tax lot",
            )
        )
        + "\n"
    ).encode("utf-8")


def _install_sources(
    source_root: Path,
    *,
    settings_raw: bytes,
    portfolio_raw: bytes,
) -> None:
    for relative, raw in (
        ("inputs/current/strategy_settings.yaml", settings_raw),
        ("inputs/current/portfolio_snapshot.txt", portfolio_raw),
    ):
        path = source_root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(raw)


def _capture_at(source_root: Path):
    def capture(role: MmiSourceRole, *, expected_source_sha256: str):
        return _capture_mmi_source_at_root(
            source_root,
            role=role,
            expected_source_sha256=expected_source_sha256,
        )

    return capture


def _digest(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _canonical_bytes(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def _identity(value: dict[str, object]) -> str:
    preimage = {
        key: item for key, item in value.items() if key != IDENTITY_FIELD
    }
    encoded = _canonical_bytes(preimage)
    framed = IDENTITY_DOMAIN + len(encoded).to_bytes(8, "big") + encoded
    return hashlib.sha256(framed).hexdigest()


def _snapshot(value: object) -> object:
    return json.loads(_canonical_bytes(value))


class _Harness:
    """One temporary checkout plus one temporary case-root parent."""

    def __init__(
        self,
        base: Path,
        *,
        settings_raw: bytes,
        portfolio_raw: bytes,
    ) -> None:
        self.base = base
        self.settings_raw = settings_raw
        self.portfolio_raw = portfolio_raw
        self.source_root = base / "checkout"
        self.cases = base / "cases"
        self.cases.mkdir(parents=True, exist_ok=True)
        _install_sources(
            self.source_root,
            settings_raw=settings_raw,
            portfolio_raw=portfolio_raw,
        )
        self.run_contexts: list[MmiProjectionRunContext] = []
        self.path_opens: list[str] = []
        self.tree_operations: list[tuple[str, str, int]] = []
        self.case_name = "case-0001"
        self.recording = False

    def install(self, patcher: pytest.MonkeyPatch) -> None:
        patcher.setattr(
            engine,
            "capture_current_mmi_source",
            _capture_at(self.source_root),
        )
        real_begin = engine.begin_mmi_projection_run

        def begin() -> MmiProjectionRunContext:
            context = real_begin()
            self.run_contexts.append(context)
            return context

        patcher.setattr(engine, "begin_mmi_projection_run", begin)

        real_open = os.open
        real_mkdir = os.mkdir

        def _in_case_tree(path: object) -> bool:
            return path in CASE_TREE_NAMES or path == self.case_name

        def tracked_open(path, flags, mode=0o777, *, dir_fd=None):  # type: ignore[no-untyped-def]
            if dir_fd is None:
                self.path_opens.append(os.fspath(path))
            elif self.recording and _in_case_tree(path):
                self.tree_operations.append(("open", str(path), flags))
            return real_open(path, flags, mode, dir_fd=dir_fd)

        def tracked_mkdir(path, mode=0o777, *, dir_fd=None):  # type: ignore[no-untyped-def]
            # Recording starts at the one exclusive case-root creation, so
            # descriptor-relative source capture can never be mistaken for
            # case-tree persistence.
            if dir_fd is not None and path == self.case_name:
                self.recording = True
            if dir_fd is not None and self.recording and _in_case_tree(path):
                self.tree_operations.append(("mkdir", str(path), mode))
            return real_mkdir(path, mode, dir_fd=dir_fd)

        patcher.setattr(os, "open", tracked_open)
        patcher.setattr(os, "mkdir", tracked_mkdir)
        # The recorders stand in for the real syscall wrappers, so the
        # engine's genuine ``supports_dir_fd`` identity check must see them.
        patcher.setattr(
            os,
            "supports_dir_fd",
            frozenset(os.supports_dir_fd) | {tracked_open, tracked_mkdir},
        )

    def case_root(self, name: str = "case-0001") -> Path:
        return self.cases / name

    def prepare(
        self,
        *,
        name: str = "case-0001",
        settings_digest: str | None = None,
        portfolio_digest: str | None = None,
        case_root: Path | None = None,
    ) -> engine.H2cPrepareResult:
        self.case_name = (
            name if case_root is None else os.path.basename(str(case_root))
        )
        self.recording = False
        return engine.prepare_h2c_persisted_case(
            strategy_settings_expected_sha256=(
                _digest(self.settings_raw)
                if settings_digest is None
                else settings_digest
            ),
            portfolio_snapshot_expected_sha256=(
                _digest(self.portfolio_raw)
                if portfolio_digest is None
                else portfolio_digest
            ),
            case_root=(
                self.case_root(name) if case_root is None else case_root
            ),
        )


def _harness(
    patcher: pytest.MonkeyPatch,
    base: Path,
    *,
    settings_raw: bytes | None = None,
    portfolio_raw: bytes | None = None,
) -> _Harness:
    harness = _Harness(
        base,
        settings_raw=(
            _settings_bytes() if settings_raw is None else settings_raw
        ),
        portfolio_raw=(
            _portfolio_bytes() if portfolio_raw is None else portfolio_raw
        ),
    )
    harness.install(patcher)
    return harness


@pytest.fixture()
def harness(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> _Harness:
    return _harness(monkeypatch, tmp_path)


class _Prepared:
    def __init__(self, harness: _Harness) -> None:
        self.harness = harness
        self.result = harness.prepare()
        self.root = harness.case_root()
        self.manifest_path = self.root / "prepared/prepared_case.json"
        self.manifest_bytes = self.manifest_path.read_bytes()
        self.manifest = json.loads(self.manifest_bytes)
        self.run_context = harness.run_contexts[0]


@pytest.fixture(scope="module")
def prepared(tmp_path_factory: pytest.TempPathFactory) -> _Prepared:
    with pytest.MonkeyPatch.context() as patcher:
        base = tmp_path_factory.mktemp("prepared")
        return _Prepared(_harness(patcher, base))


def _tree(root: Path) -> list[str]:
    return sorted(
        item.relative_to(root).as_posix() for item in root.rglob("*")
    )


# --------------------------------------------------------------------------
# Public surface
# --------------------------------------------------------------------------
def test_public_surface_and_signature_are_exact() -> None:
    assert engine.__all__ == (
        "H2cPrepareError",
        "H2cPrepareErrorCode",
        "H2cPrepareFailureClass",
        "H2cPrepareResult",
        "prepare_h2c_persisted_case",
    )
    signature = inspect.signature(engine.prepare_h2c_persisted_case)
    assert tuple(signature.parameters) == (
        "strategy_settings_expected_sha256",
        "portfolio_snapshot_expected_sha256",
        "case_root",
    )
    assert all(
        parameter.kind is inspect.Parameter.KEYWORD_ONLY
        for parameter in signature.parameters.values()
    )
    assert signature.return_annotation == "H2cPrepareResult"


def test_result_shape_is_exactly_two_frozen_fields() -> None:
    result = engine.H2cPrepareResult(
        workflow_status="AWAITING_OPERATOR_RESPONSES",
        prepared_case_identity_sha256="0" * 64,
    )
    assert tuple(field.name for field in fields(result)) == (
        "workflow_status",
        "prepared_case_identity_sha256",
    )
    assert engine.H2cPrepareResult.__slots__ == (
        "workflow_status",
        "prepared_case_identity_sha256",
    )
    with pytest.raises(FrozenInstanceError):
        result.workflow_status = "OTHER"  # type: ignore[misc]
    serialized = json.dumps(
        {
            "workflow_status": result.workflow_status,
            IDENTITY_FIELD: result.prepared_case_identity_sha256,
        }
    ).casefold()
    for forbidden in (
        "provider",
        "model",
        "availability",
        "permission",
        "publication",
        "order",
        "broker",
        "execution",
        "hold",
        "sell",
    ):
        assert forbidden not in serialized


def test_error_codes_and_failure_classes_are_exact() -> None:
    assert {code.value for code in engine.H2cPrepareErrorCode} == {
        "H2C_PREPARE_ARGUMENT_INVALID",
        "H2C_PREPARE_PATH_CONTRACT_INVALID",
        "H2C_PREPARE_CAPABILITY_UNAVAILABLE",
        "H2C_PREPARE_SOURCE_CAPTURE_INVALID",
        "H2C_PREPARE_PORTFOLIO_NOT_COMPARABLE",
        "H2C_PREPARE_LIVE_CHAIN_INVALID",
        "H2C_PREPARE_PROMPT_CONTRACT_INVALID",
        "H2C_PREPARE_LEGACY_COMPILER_INVALID",
        "H2C_PREPARE_MANIFEST_INVALID",
        "H2C_PREPARE_PERSISTENCE_FAILED",
    }
    assert len(engine.H2cPrepareErrorCode) == 10
    assert {item.value for item in engine.H2cPrepareFailureClass} == {
        "ARTIFACT_CONTENT",
        "AVAILABILITY_PERMISSION",
        "COMPILER_NORMALIZER",
        "OPERATOR_INPUT",
        "PERSISTENCE",
        "PROMPT_CONTRACT",
        "VALIDATOR_SCHEMA",
    }
    assert len(engine.H2cPrepareFailureClass) == 7
    assert set(engine._ERROR_CLASSES) == set(engine.H2cPrepareErrorCode)
    codes = engine.H2cPrepareErrorCode
    classes = engine.H2cPrepareFailureClass
    assert engine._ERROR_CLASSES == {
        codes.H2C_PREPARE_ARGUMENT_INVALID: classes.OPERATOR_INPUT,
        codes.H2C_PREPARE_PATH_CONTRACT_INVALID: classes.OPERATOR_INPUT,
        codes.H2C_PREPARE_CAPABILITY_UNAVAILABLE: (
            classes.AVAILABILITY_PERMISSION
        ),
        codes.H2C_PREPARE_SOURCE_CAPTURE_INVALID: classes.ARTIFACT_CONTENT,
        codes.H2C_PREPARE_PORTFOLIO_NOT_COMPARABLE: classes.ARTIFACT_CONTENT,
        codes.H2C_PREPARE_LIVE_CHAIN_INVALID: classes.VALIDATOR_SCHEMA,
        codes.H2C_PREPARE_PROMPT_CONTRACT_INVALID: classes.PROMPT_CONTRACT,
        codes.H2C_PREPARE_LEGACY_COMPILER_INVALID: (
            classes.COMPILER_NORMALIZER
        ),
        codes.H2C_PREPARE_MANIFEST_INVALID: classes.VALIDATOR_SCHEMA,
        codes.H2C_PREPARE_PERSISTENCE_FAILED: classes.PERSISTENCE,
    }
    with pytest.raises(ValueError):
        engine.H2cPrepareError(
            code=codes.H2C_PREPARE_ARGUMENT_INVALID,
            failure_class=classes.PERSISTENCE,
        )
    with pytest.raises(TypeError):
        engine.H2cPrepareError(
            code="H2C_PREPARE_ARGUMENT_INVALID",  # type: ignore[arg-type]
            failure_class=classes.OPERATOR_INPUT,
        )


# --------------------------------------------------------------------------
# Preflight: nothing may be created before every check passes
# --------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("settings_digest", "portfolio_digest"),
    (
        ("nothex", None),
        (None, "A" * 64),
        ("0" * 63, None),
        (None, ""),
    ),
)
def test_malformed_digests_are_rejected_before_any_side_effect(
    harness: _Harness,
    settings_digest: str | None,
    portfolio_digest: str | None,
) -> None:
    with pytest.raises(engine.H2cPrepareError) as captured:
        harness.prepare(
            settings_digest=settings_digest,
            portfolio_digest=portfolio_digest,
        )
    assert captured.value.code is (
        engine.H2cPrepareErrorCode.H2C_PREPARE_ARGUMENT_INVALID
    )
    assert captured.value.failure_class is (
        engine.H2cPrepareFailureClass.OPERATOR_INPUT
    )
    assert not harness.case_root().exists()
    assert harness.run_contexts == []


def test_non_path_case_root_is_an_argument_failure(
    harness: _Harness,
) -> None:
    with pytest.raises(engine.H2cPrepareError) as captured:
        harness.prepare(case_root=str(harness.case_root()))  # type: ignore[arg-type]
    assert captured.value.code is (
        engine.H2cPrepareErrorCode.H2C_PREPARE_ARGUMENT_INVALID
    )
    assert not harness.case_root().exists()


def test_rejected_case_roots_never_create_or_modify_anything(
    harness: _Harness,
    tmp_path: Path,
) -> None:
    existing_directory = harness.cases / "existing-directory"
    existing_directory.mkdir()
    (existing_directory / "keep").write_bytes(b"operator evidence\n")
    existing_file = harness.cases / "existing-file"
    existing_file.write_bytes(b"operator evidence\n")
    dangling = harness.cases / "dangling-symlink"
    dangling.symlink_to(harness.cases / "absent-target")
    linked = harness.cases / "linked-directory"
    linked.symlink_to(existing_directory, target_is_directory=True)
    unsafe_parent_file = harness.cases / "parent-is-a-file"
    unsafe_parent_file.write_bytes(b"not a directory\n")
    linked_parent = harness.cases / "linked-parent"
    linked_parent.symlink_to(harness.cases, target_is_directory=True)

    rejected = {
        "relative": Path("relative-case"),
        "root": Path("/"),
        "dot": Path(f"{harness.cases}/."),
        "existing_directory": existing_directory,
        "existing_file": existing_file,
        "dangling_symlink": dangling,
        "linked_directory": linked,
        "missing_parent": harness.cases / "absent" / "case",
        "file_parent": unsafe_parent_file / "case",
        "symlinked_parent": linked_parent / "case",
    }
    before = sorted(
        (item.name, item.is_symlink(), item.is_dir())
        for item in harness.cases.iterdir()
    )
    for label, candidate in rejected.items():
        with pytest.raises(engine.H2cPrepareError) as captured:
            harness.prepare(case_root=candidate)
        assert captured.value.code is (
            engine.H2cPrepareErrorCode.H2C_PREPARE_PATH_CONTRACT_INVALID
        ), label
        assert captured.value.failure_class is (
            engine.H2cPrepareFailureClass.OPERATOR_INPUT
        ), label
    assert (
        sorted(
            (item.name, item.is_symlink(), item.is_dir())
            for item in harness.cases.iterdir()
        )
        == before
    )
    assert (existing_directory / "keep").read_bytes() == (
        b"operator evidence\n"
    )
    assert existing_file.read_bytes() == b"operator evidence\n"
    assert harness.run_contexts == []


def test_absent_directory_capability_fails_closed(
    harness: _Harness,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        os,
        "supports_dir_fd",
        frozenset(os.supports_dir_fd) - {os.mkdir},
    )
    with pytest.raises(engine.H2cPrepareError) as captured:
        harness.prepare()
    assert captured.value.code is (
        engine.H2cPrepareErrorCode.H2C_PREPARE_CAPABILITY_UNAVAILABLE
    )
    assert captured.value.failure_class is (
        engine.H2cPrepareFailureClass.AVAILABILITY_PERMISSION
    )
    assert not harness.case_root().exists()


def test_clock_contract_failure_is_a_capability_failure(
    harness: _Harness,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail() -> MmiProjectionRunContext:
        raise engine.MmiClockContractError("MMI_CLOCK_READ_FAILED")

    monkeypatch.setattr(engine, "begin_mmi_projection_run", fail)
    with pytest.raises(engine.H2cPrepareError) as captured:
        harness.prepare()
    assert captured.value.code is (
        engine.H2cPrepareErrorCode.H2C_PREPARE_CAPABILITY_UNAVAILABLE
    )
    assert not harness.case_root().exists()


# --------------------------------------------------------------------------
# Runtime source semantics
# --------------------------------------------------------------------------
def test_exactly_one_evaluation_timestamp_reaches_the_manifest(
    prepared: _Prepared,
) -> None:
    assert len(prepared.harness.run_contexts) == 1
    assert prepared.manifest["evaluation_timestamp_utc"] == (
        prepared.run_context.evaluation_timestamp_utc
    )


def test_caller_digests_bind_the_exact_runtime_source_bytes(
    harness: _Harness,
) -> None:
    observed: list[tuple[MmiSourceRole, str]] = []
    real_capture = engine.capture_current_mmi_source

    def capture(role: MmiSourceRole, *, expected_source_sha256: str):
        observed.append((role, expected_source_sha256))
        return real_capture(
            role,
            expected_source_sha256=expected_source_sha256,
        )

    engine.capture_current_mmi_source = capture  # type: ignore[assignment]
    try:
        harness.prepare()
    finally:
        engine.capture_current_mmi_source = real_capture  # type: ignore[assignment]
    assert observed == [
        (MmiSourceRole.STRATEGY_SETTINGS, _digest(harness.settings_raw)),
        (MmiSourceRole.PORTFOLIO_SNAPSHOT, _digest(harness.portfolio_raw)),
    ]
    root = harness.case_root()
    assert (root / "archive/strategy_settings.yaml").read_bytes() == (
        harness.settings_raw
    )
    assert (root / "archive/portfolio_snapshot.txt").read_bytes() == (
        harness.portfolio_raw
    )


@pytest.mark.parametrize("role", ("settings", "portfolio"))
def test_digest_mismatch_fails_closed_without_a_case(
    harness: _Harness,
    role: str,
) -> None:
    mismatch = _digest(b"a source this operator never supplied")
    with pytest.raises(engine.H2cPrepareError) as captured:
        harness.prepare(
            settings_digest=mismatch if role == "settings" else None,
            portfolio_digest=mismatch if role == "portfolio" else None,
        )
    assert captured.value.code is (
        engine.H2cPrepareErrorCode.H2C_PREPARE_SOURCE_CAPTURE_INVALID
    )
    assert captured.value.failure_class is (
        engine.H2cPrepareFailureClass.ARTIFACT_CONTENT
    )
    assert captured.value.owner_reason_codes
    assert all(
        code.startswith("MMI_SOURCE_")
        for code in captured.value.owner_reason_codes
    )
    assert not harness.case_root().exists()


def test_operator_updated_current_inputs_are_accepted_when_hashes_match(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    updated_settings = _settings_bytes(benchmark="VOO")
    updated_portfolio = _portfolio_bytes(budget="321.00")
    assert updated_settings != _settings_bytes()
    assert updated_portfolio != _portfolio_bytes()
    harness = _harness(
        monkeypatch,
        tmp_path,
        settings_raw=updated_settings,
        portfolio_raw=updated_portfolio,
    )
    result = harness.prepare()
    assert result.workflow_status == "AWAITING_OPERATOR_RESPONSES"
    root = harness.case_root()
    assert (root / "archive/strategy_settings.yaml").read_bytes() == (
        updated_settings
    )
    assert (root / "archive/portfolio_snapshot.txt").read_bytes() == (
        updated_portfolio
    )


def test_blank_portfolio_content_is_not_comparable(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    harness = _harness(monkeypatch, tmp_path, portfolio_raw=b"   \n\t\n")
    with pytest.raises(engine.H2cPrepareError) as captured:
        harness.prepare()
    assert captured.value.code is (
        engine.H2cPrepareErrorCode.H2C_PREPARE_PORTFOLIO_NOT_COMPARABLE
    )
    assert captured.value.failure_class is (
        engine.H2cPrepareFailureClass.ARTIFACT_CONTENT
    )
    assert not harness.case_root().exists()


def test_stable_template_read_rejects_an_unstable_descriptor(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    template = tmp_path / "template.txt"
    template.write_bytes(b"exact template\n")
    assert engine._stable_read_legacy_template(template) == (
        b"exact template\n"
    )

    real_fstat = os.fstat
    calls: list[int] = []

    def unstable(fd: int) -> os.stat_result:
        calls.append(fd)
        status = real_fstat(fd)
        if len(calls) == 2:
            template.write_bytes(b"a different template\n")
            return real_fstat(fd)
        return status

    monkeypatch.setattr(engine.os, "fstat", unstable)
    with pytest.raises(engine.H2cPrepareError) as captured:
        engine._stable_read_legacy_template(template)
    assert captured.value.code is (
        engine.H2cPrepareErrorCode.H2C_PREPARE_LEGACY_COMPILER_INVALID
    )
    assert captured.value.failure_class is (
        engine.H2cPrepareFailureClass.COMPILER_NORMALIZER
    )


@pytest.mark.parametrize("kind", ("symlink", "empty", "directory"))
def test_unsafe_template_paths_fail_closed(
    harness: _Harness,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    kind: str,
) -> None:
    target = tmp_path / "template-target.txt"
    target.write_bytes(b"exact template\n")
    if kind == "symlink":
        candidate = tmp_path / "template-link.txt"
        candidate.symlink_to(target)
    elif kind == "empty":
        candidate = tmp_path / "template-empty.txt"
        candidate.write_bytes(b"")
    else:
        candidate = tmp_path / "template-directory"
        candidate.mkdir()
    monkeypatch.setattr(engine, "prompt_path", lambda _name: candidate)
    with pytest.raises(engine.H2cPrepareError) as captured:
        harness.prepare()
    assert captured.value.code is (
        engine.H2cPrepareErrorCode.H2C_PREPARE_LEGACY_COMPILER_INVALID
    )
    assert not harness.case_root().exists()


def test_legacy_compiler_failure_is_controlled(
    harness: _Harness,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail(**_kwargs: object) -> str:
        raise engine.PromptRenderError("controlled")

    monkeypatch.setattr(engine, "compile_legacy_step1_prompt_text", fail)
    with pytest.raises(engine.H2cPrepareError) as captured:
        harness.prepare()
    assert captured.value.code is (
        engine.H2cPrepareErrorCode.H2C_PREPARE_LEGACY_COMPILER_INVALID
    )
    assert not harness.case_root().exists()


@pytest.mark.parametrize(
    ("owner_code", "expected"),
    (
        (
            "MMI_GROUNDED_PROMPT_V2_TEXT_INVALID",
            "H2C_PREPARE_PROMPT_CONTRACT_INVALID",
        ),
        (
            "MMI_GROUNDED_PROMPT_V2_SCHEMA_INVALID",
            "H2C_PREPARE_LIVE_CHAIN_INVALID",
        ),
    ),
)
def test_grounded_prompt_failures_map_to_exact_public_codes(
    harness: _Harness,
    monkeypatch: pytest.MonkeyPatch,
    owner_code: str,
    expected: str,
) -> None:
    def fail(**_kwargs: object) -> object:
        raise MmiGroundedPromptV2Error(owner_code)

    monkeypatch.setattr(engine, "build_mmi_grounded_prompt_v2", fail)
    with pytest.raises(engine.H2cPrepareError) as captured:
        harness.prepare()
    assert captured.value.code.value == expected
    assert captured.value.owner_reason_codes == (owner_code,)
    assert not harness.case_root().exists()


def test_undocumented_owner_code_remains_a_true_bug(
    harness: _Harness,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    error = MmiGroundedPromptV2Error(
        "MMI_GROUNDED_PROMPT_V2_TEXT_INVALID"
    )
    error.code = "UNKNOWN"  # type: ignore[assignment]

    def fail(**_kwargs: object) -> object:
        raise error

    monkeypatch.setattr(engine, "build_mmi_grounded_prompt_v2", fail)
    with pytest.raises(RuntimeError, match="undocumented MMI owner"):
        harness.prepare()
    assert not harness.case_root().exists()


def test_live_chain_owners_are_all_built_and_validated(
    harness: _Harness,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []
    for name in (
        "build_mmi_policy_projection",
        "validate_mmi_policy_projection",
        "build_mmi_portfolio_snapshot_projection",
        "validate_mmi_portfolio_snapshot_projection",
        "build_mmi_analyst_visible_evidence_view_v2",
        "validate_mmi_analyst_visible_evidence_view_v2",
        "build_mmi_grounded_prompt_v2",
        "validate_mmi_grounded_prompt_v2",
    ):
        real = getattr(engine, name)

        def record(*args: object, __name: str = name, __real: Any = real, **kwargs: object):
            calls.append(__name)
            return __real(*args, **kwargs)

        monkeypatch.setattr(engine, name, record)
    for name in (
        "build_mmi_authenticated_evidence_bundle",
        "validate_mmi_authenticated_evidence_bundle",
    ):
        real = getattr(engine._evidence_bundle, name)

        def record_bundle(*args: object, __name: str = name, __real: Any = real, **kwargs: object):
            calls.append(__name)
            return __real(*args, **kwargs)

        monkeypatch.setattr(engine._evidence_bundle, name, record_bundle)

    harness.prepare()
    expected = [
        "build_mmi_policy_projection",
        "validate_mmi_policy_projection",
        "build_mmi_portfolio_snapshot_projection",
        "validate_mmi_portfolio_snapshot_projection",
        "build_mmi_authenticated_evidence_bundle",
        "validate_mmi_authenticated_evidence_bundle",
        "build_mmi_analyst_visible_evidence_view_v2",
        "validate_mmi_analyst_visible_evidence_view_v2",
        "build_mmi_grounded_prompt_v2",
        "validate_mmi_grounded_prompt_v2",
    ]
    assert set(calls) == set(expected)
    # Downstream owners independently re-validate their upstream inputs, so
    # only each owner's first invocation is ordered by this engine.
    first = {name: calls.index(name) for name in expected}
    assert sorted(expected, key=lambda name: first[name]) == expected


# --------------------------------------------------------------------------
# Complete independent manifest equality and identity
# --------------------------------------------------------------------------
def _rebuild_chain(
    harness: _Harness,
    run_context: MmiProjectionRunContext,
) -> tuple[MmiCapturedSource, MmiCapturedSource, dict[str, object]]:
    capture = _capture_at(harness.source_root)
    settings_capture = capture(
        MmiSourceRole.STRATEGY_SETTINGS,
        expected_source_sha256=_digest(harness.settings_raw),
    )
    portfolio_capture = capture(
        MmiSourceRole.PORTFOLIO_SNAPSHOT,
        expected_source_sha256=_digest(harness.portfolio_raw),
    )
    settings_source = settings_capture.source
    portfolio_source = portfolio_capture.source
    assert settings_source is not None and portfolio_source is not None
    policy = build_mmi_policy_projection(
        settings_source,
        run_context=run_context,
    ).projection
    assert validate_mmi_policy_projection(
        policy,
        source=settings_source,
        run_context=run_context,
    ).valid
    portfolio = build_mmi_portfolio_snapshot_projection(
        portfolio_source,
        policy_projection=policy,
        policy_source=settings_source,
        run_context=run_context,
    ).projection
    assert validate_mmi_portfolio_snapshot_projection(
        portfolio,
        portfolio_source=portfolio_source,
        policy_projection=policy,
        policy_source=settings_source,
        run_context=run_context,
    ).valid
    evidence = build_mmi_authenticated_evidence_bundle(
        policy_projection=policy,
        policy_source=settings_source,
        portfolio_projection=portfolio,
        portfolio_source=portfolio_source,
        run_context=run_context,
    ).projection
    assert validate_mmi_authenticated_evidence_bundle(
        evidence,
        policy_projection=policy,
        policy_source=settings_source,
        portfolio_projection=portfolio,
        portfolio_source=portfolio_source,
        run_context=run_context,
    ).valid
    view = build_mmi_analyst_visible_evidence_view_v2(
        evidence_bundle=evidence,
        policy_projection=policy,
        policy_source=settings_source,
        portfolio_projection=portfolio,
        portfolio_source=portfolio_source,
        run_context=run_context,
    ).projection
    assert validate_mmi_analyst_visible_evidence_view_v2(
        value=view,
        evidence_bundle=evidence,
        policy_projection=policy,
        policy_source=settings_source,
        portfolio_projection=portfolio,
        portfolio_source=portfolio_source,
        run_context=run_context,
    ).valid
    prompt = validate_mmi_grounded_prompt_v2(
        value=build_mmi_grounded_prompt_v2(
            analyst_visible_evidence_view=view,
            evidence_bundle=evidence,
            policy_projection=policy,
            policy_source=settings_source,
            portfolio_projection=portfolio,
            portfolio_source=portfolio_source,
            run_context=run_context,
        ),
        evidence_bundle=evidence,
        policy_projection=policy,
        policy_source=settings_source,
        portfolio_projection=portfolio,
        portfolio_source=portfolio_source,
        run_context=run_context,
    )
    return settings_source, portfolio_source, prompt


def test_persisted_prompts_equal_independent_recomputation(
    prepared: _Prepared,
) -> None:
    harness = prepared.harness
    _, _, prompt = _rebuild_chain(harness, prepared.run_context)
    h1_bytes = (prepared.root / "prompts/h1_prompt.txt").read_bytes()
    assert h1_bytes == prompt["prompt_text"].encode("utf-8")

    template_bytes = prompt_path("research_dual_lane.txt").read_bytes()
    assert (
        prepared.root / "archive/research_dual_lane.txt"
    ).read_bytes() == template_bytes

    def universal(raw: bytes) -> str:
        return raw.decode("utf-8").replace("\r\n", "\n").replace("\r", "\n")

    settings_text = universal(harness.settings_raw)
    legacy_expected = compile_legacy_step1_prompt_text(
        template_text=universal(template_bytes),
        strategy_settings_text=settings_text,
        portfolio_snapshot_text=universal(harness.portfolio_raw),
        approved_extended_etf_json=(
            derive_legacy_approved_extended_etf_json(
                strategy_settings_text=settings_text
            )
        ),
    ).encode("utf-8")
    assert (
        prepared.root / "prompts/legacy_prompt.txt"
    ).read_bytes() == legacy_expected


def test_complete_manifest_equals_an_independent_expected_mapping(
    prepared: _Prepared,
) -> None:
    harness = prepared.harness
    settings_source, portfolio_source, prompt = _rebuild_chain(
        harness,
        prepared.run_context,
    )
    template_bytes = (
        prepared.root / "archive/research_dual_lane.txt"
    ).read_bytes()
    h1_bytes = (prepared.root / "prompts/h1_prompt.txt").read_bytes()
    legacy_bytes = (prepared.root / "prompts/legacy_prompt.txt").read_bytes()
    expected: dict[str, object] = {
        "schema_version": "mmi_h2c_prepared_case_v1",
        "artifact_kind": "MMI_H2C_PREPARED_CASE",
        "preparation_contract_version": (
            "mmi_h2c_persisted_case_prepare_v1"
        ),
        "report_only": True,
        "authority_effect": "NONE",
        "workflow_status": "AWAITING_OPERATOR_RESPONSES",
        "evaluation_timestamp_utc": (
            prepared.run_context.evaluation_timestamp_utc
        ),
        "strategy_settings_source": {
            "source_record": _snapshot(dict(settings_source.source_record)),
            "archive_relative_path": "archive/strategy_settings.yaml",
        },
        "portfolio_snapshot_source": {
            "source_record": _snapshot(dict(portfolio_source.source_record)),
            "archive_relative_path": "archive/portfolio_snapshot.txt",
        },
        "legacy_prompt_template": {
            "repository_relative_locator": TEMPLATE_RELATIVE,
            "archive_relative_path": "archive/research_dual_lane.txt",
            "byte_length": len(template_bytes),
            "sha256": hashlib.sha256(template_bytes).hexdigest(),
        },
        "grounded_prompt": _snapshot(dict(prompt)),
        "h1_prompt": {
            "relative_path": "prompts/h1_prompt.txt",
            "byte_length": len(h1_bytes),
            "sha256": hashlib.sha256(h1_bytes).hexdigest(),
        },
        "legacy_prompt": {
            "relative_path": "prompts/legacy_prompt.txt",
            "byte_length": len(legacy_bytes),
            "sha256": hashlib.sha256(legacy_bytes).hexdigest(),
            "compiler_contract_version": (
                "mmi_legacy_step1_compatibility_compiler_v1"
            ),
        },
        "response_leaves": {
            "h1": "responses/h1_response.raw",
            "legacy": "responses/legacy_response.raw",
        },
        "result_leaves": {
            "case_evidence_bundle": "artifacts/case_evidence_bundle.json",
            "comparison_report": "artifacts/comparison_report.json",
            "receipt": "artifacts/receipt.json",
        },
    }
    expected[IDENTITY_FIELD] = _identity(expected)

    assert prepared.manifest == expected
    assert prepared.manifest_bytes == _canonical_bytes(expected)
    assert prepared.result.prepared_case_identity_sha256 == (
        expected[IDENTITY_FIELD]
    )
    assert prepared.manifest[IDENTITY_FIELD] == expected[IDENTITY_FIELD]
    validate_mmi_h2c_prepared_case_v1(prepared_case=prepared.manifest)


def test_declared_case_relative_paths_are_frozen(
    prepared: _Prepared,
) -> None:
    assert engine._DECLARED_CASE_RELATIVE_PATHS == (
        "archive/strategy_settings.yaml",
        "archive/portfolio_snapshot.txt",
        "archive/research_dual_lane.txt",
        "prompts/h1_prompt.txt",
        "prompts/legacy_prompt.txt",
        "responses/h1_response.raw",
        "responses/legacy_response.raw",
        "artifacts/case_evidence_bundle.json",
        "artifacts/comparison_report.json",
        "artifacts/receipt.json",
    )
    manifest = prepared.manifest
    assert (
        manifest["strategy_settings_source"]["archive_relative_path"],
        manifest["portfolio_snapshot_source"]["archive_relative_path"],
        manifest["legacy_prompt_template"]["archive_relative_path"],
        manifest["h1_prompt"]["relative_path"],
        manifest["legacy_prompt"]["relative_path"],
        manifest["response_leaves"]["h1"],
        manifest["response_leaves"]["legacy"],
        manifest["result_leaves"]["case_evidence_bundle"],
        manifest["result_leaves"]["comparison_report"],
        manifest["result_leaves"]["receipt"],
    ) == engine._DECLARED_CASE_RELATIVE_PATHS
    # The frozen envelope declares no location for itself.
    assert "prepared/prepared_case.json" not in json.dumps(manifest)


def test_manifest_layout_divergence_fails_closed(
    harness: _Harness,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    real_build = engine._prepared_case._build_mmi_h2c_prepared_case_v1

    def drifted(**kwargs: object) -> dict[str, object]:
        prepared = real_build(**kwargs)
        prepared["h1_prompt"]["relative_path"] = "prompts/other.txt"
        return prepared

    monkeypatch.setattr(
        engine._prepared_case,
        "_build_mmi_h2c_prepared_case_v1",
        drifted,
    )
    with pytest.raises(engine.H2cPrepareError) as captured:
        harness.prepare()
    assert captured.value.code is (
        engine.H2cPrepareErrorCode.H2C_PREPARE_MANIFEST_INVALID
    )
    assert captured.value.failure_class is (
        engine.H2cPrepareFailureClass.VALIDATOR_SCHEMA
    )
    assert not harness.case_root().exists()


def test_frozen_owner_rejection_is_a_manifest_failure(
    harness: _Harness,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail(**_kwargs: object) -> dict[str, object]:
        raise engine._prepared_case.MmiH2cPreparedCaseV1Error(
            "MMI_H2C_PREPARED_CASE_V1_INVALID"
        )

    monkeypatch.setattr(
        engine._prepared_case,
        "_build_mmi_h2c_prepared_case_v1",
        fail,
    )
    with pytest.raises(engine.H2cPrepareError) as captured:
        harness.prepare()
    assert captured.value.code is (
        engine.H2cPrepareErrorCode.H2C_PREPARE_MANIFEST_INVALID
    )
    assert captured.value.owner_reason_codes == (
        "MMI_H2C_PREPARED_CASE_V1_INVALID",
    )
    assert not harness.case_root().exists()


def test_canonicalization_precedes_the_first_persistent_write(
    harness: _Harness,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail(*_args: object, **_kwargs: object) -> bytes:
        raise engine.MmiCanonicalizationError("CONTROLLED")

    monkeypatch.setattr(engine, "canonical_json_bytes", fail)
    with pytest.raises(engine.H2cPrepareError) as captured:
        harness.prepare()
    assert captured.value.code is (
        engine.H2cPrepareErrorCode.H2C_PREPARE_MANIFEST_INVALID
    )
    assert not harness.case_root().exists()
    assert list(harness.cases.iterdir()) == []


# --------------------------------------------------------------------------
# Persistence
# --------------------------------------------------------------------------
def test_exact_tree_modes_and_absent_leaves(prepared: _Prepared) -> None:
    root = prepared.root
    assert _tree(root) == [
        "archive",
        "archive/portfolio_snapshot.txt",
        "archive/research_dual_lane.txt",
        "archive/strategy_settings.yaml",
        "artifacts",
        "prepared",
        "prepared/prepared_case.json",
        "prompts",
        "prompts/h1_prompt.txt",
        "prompts/legacy_prompt.txt",
        "responses",
    ]
    assert stat.S_IMODE(root.stat().st_mode) == 0o700
    for directory in CASE_DIRECTORIES:
        assert stat.S_IMODE((root / directory).stat().st_mode) == 0o700
    for directory, name in WRITTEN_LEAVES + (MANIFEST_LEAF,):
        leaf = root / directory / name
        assert stat.S_IMODE(leaf.stat().st_mode) == 0o600
        assert not leaf.is_symlink()
    for directory, name in ABSENT_LEAVES:
        assert not (root / directory / name).exists()
    assert list((root / "responses").iterdir()) == []
    assert list((root / "artifacts").iterdir()) == []


def test_persistence_order_flags_and_manifest_is_written_last(
    prepared: _Prepared,
) -> None:
    operations = prepared.harness.tree_operations
    directory_flags = (
        os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
    )
    create_flags = (
        os.O_WRONLY
        | os.O_CREAT
        | os.O_EXCL
        | os.O_NOFOLLOW
        | os.O_CLOEXEC
    )
    read_flags = (
        os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK
    )
    expected: list[tuple[str, str, int]] = [
        ("mkdir", "case-0001", 0o700),
        ("open", "case-0001", directory_flags),
    ]
    for directory in CASE_DIRECTORIES:
        expected.append(("mkdir", directory, 0o700))
        expected.append(("open", directory, directory_flags))
    for _, name in WRITTEN_LEAVES:
        expected.append(("open", name, create_flags))
    for _, name in WRITTEN_LEAVES:
        expected.append(("open", name, read_flags))
    expected.append(("open", MANIFEST_LEAF[1], create_flags))
    expected.append(("open", MANIFEST_LEAF[1], read_flags))
    assert operations == expected
    creations = [
        name
        for kind, name, flags in operations
        if kind == "open" and flags == create_flags
    ]
    assert creations[-1] == "prepared_case.json"


def test_no_response_leaf_and_no_live_input_is_ever_opened(
    prepared: _Prepared,
) -> None:
    opened_names = {name for _, name, _ in prepared.harness.tree_operations}
    assert opened_names.isdisjoint({name for _, name in ABSENT_LEAVES})
    for path in prepared.harness.path_opens:
        assert "inputs/current" not in path
    template = str(prompt_path("research_dual_lane.txt"))
    assert template in prepared.harness.path_opens
    assert str(prepared.harness.cases) in prepared.harness.path_opens


def test_writes_are_fsynced_for_file_and_parent_directory(
    harness: _Harness,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    real_fsync = os.fsync
    synced: list[int] = []

    def tracked(fd: int) -> None:
        synced.append(fd)
        return real_fsync(fd)

    monkeypatch.setattr(engine.os, "fsync", tracked)
    harness.prepare()
    # six directories (each fsyncing its parent) plus, for each of the six
    # created files, one file fsync and one containing-directory fsync.
    assert len(synced) == 6 + 6 * 2


def test_partial_writes_are_completed_by_the_write_loop(
    harness: _Harness,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    real_write = os.write

    def one_byte(fd: int, data: bytes) -> int:
        return real_write(fd, bytes(data[:1]))

    monkeypatch.setattr(engine.os, "write", one_byte)
    harness.prepare()
    root = harness.case_root()
    assert (root / "archive/strategy_settings.yaml").read_bytes() == (
        harness.settings_raw
    )
    manifest = json.loads(
        (root / "prepared/prepared_case.json").read_bytes()
    )
    validate_mmi_h2c_prepared_case_v1(prepared_case=manifest)


def test_a_nonpositive_write_result_fails_closed(
    harness: _Harness,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(engine.os, "write", lambda _fd, _data: 0)
    with pytest.raises(engine.H2cPrepareError) as captured:
        harness.prepare()
    assert captured.value.code is (
        engine.H2cPrepareErrorCode.H2C_PREPARE_PERSISTENCE_FAILED
    )
    assert captured.value.failure_class is (
        engine.H2cPrepareFailureClass.PERSISTENCE
    )
    assert not (harness.case_root() / "prepared/prepared_case.json").exists()


def test_corrupted_archive_bytes_are_caught_before_the_manifest(
    harness: _Harness,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    real_write = os.write

    def corrupt(fd: int, data: bytes) -> int:
        if data.startswith(b"as_of"):
            return real_write(fd, b"\x00" * len(data))
        return real_write(fd, data)

    monkeypatch.setattr(engine.os, "write", corrupt)
    with pytest.raises(engine.H2cPrepareError) as captured:
        harness.prepare()
    assert captured.value.code is (
        engine.H2cPrepareErrorCode.H2C_PREPARE_PERSISTENCE_FAILED
    )
    root = harness.case_root()
    assert not (root / "prepared/prepared_case.json").exists()
    assert (root / "archive/strategy_settings.yaml").read_bytes() != (
        harness.settings_raw
    )


def test_a_corrupted_manifest_is_never_a_valid_completion_marker(
    harness: _Harness,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    real_write = os.write

    def corrupt(fd: int, data: bytes) -> int:
        if data.startswith(b'{"artifact_kind"'):
            return real_write(fd, b"{" + b" " * (len(data) - 1))
        return real_write(fd, data)

    monkeypatch.setattr(engine.os, "write", corrupt)
    with pytest.raises(engine.H2cPrepareError) as captured:
        harness.prepare()
    assert captured.value.code is (
        engine.H2cPrepareErrorCode.H2C_PREPARE_PERSISTENCE_FAILED
    )
    manifest_path = harness.case_root() / "prepared/prepared_case.json"
    with pytest.raises(json.JSONDecodeError):
        json.loads(manifest_path.read_bytes())


def test_a_present_declared_leaf_blocks_the_manifest(
    harness: _Harness,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    real_write_file = engine._write_exact_file
    calls: list[str] = []

    def intercept(*, name: str, exact_bytes: bytes, dir_fd: int):
        calls.append(name)
        written = real_write_file(
            name=name,
            exact_bytes=exact_bytes,
            dir_fd=dir_fd,
        )
        if name == "legacy_prompt.txt":
            root = harness.case_root()
            (root / "responses/h1_response.raw").write_bytes(b"early\n")
        return written

    monkeypatch.setattr(engine, "_write_exact_file", intercept)
    with pytest.raises(engine.H2cPrepareError) as captured:
        harness.prepare()
    assert captured.value.code is (
        engine.H2cPrepareErrorCode.H2C_PREPARE_PATH_CONTRACT_INVALID
    )
    assert "prepared_case.json" not in calls
    assert not (
        harness.case_root() / "prepared/prepared_case.json"
    ).exists()


@pytest.mark.parametrize("cut", (1, 2, 3, 4, 5, 6))
def test_every_persistence_cut_retains_partials_without_a_manifest(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    cut: int,
) -> None:
    harness = _harness(monkeypatch, tmp_path)
    real_write_file = engine._write_exact_file
    written: list[str] = []

    def failing(*, name: str, exact_bytes: bytes, dir_fd: int):
        if len(written) + 1 == cut:
            raise engine.H2cPrepareError(
                code=(
                    engine.H2cPrepareErrorCode.H2C_PREPARE_PERSISTENCE_FAILED
                ),
                failure_class=engine.H2cPrepareFailureClass.PERSISTENCE,
            )
        written.append(name)
        return real_write_file(
            name=name,
            exact_bytes=exact_bytes,
            dir_fd=dir_fd,
        )

    monkeypatch.setattr(engine, "_write_exact_file", failing)
    with pytest.raises(engine.H2cPrepareError) as captured:
        harness.prepare()
    assert captured.value.code is (
        engine.H2cPrepareErrorCode.H2C_PREPARE_PERSISTENCE_FAILED
    )
    root = harness.case_root()
    expected_names = [name for _, name in WRITTEN_LEAVES][: cut - 1]
    assert written == expected_names
    # Partial report-only evidence is retained, never rolled back.
    assert root.is_dir()
    assert sorted(item.name for item in root.iterdir()) == sorted(
        CASE_DIRECTORIES
    )
    assert not (root / "prepared/prepared_case.json").exists()
    assert list((root / "prepared").iterdir()) == []
    for directory, name in WRITTEN_LEAVES[: cut - 1]:
        assert (root / directory / name).is_file()
    for directory, name in WRITTEN_LEAVES[cut - 1 :]:
        assert not (root / directory / name).exists()


@pytest.mark.parametrize("complete", (True, False))
def test_rerun_is_rejected_without_reading_or_modifying_the_case(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    complete: bool,
) -> None:
    harness = _harness(monkeypatch, tmp_path)
    if complete:
        harness.prepare()
    else:
        real_write_file = engine._write_exact_file
        seen: list[str] = []

        def failing(*, name: str, exact_bytes: bytes, dir_fd: int):
            if len(seen) == 2:
                raise engine.H2cPrepareError(
                    code=(
                        engine.H2cPrepareErrorCode.
                        H2C_PREPARE_PERSISTENCE_FAILED
                    ),
                    failure_class=engine.H2cPrepareFailureClass.PERSISTENCE,
                )
            seen.append(name)
            return real_write_file(
                name=name,
                exact_bytes=exact_bytes,
                dir_fd=dir_fd,
            )

        with monkeypatch.context() as patcher:
            patcher.setattr(engine, "_write_exact_file", failing)
            with pytest.raises(engine.H2cPrepareError):
                harness.prepare()

    root = harness.case_root()
    before = sorted(
        (
            item.relative_to(root).as_posix(),
            item.read_bytes() if item.is_file() else None,
            item.stat().st_mtime_ns,
        )
        for item in root.rglob("*")
    )
    harness.tree_operations.clear()
    with pytest.raises(engine.H2cPrepareError) as captured:
        harness.prepare()
    assert captured.value.code is (
        engine.H2cPrepareErrorCode.H2C_PREPARE_PATH_CONTRACT_INVALID
    )
    assert captured.value.failure_class is (
        engine.H2cPrepareFailureClass.OPERATOR_INPUT
    )
    assert harness.tree_operations == []
    after = sorted(
        (
            item.relative_to(root).as_posix(),
            item.read_bytes() if item.is_file() else None,
            item.stat().st_mtime_ns,
        )
        for item in root.rglob("*")
    )
    assert after == before


def test_a_fresh_case_root_still_succeeds_after_a_rejected_rerun(
    harness: _Harness,
) -> None:
    first = harness.prepare(name="case-0001")
    with pytest.raises(engine.H2cPrepareError):
        harness.prepare(name="case-0001")
    second = harness.prepare(name="case-0002")
    assert first.workflow_status == second.workflow_status
    assert harness.case_root("case-0002").is_dir()
    validate_mmi_h2c_prepared_case_v1(
        prepared_case=json.loads(
            (
                harness.case_root("case-0002")
                / "prepared/prepared_case.json"
            ).read_bytes()
        )
    )


# --------------------------------------------------------------------------
# Dormancy, isolation and unchanged neighbours
# --------------------------------------------------------------------------
def test_owner_has_no_provider_network_or_concurrency_capability() -> None:
    source = Path(engine.__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)
    modules = {
        node.module
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module
    } | {
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    }
    assert not {
        "anthropic",
        "asyncio",
        "concurrent",
        "ctypes",
        "http",
        "httpx",
        "multiprocessing",
        "openai",
        "playwright",
        "requests",
        "sched",
        "selenium",
        "signal",
        "socket",
        "subprocess",
        "threading",
        "urllib",
    }.intersection(modules)
    assert "except Exception" not in source
    for fragment in (
        "git ",
        '"git"',
        "print(",
        "sys.",
        "input(",
        "time.sleep",
        "read_bytes",
        "write_bytes",
        "read_text",
        "write_text",
        "shutil",
        "unlink",
        "remove(",
        "rmdir",
        "rename",
        "replace(",
    ):
        assert fragment not in source, fragment
    # Every declared response and result leaf appears exactly once, as a
    # layout constant of a leaf that Phase A requires to stay absent.
    for leaf in (
        "h1_response.raw",
        "legacy_response.raw",
        "case_evidence_bundle.json",
        "comparison_report.json",
        "receipt.json",
    ):
        assert source.count(leaf) == 1, leaf
    tree_calls = {
        node.func.attr
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id == "os"
    }
    assert tree_calls <= {
        "close",
        "fchmod",
        "fspath",
        "fstat",
        "fsync",
        "mkdir",
        "open",
        "read",
        "stat",
        "write",
    }
    assert "listdir" not in source and "walk(" not in source


def test_engine_is_dormant_and_owns_the_prepared_case_consumer() -> None:
    production_root = repo_root() / "src/investment_orchestrator"
    engine_path = Path(engine.__file__).resolve()
    engine_relative = engine_path.relative_to(repo_root())
    engine_consumers: list[str] = []
    prepared_consumers: list[str] = []
    receipt_consumers: list[str] = []
    for path in sorted(production_root.rglob("*.py")):
        source = path.read_text(encoding="utf-8")
        relative = path.relative_to(repo_root()).as_posix()
        if (
            "mmi_h2c_prepare_persisted_case_v1" in source
            and path.resolve() != engine_path
        ):
            engine_consumers.append(relative)
        if (
            "mmi_h2c_prepared_case_v1" in source
            and path.name != "mmi_h2c_prepared_case_v1.py"
        ):
            prepared_consumers.append(relative)
        if (
            "mmi_h2c_dual_side_persisted_case_receipt_v2" in source
            and path.name != "mmi_h2c_dual_side_persisted_case_receipt_v2.py"
        ):
            receipt_consumers.append(relative)
    assert engine_consumers == []
    assert prepared_consumers == [engine_relative.as_posix()]
    assert receipt_consumers == []
    assert mmi.__all__ == ()
    assert not hasattr(package, "__all__")


def test_foreground_session_and_cli_remain_untouched_by_phase_a() -> None:
    from investment_orchestrator.cli import run_mmi_h2c_capture as cli
    from investment_orchestrator.offline import (
        mmi_h2c_manual_capture_session as session,
    )

    for module in (cli, session):
        source = Path(module.__file__).read_text(encoding="utf-8")
        assert "mmi_h2c_prepare_persisted_case_v1" not in source
        assert "prepare_h2c_persisted_case" not in source
    assert session.__all__ == (
        "H2cManualCaptureError",
        "H2cManualCaptureErrorCode",
        "H2cManualCaptureFailureClass",
        "H2cManualCaptureResult",
        "H2cOperatorHandoff",
        "run_h2c_manual_capture",
    )
    options = [
        action.option_strings[0]
        for action in cli._parser()._actions
        if action.option_strings and action.option_strings[0] != "-h"
    ]
    assert len(options) == 9
    assert tuple(
        inspect.signature(session.run_h2c_manual_capture).parameters
    ) == (
        "strategy_settings_expected_sha256",
        "portfolio_snapshot_expected_sha256",
        "h1_prompt_output_path",
        "legacy_prompt_output_path",
        "h1_response_path",
        "legacy_response_path",
        "case_evidence_bundle_output_path",
        "comparison_report_output_path",
        "receipt_output_path",
        "operator_handoff",
    )
