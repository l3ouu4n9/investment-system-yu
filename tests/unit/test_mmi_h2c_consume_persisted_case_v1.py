from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import stat
import errno
import sys
import copy

import pytest
from unittest.mock import Mock

from investment_orchestrator.offline.mmi_h2c_consume_persisted_case_v1 import (
    H2cConsumeError,
    H2cConsumeErrorCode,
    H2cConsumeFailureClass,
    H2cConsumeResult,
    consume_h2c_persisted_case,
    consume_h2c_persisted_case_from_archives,
    _MANIFEST_MAXIMUM_BYTES,
    _raise_for_archived_source_error,
)
from investment_orchestrator.offline.mmi_h2c_archived_source_v1 import (
    MmiH2cArchivedSourceV1Error,
)
from investment_orchestrator.offline.mmi_h2c_prepare_persisted_case_v1 import (
    prepare_h2c_persisted_case,
)
from investment_orchestrator.common.paths import repo_root
from investment_orchestrator.mmi.canonical import (
    MAXIMUM_MMI_RAW_RESPONSE_BYTES,
    MAX_MMI_H2C_CASE_EVIDENCE_BUNDLE_V1_CANONICAL_BYTES,
    MAX_MMI_LEGACY_STEP1_COMPARISON_REPORT_V1_CANONICAL_BYTES,
    canonical_json_bytes,
    record_identity_sha256,
)
from investment_orchestrator.mmi.contracts import MMI_SOURCE_CATALOG, MmiSourceRole
from investment_orchestrator.offline.mmi_legacy_step1_comparison_report_v1 import MAX_LEGACY_RESEARCH_RAW_BYTES
from investment_orchestrator.offline.mmi_h2c_case_bundle_v1 import validate_mmi_h2c_case_evidence_bundle_v1
from investment_orchestrator.offline.mmi_legacy_step1_comparison_report_v1 import validate_mmi_legacy_step1_comparison_report_v1
from investment_orchestrator.offline.mmi_h2c_dual_side_persisted_case_receipt_v2 import validate_mmi_h2c_dual_side_persisted_case_receipt_v2

from tests.unit.test_mmi_h2c_prepare_persisted_case_v1 import (
    _settings_bytes,
    _portfolio_bytes,
)

def _response_handoff(h1_prompt_path: Path) -> tuple[bytes, bytes]:
    prompt = h1_prompt_path.read_text(encoding="utf-8")
    context = prompt.split("PROMPT_CONTEXT_BINDING_SHA256=", 1)[1].splitlines()[0]
    framed = prompt.split("MMI_V2_EVIDENCE_FRAME_START\n", 1)[1]
    view = json.loads(framed.splitlines()[1])
    rows = [
        {
            "ticker": item["ticker"],
            "evidence_status": "EVIDENCE_SUPPORTED",
            "rationale_12m_plus": "R" * 40,
            "references": [f"POLICY.INSTRUMENT.{index:04d}"],
        }
        for index, item in enumerate(view["policy_view"]["analysis_instruments"], start=1)
    ]
    payload = {
        "response_schema_version": "mmi_grounded_analysis_response_v2",
        "prompt_context_binding_sha256": context,
        "analysis_status": "QUALITATIVE_ANALYSIS_PROVIDED",
        "instrument_views": rows,
        "anchor_associations_status": "UNAVAILABLE",
        "scheduled_events_status": "UNAVAILABLE",
        "regime_observation_status": "UNAVAILABLE",
        "evidence_observations": [],
        "risks": [],
        "uncertainties": [],
        "contradictions": [],
        "research_questions": [],
        "summary": {
            "text": "Qualitative evidence remains report-only.",
            "references": ["VIEW.EVALUATION_TIMESTAMP"],
            "hypothesis": False,
        },
    }
    h1_bytes = json.dumps(payload, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode("utf-8")
    legacy_bytes = b"\n"
    return h1_bytes, legacy_bytes

def _capture_at(source_root: Path):
    from investment_orchestrator.mmi.source_capture import _capture_mmi_source_at_root
    def capture(role, *, expected_source_sha256):
        return _capture_mmi_source_at_root(source_root, role=role, expected_source_sha256=expected_source_sha256)
    return capture

@pytest.fixture
def run_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.chdir(tmp_path)
    import investment_orchestrator.offline.mmi_h2c_prepare_persisted_case_v1 as engine_prepare
    import investment_orchestrator.offline.mmi_h2c_consume_persisted_case_v1 as engine_consume
    monkeypatch.setattr(engine_prepare, "capture_current_mmi_source", _capture_at(tmp_path))
    monkeypatch.setattr(engine_consume, "capture_current_mmi_source", _capture_at(tmp_path))
    return tmp_path

def _create_prepared_case(
    tmp_path: Path, settings_suffix=b"", portfolio_suffix=b""
) -> tuple[Path, str, str, str]:
    case_root = tmp_path / "case_root"
    settings = _settings_bytes() + settings_suffix
    portfolio = _portfolio_bytes() + portfolio_suffix

    current = tmp_path / "inputs/current"
    current.mkdir(parents=True, exist_ok=True)
    (current / "strategy_settings.yaml").write_bytes(settings)
    (current / "portfolio_snapshot.txt").write_bytes(portfolio)

    settings_sha256 = hashlib.sha256(settings).hexdigest()
    portfolio_sha256 = hashlib.sha256(portfolio).hexdigest()
    result = prepare_h2c_persisted_case(
        strategy_settings_expected_sha256=settings_sha256,
        portfolio_snapshot_expected_sha256=portfolio_sha256,
        case_root=case_root,
    )
    return case_root, result.prepared_case_identity_sha256, settings_sha256, portfolio_sha256

def _write_responses(case_root: Path, h1_bytes: bytes | None = None, legacy_bytes: bytes | None = None) -> None:
    responses = case_root / "responses"
    responses.mkdir(exist_ok=True)
    h1_gen, leg_gen = _response_handoff(case_root / "prompts/h1_prompt.txt")
    if h1_bytes is None:
        h1_bytes = h1_gen
    if legacy_bytes is None:
        legacy_bytes = leg_gen
    (responses / "h1_response.raw").write_bytes(h1_bytes)
    (responses / "legacy_response.raw").write_bytes(legacy_bytes)

def test_h2c_consume_failure_classes() -> None:
    assert len(H2cConsumeFailureClass) == 8

def test_h2c_consume_error_codes() -> None:
    assert len(H2cConsumeErrorCode) == 15

# --- 10. Persisted validator, bytes and identity oracle ---
def test_h2c_consume_positive_path_and_validators(run_env: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    case_root, case_sha, set_sha, port_sha = _create_prepared_case(run_env)
    _write_responses(case_root)

    # 6. Exactly-one-open response oracles
    orig_open = os.open
    open_counts = {"h1": 0, "legacy": 0}
    def mocked_open(path, flags, mode=0o777, *, dir_fd=None):
        str_path = str(path)
        if "responses/h1_response.raw" in str_path:
            open_counts["h1"] += 1
            if open_counts["h1"] > 1:
                raise RuntimeError("h1 opened more than once")
        if "responses/legacy_response.raw" in str_path:
            open_counts["legacy"] += 1
            if open_counts["legacy"] > 1:
                raise RuntimeError("legacy opened more than once")
        return orig_open(path, flags, mode, dir_fd=dir_fd)
    monkeypatch.setattr(os, "open", mocked_open)
    monkeypatch.setattr(os, "supports_dir_fd", frozenset(os.supports_dir_fd) | {mocked_open})

    orig_build = consume_h2c_persisted_case.__globals__["build_mmi_legacy_step1_comparison_report_v1"]
    report_kwargs = {}
    def mocked_build(**kwargs):
        report_kwargs.update(kwargs)
        return orig_build(**kwargs)
    monkeypatch.setitem(consume_h2c_persisted_case.__globals__, "build_mmi_legacy_step1_comparison_report_v1", mocked_build)

    result = consume_h2c_persisted_case(
        case_root=case_root,
        expected_prepared_case_identity_sha256=case_sha,
        strategy_settings_expected_sha256=set_sha,
        portfolio_snapshot_expected_sha256=port_sha,
    )
    assert result.workflow_status == "COMPLETED"

    assert open_counts["h1"] == 1
    assert open_counts["legacy"] == 1

    bundle_path = case_root / "artifacts/case_evidence_bundle.json"
    report_path = case_root / "artifacts/comparison_report.json"
    receipt_path = case_root / "artifacts/receipt.json"

    bundle_bytes = bundle_path.read_bytes()
    report_bytes = report_path.read_bytes()
    receipt_bytes = receipt_path.read_bytes()

    bundle_json = json.loads(bundle_bytes.decode("utf-8"))
    report_json = json.loads(report_bytes.decode("utf-8"))
    receipt_json = json.loads(receipt_bytes.decode("utf-8"))

    validate_mmi_h2c_case_evidence_bundle_v1(bundle=bundle_json)
    validate_mmi_legacy_step1_comparison_report_v1(value=report_json, **report_kwargs)
    validate_mmi_h2c_dual_side_persisted_case_receipt_v2(receipt=receipt_json)

    from investment_orchestrator.mmi.canonical import MAXIMUM_CANONICAL_JSON_BYTES

    assert bundle_bytes == canonical_json_bytes(bundle_json, maximum_bytes=MAX_MMI_H2C_CASE_EVIDENCE_BUNDLE_V1_CANONICAL_BYTES)
    assert report_bytes == canonical_json_bytes(report_json, maximum_bytes=MAX_MMI_LEGACY_STEP1_COMPARISON_REPORT_V1_CANONICAL_BYTES)
    assert receipt_bytes == canonical_json_bytes(receipt_json, maximum_bytes=MAXIMUM_CANONICAL_JSON_BYTES)

    assert bundle_json["case_evidence_bundle_identity_sha256"] == result.case_evidence_bundle_identity_sha256
    assert report_json["comparison_report_identity_sha256"] == result.comparison_report_identity_sha256
    assert receipt_json["receipt_identity_sha256"] == result.receipt_identity_sha256

# --- 3. Replace the existing collision test ---
@pytest.mark.parametrize("leaf", [
    "case_evidence_bundle.json",
    "comparison_report.json",
    "receipt.json",
])
def test_h2c_consume_artifact_collision_preflight(run_env: Path, monkeypatch: pytest.MonkeyPatch, leaf: str) -> None:
    case_root, case_sha, set_sha, port_sha = _create_prepared_case(run_env)
    _write_responses(case_root)

    arts = case_root / "artifacts"
    arts.mkdir(exist_ok=True)
    (arts / leaf).write_bytes(b"{}")

    orig_open = os.open
    def mocked_open(path, flags, mode=0o777, *, dir_fd=None):
        str_path = str(path)
        if "prepared/prepared_case.json" in str_path or "responses/" in str_path:
            raise RuntimeError(f"Read object {str_path} after collision preflight")
        return orig_open(path, flags, mode, dir_fd=dir_fd)
    monkeypatch.setattr(os, "open", mocked_open)
    monkeypatch.setattr(os, "supports_dir_fd", frozenset(os.supports_dir_fd) | {mocked_open})

    with pytest.raises(Exception) as exc:
        consume_h2c_persisted_case(
            case_root=case_root,
            expected_prepared_case_identity_sha256=case_sha,
            strategy_settings_expected_sha256=set_sha,
            portfolio_snapshot_expected_sha256=port_sha,
        )
    assert exc.value.code == H2cConsumeErrorCode.H2C_CONSUME_COLLISION

# --- 4. Prompt/source-before-response runtime oracles ---
def test_h2c_consume_g2_or_prompt_mismatch_opens_no_response(run_env: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    case_root, case_sha, set_sha, port_sha = _create_prepared_case(run_env)
    _write_responses(case_root)

    h1_path = case_root / "prompts/h1_prompt.txt"
    h1_path.write_text("TAMPERED")

    orig_open = os.open
    def mocked_open(path, flags, mode=0o777, *, dir_fd=None):
        if "responses/" in str(path):
            raise RuntimeError("Response opened despite prompt mismatch")
        return orig_open(path, flags, mode, dir_fd=dir_fd)
    monkeypatch.setattr(os, "open", mocked_open)
    monkeypatch.setattr(os, "supports_dir_fd", frozenset(os.supports_dir_fd) | {mocked_open})

    with pytest.raises(Exception) as exc:
        consume_h2c_persisted_case(
            case_root=case_root,
            expected_prepared_case_identity_sha256=case_sha,
            strategy_settings_expected_sha256=set_sha,
            portfolio_snapshot_expected_sha256=port_sha,
        )
    assert exc.value.code == H2cConsumeErrorCode.H2C_CONSUME_PROMPT_CONTRACT_INVALID


def test_h2c_consume_source_mismatch_opens_no_response(run_env: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    case_root, case_sha, set_sha, port_sha = _create_prepared_case(run_env)
    _write_responses(case_root)

    orig_open = os.open
    def mocked_open(path, flags, mode=0o777, *, dir_fd=None):
        if "responses/" in str(path):
            raise RuntimeError("Response opened despite source mismatch")
        return orig_open(path, flags, mode, dir_fd=dir_fd)
    monkeypatch.setattr(os, "open", mocked_open)
    monkeypatch.setattr(os, "supports_dir_fd", frozenset(os.supports_dir_fd) | {mocked_open})

    with pytest.raises(Exception) as exc:
        consume_h2c_persisted_case(
            case_root=case_root,
            expected_prepared_case_identity_sha256=case_sha,
            strategy_settings_expected_sha256="3" * 64,
            portfolio_snapshot_expected_sha256=port_sha,
        )
    assert exc.value.code == H2cConsumeErrorCode.H2C_CONSUME_LIVE_CHAIN_INVALID or exc.value.code == H2cConsumeErrorCode.H2C_CONSUME_SOURCE_CAPTURE_INVALID

# --- 5. Complete the cross-case mismatch test ---
def test_h2c_consume_case_a_prepared_case_with_case_b_source(run_env: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # Create Case A
    run_env_a = run_env / "a"
    run_env_a.mkdir()
    monkeypatch.chdir(run_env_a)
    import investment_orchestrator.offline.mmi_h2c_prepare_persisted_case_v1 as engine_prepare
    import investment_orchestrator.offline.mmi_h2c_consume_persisted_case_v1 as engine_consume
    monkeypatch.setattr(engine_prepare, "capture_current_mmi_source", _capture_at(run_env_a))
    monkeypatch.setattr(engine_consume, "capture_current_mmi_source", _capture_at(run_env_a))
    case_root_a, case_sha_a, set_sha_a, port_sha_a = _create_prepared_case(run_env_a, settings_suffix=b"\n# Case A\n")
    _write_responses(case_root_a)

    # Create Case B
    run_env_b = run_env / "b"
    run_env_b.mkdir()
    monkeypatch.chdir(run_env_b)
    monkeypatch.setattr(engine_prepare, "capture_current_mmi_source", _capture_at(run_env_b))
    monkeypatch.setattr(engine_consume, "capture_current_mmi_source", _capture_at(run_env_b))
    case_root_b, case_sha_b, set_sha_b, port_sha_b = _create_prepared_case(run_env_b, settings_suffix=b"\n# Case B\n")
    _write_responses(case_root_b)

    orig_open = os.open
    def mocked_open(path, flags, mode=0o777, *, dir_fd=None):
        if "responses/" in str(path):
            raise RuntimeError("Response opened during cross-case mismatch")
        return orig_open(path, flags, mode, dir_fd=dir_fd)
    monkeypatch.setattr(os, "open", mocked_open)
    monkeypatch.setattr(os, "supports_dir_fd", frozenset(os.supports_dir_fd) | {mocked_open})

    # Attempt to consume Case A using Case B's source inputs and Case B's live environment
    with pytest.raises(Exception) as exc:
        consume_h2c_persisted_case(
            case_root=case_root_a,
            expected_prepared_case_identity_sha256=case_sha_a,
            strategy_settings_expected_sha256=set_sha_b,
            portfolio_snapshot_expected_sha256=port_sha_b,
        )
    assert exc.value.code == H2cConsumeErrorCode.H2C_CONSUME_LIVE_CHAIN_INVALID
    assert not (case_root_a / "artifacts/receipt.json").exists()

# --- 7. Mid-read mutation oracle ---
def test_h2c_consume_mid_read_mutation_response(run_env: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    case_root, case_sha, set_sha, port_sha = _create_prepared_case(run_env)
    _write_responses(case_root)

    orig_fstat = os.fstat
    simulate = False

    def mocked_fstat(fd):
        st = orig_fstat(fd)
        if simulate and st.st_size == len((case_root / "responses/h1_response.raw").read_bytes()):
            class FakeStat:
                def __init__(self, st):
                    self.st_mode = st.st_mode
                    self.st_ino = st.st_ino
                    self.st_dev = st.st_dev
                    self.st_size = st.st_size
                    self.st_mtime_ns = st.st_mtime_ns + 1
                    self.st_ctime_ns = st.st_ctime_ns
            return FakeStat(st)
        return st

    orig_read = os.read
    def mocked_read(fd, n):
        nonlocal simulate
        st = orig_fstat(fd)
        if st.st_size == len((case_root / "responses/h1_response.raw").read_bytes()):
            simulate = True
        return orig_read(fd, n)

    monkeypatch.setattr(os, "fstat", mocked_fstat)
    monkeypatch.setattr(os, "read", mocked_read)

    with pytest.raises(Exception) as exc:
        consume_h2c_persisted_case(
            case_root=case_root,
            expected_prepared_case_identity_sha256=case_sha,
            strategy_settings_expected_sha256=set_sha,
            portfolio_snapshot_expected_sha256=port_sha,
        )
    assert exc.value.code == H2cConsumeErrorCode.H2C_CONSUME_RESPONSE_INPUT_INVALID
    assert not (case_root / "artifacts/receipt.json").exists()

# --- 8. Exact limit-plus-one tests ---
def test_h2c_consume_h1_limit_plus_one(run_env: Path) -> None:
    case_root, case_sha, set_sha, port_sha = _create_prepared_case(run_env)
    _write_responses(case_root)
    with open(case_root / "responses/h1_response.raw", "wb") as f:
        f.write(b"0" * (MAXIMUM_MMI_RAW_RESPONSE_BYTES + 1))
    with pytest.raises(Exception) as exc:
        consume_h2c_persisted_case(
            case_root=case_root,
            expected_prepared_case_identity_sha256=case_sha,
            strategy_settings_expected_sha256=set_sha,
            portfolio_snapshot_expected_sha256=port_sha,
        )
    assert exc.value.code == H2cConsumeErrorCode.H2C_CONSUME_RESPONSE_INPUT_INVALID

def test_h2c_consume_legacy_limit_plus_one(run_env: Path) -> None:
    case_root, case_sha, set_sha, port_sha = _create_prepared_case(run_env)
    _write_responses(case_root)
    with open(case_root / "responses/legacy_response.raw", "wb") as f:
        f.write(b"0" * (MAX_LEGACY_RESEARCH_RAW_BYTES + 1))
    with pytest.raises(Exception) as exc:
        consume_h2c_persisted_case(
            case_root=case_root,
            expected_prepared_case_identity_sha256=case_sha,
            strategy_settings_expected_sha256=set_sha,
            portfolio_snapshot_expected_sha256=port_sha,
        )
    assert exc.value.code == H2cConsumeErrorCode.H2C_CONSUME_RESPONSE_INPUT_INVALID

# --- 8a. Archive raw-source read bound: catalog ownership, not the record bound ---
def _padding_suffix(base_length: int, *, target_total: int) -> bytes:
    """A suffix that pads a legitimate source to an exact total size.

    Mirrors the existing ``settings_suffix=b"\\n# Case A\\n"`` precedent
    elsewhere in this file: the padding is an inert trailing comment, not a
    structural change, so the prepare owner still accepts and authenticates
    it exactly like any other real source.
    """
    marker, tail = b"\n# ", b"\n"
    pad_length = target_total - base_length - len(marker) - len(tail)
    assert pad_length >= 0
    return marker + (b"x" * pad_length) + tail


_STRATEGY_CATALOG_MAX = MMI_SOURCE_CATALOG[MmiSourceRole.STRATEGY_SETTINGS].maximum_bytes
_PORTFOLIO_CATALOG_MAX = MMI_SOURCE_CATALOG[MmiSourceRole.PORTFOLIO_SNAPSHOT].maximum_bytes


@pytest.mark.parametrize(
    "kwargs",
    [
        pytest.param(
            {"settings_suffix": _padding_suffix(len(_settings_bytes()), target_total=20_000)},
            id="strategy",
        ),
        pytest.param(
            {"portfolio_suffix": _padding_suffix(len(_portfolio_bytes()), target_total=20_000)},
            id="portfolio",
        ),
    ],
)
def test_h2c_consume_source_divergence_window_succeeds(run_env: Path, kwargs) -> None:
    """A genuine source strictly above the old 8,192 record-canonical bound
    and strictly below the catalog raw-source maximum must still consume
    successfully -- this is the domain the old bound wrongly rejected."""
    case_root, case_sha, set_sha, port_sha = _create_prepared_case(run_env, **kwargs)
    _write_responses(case_root)
    result = consume_h2c_persisted_case(
        case_root=case_root,
        expected_prepared_case_identity_sha256=case_sha,
        strategy_settings_expected_sha256=set_sha,
        portfolio_snapshot_expected_sha256=port_sha,
    )
    assert result.workflow_status == "COMPLETED"
    for leaf in ("case_evidence_bundle.json", "comparison_report.json", "receipt.json"):
        assert (case_root / "artifacts" / leaf).exists()


@pytest.mark.parametrize(
    "kwargs",
    [
        pytest.param(
            {
                "settings_suffix": _padding_suffix(
                    len(_settings_bytes()), target_total=_STRATEGY_CATALOG_MAX
                )
            },
            id="strategy",
        ),
        pytest.param(
            {
                "portfolio_suffix": _padding_suffix(
                    len(_portfolio_bytes()), target_total=_PORTFOLIO_CATALOG_MAX
                )
            },
            id="portfolio",
        ),
    ],
)
def test_h2c_consume_source_at_exact_catalog_maximum_succeeds(run_env: Path, kwargs) -> None:
    """Proves the corrected ceiling is the role-specific catalog maximum
    itself, not merely "larger than 8,192": a source at exactly that size
    must still consume successfully end-to-end."""
    case_root, case_sha, set_sha, port_sha = _create_prepared_case(run_env, **kwargs)
    _write_responses(case_root)
    result = consume_h2c_persisted_case(
        case_root=case_root,
        expected_prepared_case_identity_sha256=case_sha,
        strategy_settings_expected_sha256=set_sha,
        portfolio_snapshot_expected_sha256=port_sha,
    )
    assert result.workflow_status == "COMPLETED"


@pytest.mark.parametrize(
    "leaf,catalog_max",
    [
        pytest.param("archive/strategy_settings.yaml", _STRATEGY_CATALOG_MAX, id="strategy"),
        pytest.param("archive/portfolio_snapshot.txt", _PORTFOLIO_CATALOG_MAX, id="portfolio"),
    ],
)
def test_h2c_consume_corrupted_archive_leaf_above_catalog_maximum_opens_no_response(
    run_env: Path, monkeypatch: pytest.MonkeyPatch, leaf: str, catalog_max: int
) -> None:
    """A prepared case cannot legitimately carry a source above the catalog
    maximum (prepare itself rejects it), so the only way to exercise the
    ceiling is a corrupted persisted-case input: the archived raw leaf is
    replaced with catalog_max + 1 bytes while the authenticated manifest and
    source records are left exactly as prepared. The raw read must reject
    this before the archive/live byte-equality check, before any response
    leaf is opened, and before any artifact is persisted."""
    case_root, case_sha, set_sha, port_sha = _create_prepared_case(run_env)
    _write_responses(case_root)

    (case_root / leaf).write_bytes(b"x" * (catalog_max + 1))

    orig_open = os.open
    def mocked_open(path, flags, mode=0o777, *, dir_fd=None):
        if "responses/" in str(path):
            raise RuntimeError("Response opened despite oversize archive leaf")
        return orig_open(path, flags, mode, dir_fd=dir_fd)
    monkeypatch.setattr(os, "open", mocked_open)
    monkeypatch.setattr(os, "supports_dir_fd", frozenset(os.supports_dir_fd) | {mocked_open})

    with pytest.raises(Exception) as exc:
        consume_h2c_persisted_case(
            case_root=case_root,
            expected_prepared_case_identity_sha256=case_sha,
            strategy_settings_expected_sha256=set_sha,
            portfolio_snapshot_expected_sha256=port_sha,
        )
    assert exc.value.code == H2cConsumeErrorCode.H2C_CONSUME_PATH_CONTRACT_INVALID
    assert not (case_root / "artifacts/receipt.json").exists()
    assert not (case_root / "artifacts/case_evidence_bundle.json").exists()
    assert not (case_root / "artifacts/comparison_report.json").exists()

# --- 9. Legacy parse/normalization rejection ---
def test_h2c_consume_legacy_parse_rejection(run_env: Path) -> None:
    case_root, case_sha, set_sha, port_sha = _create_prepared_case(run_env)
    _write_responses(case_root, legacy_bytes=b"\x80")
    with pytest.raises(Exception) as exc:
        consume_h2c_persisted_case(
            case_root=case_root,
            expected_prepared_case_identity_sha256=case_sha,
            strategy_settings_expected_sha256=set_sha,
            portfolio_snapshot_expected_sha256=port_sha,
        )
    assert exc.value.code == H2cConsumeErrorCode.H2C_CONSUME_VALIDATION_INVALID or exc.value.failure_class == H2cConsumeFailureClass.COMPILER_NORMALIZER
    assert not (case_root / "artifacts/receipt.json").exists()

# --- 11. Receipt-last and partial-state non-reuse oracle ---
def test_h2c_consume_persistence_failure_before_receipt_blocks_reuse(run_env: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    case_root, case_sha, set_sha, port_sha = _create_prepared_case(run_env)
    _write_responses(case_root)

    orig_open = os.open
    def mocked_open(path, flags, mode=0o777, *, dir_fd=None):
        if str(path).endswith("receipt.json") and (flags & os.O_CREAT):
            raise OSError(errno.ENOSPC, "No space")
        return orig_open(path, flags, mode, dir_fd=dir_fd)
    monkeypatch.setattr(os, "open", mocked_open)
    monkeypatch.setattr(os, "supports_dir_fd", frozenset(os.supports_dir_fd) | {mocked_open})

    with pytest.raises(Exception) as exc:
        consume_h2c_persisted_case(
            case_root=case_root,
            expected_prepared_case_identity_sha256=case_sha,
            strategy_settings_expected_sha256=set_sha,
            portfolio_snapshot_expected_sha256=port_sha,
        )
    assert exc.value.code == H2cConsumeErrorCode.H2C_CONSUME_PERSISTENCE_FAILED
    assert (case_root / "artifacts/case_evidence_bundle.json").exists()
    assert (case_root / "artifacts/comparison_report.json").exists()
    assert not (case_root / "artifacts/receipt.json").exists()

    # Undo monkeypatch
    monkeypatch.undo()

    def mocked_open2(path, flags, mode=0o777, *, dir_fd=None):
        if "prepared/prepared_case.json" in str(path) or "responses/" in str(path):
            raise RuntimeError("Read object after collision preflight")
        return orig_open(path, flags, mode, dir_fd=dir_fd)
    monkeypatch.setattr(os, "open", mocked_open2)
    monkeypatch.setattr(os, "supports_dir_fd", frozenset(os.supports_dir_fd) | {mocked_open2})

    with pytest.raises(Exception) as exc:
        consume_h2c_persisted_case(
            case_root=case_root,
            expected_prepared_case_identity_sha256=case_sha,
            strategy_settings_expected_sha256=set_sha,
            portfolio_snapshot_expected_sha256=port_sha,
        )
    assert exc.value.code == H2cConsumeErrorCode.H2C_CONSUME_COLLISION

def test_h2c_consume_manifest_content_invalid(run_env: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    case_root, case_sha, set_sha, port_sha = _create_prepared_case(run_env)
    manifest_path = case_root / "prepared/prepared_case.json"
    manifest_path.write_bytes(b"not json {")
    from investment_orchestrator.offline.mmi_h2c_consume_persisted_case_v1 import H2cConsumeError
    with pytest.raises(H2cConsumeError) as exc:
        consume_h2c_persisted_case(
            case_root=case_root,
            expected_prepared_case_identity_sha256=case_sha,
            strategy_settings_expected_sha256=set_sha,
            portfolio_snapshot_expected_sha256=port_sha,
        )
    assert exc.value.code == H2cConsumeErrorCode.H2C_CONSUME_ARTIFACT_CONTENT_INVALID
    assert exc.value.failure_class == H2cConsumeFailureClass.ARTIFACT_CONTENT

def test_h2c_consume_manifest_schema_invalid(run_env: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    case_root, case_sha, set_sha, port_sha = _create_prepared_case(run_env)
    manifest_path = case_root / "prepared/prepared_case.json"
    manifest_path.write_bytes(b'{"bad": "schema"}')
    from investment_orchestrator.offline.mmi_h2c_consume_persisted_case_v1 import H2cConsumeError
    with pytest.raises(H2cConsumeError) as exc:
        consume_h2c_persisted_case(
            case_root=case_root,
            expected_prepared_case_identity_sha256=case_sha,
            strategy_settings_expected_sha256=set_sha,
            portfolio_snapshot_expected_sha256=port_sha,
        )
    assert exc.value.code == H2cConsumeErrorCode.H2C_CONSUME_MANIFEST_INVALID
    assert exc.value.failure_class == H2cConsumeFailureClass.VALIDATOR_SCHEMA

# --- 12. Archived persisted-case consume (D4e E2) ---

_PREPARED_CASE_IDENTITY_FIELD = "prepared_case_identity_sha256"
_PREPARED_CASE_IDENTITY_DOMAIN = b"mmi_h2c_prepared_case_v1\0"
_PREPARED_CASE_MAXIMUM_CANONICAL_BYTES = 411_753


def _resign_manifest(case_root: Path, mutate) -> dict[str, object]:
    """Apply `mutate` to the parsed manifest, then recompute its own
    self-identity so E1's schema/identity gate still passes and only a
    downstream deterministic-rebuild gate can catch the tampering.
    """
    manifest_path = case_root / "prepared/prepared_case.json"
    manifest = json.loads(manifest_path.read_bytes())
    mutate(manifest)
    manifest[_PREPARED_CASE_IDENTITY_FIELD] = record_identity_sha256(
        manifest,
        identity_field=_PREPARED_CASE_IDENTITY_FIELD,
        domain=_PREPARED_CASE_IDENTITY_DOMAIN,
        maximum_bytes=_PREPARED_CASE_MAXIMUM_CANONICAL_BYTES,
    )
    manifest_path.write_bytes(json.dumps(manifest).encode("utf-8"))
    return manifest


def _guard_no_response_open(monkeypatch: pytest.MonkeyPatch, *, message: str):
    orig_open = os.open
    def mocked_open(path, flags, mode=0o777, *, dir_fd=None):
        if "responses/" in str(path):
            raise RuntimeError(message)
        return orig_open(path, flags, mode, dir_fd=dir_fd)
    monkeypatch.setattr(os, "open", mocked_open)
    monkeypatch.setattr(os, "supports_dir_fd", frozenset(os.supports_dir_fd) | {mocked_open})


def _record_response_opens(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    response_opens: list[str] = []
    orig_open = os.open
    def mocked_open(path, flags, mode=0o777, *, dir_fd=None):
        if "responses/" in str(path):
            response_opens.append(str(path))
        return orig_open(path, flags, mode, dir_fd=dir_fd)
    monkeypatch.setattr(os, "open", mocked_open)
    monkeypatch.setattr(os, "supports_dir_fd", frozenset(os.supports_dir_fd) | {mocked_open})
    return response_opens


def _assert_no_success_artifacts(case_root: Path) -> None:
    assert not (case_root / "artifacts/case_evidence_bundle.json").exists()
    assert not (case_root / "artifacts/comparison_report.json").exists()
    assert not (case_root / "artifacts/receipt.json").exists()


# A. Collision preflight happens before ANY case input read, for each leaf.
@pytest.mark.parametrize("leaf", [
    "case_evidence_bundle.json",
    "comparison_report.json",
    "receipt.json",
])
def test_h2c_consume_archived_collision_preflight(run_env: Path, monkeypatch: pytest.MonkeyPatch, leaf: str) -> None:
    case_root, case_sha, _set_sha, _port_sha = _create_prepared_case(run_env)
    _write_responses(case_root)

    arts = case_root / "artifacts"
    arts.mkdir(exist_ok=True)
    (arts / leaf).write_bytes(b"{}")

    orig_open = os.open
    def mocked_open(path, flags, mode=0o777, *, dir_fd=None):
        str_path = str(path)
        if (
            "prepared/prepared_case.json" in str_path
            or "responses/" in str_path
            or "archive/" in str_path
        ):
            raise RuntimeError(f"Read {str_path} after collision preflight")
        return orig_open(path, flags, mode, dir_fd=dir_fd)
    monkeypatch.setattr(os, "open", mocked_open)
    monkeypatch.setattr(os, "supports_dir_fd", frozenset(os.supports_dir_fd) | {mocked_open})

    with pytest.raises(H2cConsumeError) as exc:
        consume_h2c_persisted_case_from_archives(
            case_root=case_root,
            expected_prepared_case_identity_sha256=case_sha,
        )
    assert exc.value.code == H2cConsumeErrorCode.H2C_CONSUME_COLLISION
    assert exc.value.failure_class == H2cConsumeFailureClass.PERSISTENCE


# B. All six E1 error codes translate to the frozen public code/class pairs.
@pytest.mark.parametrize(
    "e1_code,expected_public_code,expected_failure_class",
    [
        (
            "ARCHIVED_ARGUMENT_INVALID",
            H2cConsumeErrorCode.H2C_CONSUME_ARGUMENT_INVALID,
            H2cConsumeFailureClass.OPERATOR_INPUT,
        ),
        (
            "PREPARED_CASE_INPUT_INVALID",
            H2cConsumeErrorCode.H2C_CONSUME_ARTIFACT_CONTENT_INVALID,
            H2cConsumeFailureClass.ARTIFACT_CONTENT,
        ),
        (
            "PREPARED_CASE_SCHEMA_INVALID",
            H2cConsumeErrorCode.H2C_CONSUME_MANIFEST_INVALID,
            H2cConsumeFailureClass.VALIDATOR_SCHEMA,
        ),
        (
            "ARCHIVE_SOURCE_INPUT_INVALID",
            H2cConsumeErrorCode.H2C_CONSUME_ARTIFACT_CONTENT_INVALID,
            H2cConsumeFailureClass.ARTIFACT_CONTENT,
        ),
        (
            "ARCHIVE_SOURCE_SCHEMA_INVALID",
            H2cConsumeErrorCode.H2C_CONSUME_LIVE_CHAIN_INVALID,
            H2cConsumeFailureClass.VALIDATOR_SCHEMA,
        ),
        (
            "CAPABILITY_UNAVAILABLE",
            H2cConsumeErrorCode.H2C_CONSUME_CAPABILITY_UNAVAILABLE,
            H2cConsumeFailureClass.AVAILABILITY_PERMISSION,
        ),
    ],
)
def test_h2c_consume_archived_e1_error_translation(
    e1_code: str,
    expected_public_code: H2cConsumeErrorCode,
    expected_failure_class: H2cConsumeFailureClass,
) -> None:
    with pytest.raises(H2cConsumeError) as exc:
        _raise_for_archived_source_error(MmiH2cArchivedSourceV1Error(e1_code))
    assert exc.value.code == expected_public_code
    assert exc.value.failure_class == expected_failure_class
    assert exc.value.owner_reason_codes == (e1_code,)


def test_h2c_consume_archived_unknown_e1_error_propagates_same_instance(
    run_env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    case_root, case_sha, _set_sha, _port_sha = _create_prepared_case(run_env)
    _write_responses(case_root)

    import investment_orchestrator.offline.mmi_h2c_consume_persisted_case_v1 as ec_mod

    unknown_code = "FUTURE_ARCHIVED_SOURCE_ERROR"
    unknown_error = MmiH2cArchivedSourceV1Error(unknown_code)
    def fail_snapshot(*, case_fd, expected_prepared_case_identity_sha256):
        raise unknown_error
    monkeypatch.setattr(
        ec_mod,
        "_build_mmi_h2c_archived_prepared_case_snapshot",
        fail_snapshot,
    )
    response_opens = _record_response_opens(monkeypatch)

    with pytest.raises(MmiH2cArchivedSourceV1Error) as exc:
        consume_h2c_persisted_case_from_archives(
            case_root=case_root,
            expected_prepared_case_identity_sha256=case_sha,
        )

    assert exc.value is unknown_error
    assert type(exc.value) is MmiH2cArchivedSourceV1Error
    assert exc.value.code == unknown_code
    assert not isinstance(exc.value, H2cConsumeError)
    assert not isinstance(exc.value, RuntimeError)
    assert response_opens.count("responses/h1_response.raw") == 0
    assert response_opens.count("responses/legacy_response.raw") == 0
    _assert_no_success_artifacts(case_root)


def test_h2c_consume_archived_identity_mismatch_is_argument_invalid(
    run_env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """End-to-end wiring proof for one representative real E1 failure path."""
    case_root, _case_sha, _set_sha, _port_sha = _create_prepared_case(run_env)
    _write_responses(case_root)
    _guard_no_response_open(monkeypatch, message="Response opened despite identity mismatch")
    with pytest.raises(H2cConsumeError) as exc:
        consume_h2c_persisted_case_from_archives(
            case_root=case_root,
            expected_prepared_case_identity_sha256="1" * 64,
        )
    assert exc.value.code == H2cConsumeErrorCode.H2C_CONSUME_ARGUMENT_INVALID


# C/D. E1 invoked exactly once; prepared case/strategy/portfolio archive are
# never reread by the E2 orchestration.
def test_h2c_consume_archived_e1_exactly_once_no_reread(
    run_env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    case_root, case_sha, _set_sha, _port_sha = _create_prepared_case(run_env)
    _write_responses(case_root)

    import investment_orchestrator.offline.mmi_h2c_archived_source_v1 as e1_mod
    import investment_orchestrator.offline.mmi_h2c_consume_persisted_case_v1 as ec_mod

    e1_calls: list[str] = []
    e1_original = e1_mod._stable_read_exact_bytes
    def e1_spy(case_fd, relative_path, *, maximum_bytes):
        e1_calls.append(relative_path)
        return e1_original(case_fd, relative_path, maximum_bytes=maximum_bytes)
    monkeypatch.setattr(e1_mod, "_stable_read_exact_bytes", e1_spy)

    e2_calls: list[str] = []
    e2_original = ec_mod._neutral_stable_read_exact_bytes
    def e2_spy(case_fd, relative_path, *, maximum_bytes):
        e2_calls.append(relative_path)
        return e2_original(case_fd, relative_path, maximum_bytes=maximum_bytes)
    monkeypatch.setattr(ec_mod, "_neutral_stable_read_exact_bytes", e2_spy)

    result = consume_h2c_persisted_case_from_archives(
        case_root=case_root,
        expected_prepared_case_identity_sha256=case_sha,
    )

    assert result.workflow_status == "COMPLETED"
    # E1 owns exactly these three reads, exactly once each.
    assert e1_calls.count("prepared/prepared_case.json") == 1
    assert e1_calls.count("archive/strategy_settings.yaml") == 1
    assert e1_calls.count("archive/portfolio_snapshot.txt") == 1
    assert len(e1_calls) == 3
    # E2 never rereads any of E1's three owned leaves.
    assert "prepared/prepared_case.json" not in e2_calls
    assert "archive/strategy_settings.yaml" not in e2_calls
    assert "archive/portfolio_snapshot.txt" not in e2_calls
    # I. Successful execution reads each response exactly once.
    assert e2_calls.count("responses/h1_response.raw") == 1
    assert e2_calls.count("responses/legacy_response.raw") == 1


@pytest.mark.parametrize(
    "owner",
    [
        "p2_policy",
        "p2_portfolio",
        "p2_evidence",
        "analyst_view",
        "g2",
    ],
)
def test_h2c_consume_archived_owner_failure_before_response(
    run_env: Path,
    monkeypatch: pytest.MonkeyPatch,
    owner: str,
) -> None:
    case_root, case_sha, _set_sha, _port_sha = _create_prepared_case(run_env)
    _write_responses(case_root)

    import investment_orchestrator.offline.mmi_h2c_consume_persisted_case_v1 as ec_mod

    expected_code = H2cConsumeErrorCode.H2C_CONSUME_LIVE_CHAIN_INVALID
    expected_failure_class = H2cConsumeFailureClass.VALIDATOR_SCHEMA
    if owner == "p2_policy":
        owner_code = "MMI_POLICY_SOURCE_BYTES_INVALID"
        owner_error = ec_mod._ProjectionBlocked(owner_code)
        def fail_policy(*args, **kwargs):
            raise owner_error
        monkeypatch.setattr(
            ec_mod,
            "_build_mmi_policy_projection_from_source_bytes",
            fail_policy,
        )
    elif owner == "p2_portfolio":
        owner_code = "MMI_PORTFOLIO_SOURCE_BYTES_INVALID"
        owner_error = ec_mod._PortfolioBlocked(owner_code)
        def fail_portfolio(*args, **kwargs):
            raise owner_error
        monkeypatch.setattr(
            ec_mod,
            "_build_mmi_portfolio_snapshot_projection_from_source_bytes",
            fail_portfolio,
        )
    elif owner == "p2_evidence":
        owner_code = "MMI_AUTHENTICATED_EVIDENCE_BUNDLE_CONTRACT_INVALID"
        owner_error = ec_mod.MmiCanonicalizationError(owner_code)
        def fail_evidence(*args, **kwargs):
            raise owner_error
        monkeypatch.setattr(
            ec_mod._evidence_bundle,
            "_build_mmi_authenticated_evidence_bundle_from_components",
            fail_evidence,
        )
    elif owner == "analyst_view":
        owner_code = "MMI_ANALYST_VIEW_V2_INTERNAL_CONTRACT_FAILURE"
        analyst_failure = ec_mod.MmiPolicyProjectionBuildResult(
            status=ec_mod.MmiProjectionResultCategory.PROJECTION_CONTRACT_FAILURE,
            authority_effect=ec_mod.AUTHORITY_EFFECT_NONE,
            reason_codes=(owner_code,),
            projection=None,
        )
        def fail_analyst_view(**kwargs):
            return analyst_failure
        monkeypatch.setattr(
            ec_mod,
            "_build_mmi_analyst_visible_evidence_view_v2_from_source_record_identities",
            fail_analyst_view,
        )
    else:
        owner_code = "MMI_GROUNDED_PROMPT_V2_TEXT_INVALID"
        owner_error = ec_mod.MmiGroundedPromptV2Error(owner_code)
        expected_code = (
            H2cConsumeErrorCode.H2C_CONSUME_PROMPT_CONTRACT_INVALID
        )
        expected_failure_class = H2cConsumeFailureClass.PROMPT_CONTRACT
        def fail_g2(**kwargs):
            raise owner_error
        monkeypatch.setattr(
            ec_mod,
            "_build_mmi_grounded_prompt_v2_from_source_record_identities",
            fail_g2,
        )

    response_opens = _record_response_opens(monkeypatch)
    if owner == "p2_evidence":
        with pytest.raises(ec_mod.MmiCanonicalizationError) as exc:
            consume_h2c_persisted_case_from_archives(
                case_root=case_root,
                expected_prepared_case_identity_sha256=case_sha,
            )
        assert exc.value is owner_error
        assert exc.value.code == owner_code
    else:
        with pytest.raises(H2cConsumeError) as exc:
            consume_h2c_persisted_case_from_archives(
                case_root=case_root,
                expected_prepared_case_identity_sha256=case_sha,
            )
        assert exc.value.code == expected_code
        assert exc.value.failure_class == expected_failure_class
        assert exc.value.owner_reason_codes == (owner_code,)

    assert response_opens.count("responses/h1_response.raw") == 0
    assert response_opens.count("responses/legacy_response.raw") == 0
    _assert_no_success_artifacts(case_root)


# E. G2 canonical-byte mismatch occurs before any response is read.
def test_h2c_consume_archived_g2_mismatch_before_response(
    run_env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    case_root, _case_sha, _set_sha, _port_sha = _create_prepared_case(run_env)
    _write_responses(case_root)

    def tamper(manifest: dict[str, object]) -> None:
        manifest["grounded_prompt"]["instruction_set_version"] = "TAMPERED_VERSION"
    manifest = _resign_manifest(case_root, tamper)

    _guard_no_response_open(monkeypatch, message="Response opened despite G2 mismatch")
    with pytest.raises(H2cConsumeError) as exc:
        consume_h2c_persisted_case_from_archives(
            case_root=case_root,
            expected_prepared_case_identity_sha256=manifest[_PREPARED_CASE_IDENTITY_FIELD],
        )
    assert exc.value.code == H2cConsumeErrorCode.H2C_CONSUME_PROMPT_CONTRACT_INVALID
    assert exc.value.failure_class == H2cConsumeFailureClass.PROMPT_CONTRACT


# F. Legacy template hash mismatch occurs before any response is read.
def test_h2c_consume_archived_template_mismatch_before_response(
    run_env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    case_root, case_sha, _set_sha, _port_sha = _create_prepared_case(run_env)
    _write_responses(case_root)
    (case_root / "archive/research_dual_lane.txt").write_text("TAMPERED TEMPLATE\n")

    _guard_no_response_open(monkeypatch, message="Response opened despite template mismatch")
    with pytest.raises(H2cConsumeError) as exc:
        consume_h2c_persisted_case_from_archives(
            case_root=case_root,
            expected_prepared_case_identity_sha256=case_sha,
        )
    assert exc.value.code == H2cConsumeErrorCode.H2C_CONSUME_PROMPT_CONTRACT_INVALID


# G. Reconstructed legacy-prompt hash mismatch occurs before any response.
def test_h2c_consume_archived_legacy_prompt_mismatch_before_response(
    run_env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    case_root, _case_sha, _set_sha, _port_sha = _create_prepared_case(run_env)
    _write_responses(case_root)

    def tamper(manifest: dict[str, object]) -> None:
        manifest["legacy_prompt"]["sha256"] = "1" * 64
    manifest = _resign_manifest(case_root, tamper)

    _guard_no_response_open(monkeypatch, message="Response opened despite legacy prompt mismatch")
    with pytest.raises(H2cConsumeError) as exc:
        consume_h2c_persisted_case_from_archives(
            case_root=case_root,
            expected_prepared_case_identity_sha256=manifest[_PREPARED_CASE_IDENTITY_FIELD],
        )
    assert exc.value.code == H2cConsumeErrorCode.H2C_CONSUME_PROMPT_CONTRACT_INVALID


# H. H1 prompt hash mismatch occurs before any response is read.
def test_h2c_consume_archived_h1_prompt_mismatch_before_response(
    run_env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    case_root, case_sha, _set_sha, _port_sha = _create_prepared_case(run_env)
    _write_responses(case_root)
    (case_root / "prompts/h1_prompt.txt").write_text("TAMPERED")

    _guard_no_response_open(monkeypatch, message="Response opened despite H1 prompt mismatch")
    with pytest.raises(H2cConsumeError) as exc:
        consume_h2c_persisted_case_from_archives(
            case_root=case_root,
            expected_prepared_case_identity_sha256=case_sha,
        )
    assert exc.value.code == H2cConsumeErrorCode.H2C_CONSUME_PROMPT_CONTRACT_INVALID


# J. Downstream R1/R2 failure: responses are opened exactly once, never
# reopened after the downstream parse/validation failure.
def test_h2c_consume_archived_downstream_failure_no_response_reopen(
    run_env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    case_root, case_sha, _set_sha, _port_sha = _create_prepared_case(run_env)
    _write_responses(case_root)
    (case_root / "responses/h1_response.raw").write_bytes(b"not json")

    response_opens: list[str] = []
    orig_open = os.open
    def mocked_open(path, flags, mode=0o777, *, dir_fd=None):
        if "responses/" in str(path):
            response_opens.append(str(path))
        return orig_open(path, flags, mode, dir_fd=dir_fd)
    monkeypatch.setattr(os, "open", mocked_open)
    monkeypatch.setattr(os, "supports_dir_fd", frozenset(os.supports_dir_fd) | {mocked_open})

    with pytest.raises(H2cConsumeError) as exc:
        consume_h2c_persisted_case_from_archives(
            case_root=case_root,
            expected_prepared_case_identity_sha256=case_sha,
        )
    assert exc.value.code == H2cConsumeErrorCode.H2C_CONSUME_RESPONSE_CONTENT_INVALID
    assert response_opens.count("responses/h1_response.raw") == 1
    assert response_opens.count("responses/legacy_response.raw") == 1


# K/L/M. Receipt persisted last; a persistence failure before receipt leaves
# bundle+comparison persisted and no receipt; rerunning that partial case
# fails closed on collision, with no resume/repair/reuse.
def test_h2c_consume_archived_receipt_last_and_partial_blocks_reuse(
    run_env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    case_root, case_sha, _set_sha, _port_sha = _create_prepared_case(run_env)
    _write_responses(case_root)

    import investment_orchestrator.offline.mmi_h2c_consume_persisted_case_v1 as ec_mod

    write_order: list[str] = []
    original_write = ec_mod._write_new_exact_file
    def mocked_write(case_fd, relative_path, *, exact_bytes):
        write_order.append(relative_path)
        if relative_path == ec_mod._ARTIFACTS_RECEIPT_PATH:
            raise ec_mod.H2cConsumeError(
                code=H2cConsumeErrorCode.H2C_CONSUME_PERSISTENCE_FAILED,
                failure_class=H2cConsumeFailureClass.PERSISTENCE,
            )
        return original_write(case_fd, relative_path, exact_bytes=exact_bytes)
    monkeypatch.setattr(ec_mod, "_write_new_exact_file", mocked_write)

    with pytest.raises(H2cConsumeError) as exc:
        consume_h2c_persisted_case_from_archives(
            case_root=case_root,
            expected_prepared_case_identity_sha256=case_sha,
        )
    assert exc.value.code == H2cConsumeErrorCode.H2C_CONSUME_PERSISTENCE_FAILED
    assert write_order == [
        ec_mod._ARTIFACTS_BUNDLE_PATH,
        ec_mod._ARTIFACTS_REPORT_PATH,
        ec_mod._ARTIFACTS_RECEIPT_PATH,
    ]
    assert (case_root / "artifacts/case_evidence_bundle.json").exists()
    assert (case_root / "artifacts/comparison_report.json").exists()
    assert not (case_root / "artifacts/receipt.json").exists()

    monkeypatch.undo()
    with pytest.raises(H2cConsumeError) as exc2:
        consume_h2c_persisted_case_from_archives(
            case_root=case_root,
            expected_prepared_case_identity_sha256=case_sha,
        )
    assert exc2.value.code == H2cConsumeErrorCode.H2C_CONSUME_COLLISION


# N. No MmiCapturedSource construction / live recapture is ever used.
def test_h2c_consume_archived_never_recaptures_live_source(
    run_env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    case_root, case_sha, _set_sha, _port_sha = _create_prepared_case(run_env)
    _write_responses(case_root)

    import investment_orchestrator.offline.mmi_h2c_consume_persisted_case_v1 as ec_mod

    def blow_up(*args, **kwargs):
        raise AssertionError(
            "capture_current_mmi_source must never be called by the archived path"
        )
    monkeypatch.setattr(ec_mod, "capture_current_mmi_source", blow_up)

    result = consume_h2c_persisted_case_from_archives(
        case_root=case_root,
        expected_prepared_case_identity_sha256=case_sha,
    )
    assert result.workflow_status == "COMPLETED"


# O. The archived public entry has exactly one production caller: its CLI.
def test_h2c_consume_archived_has_only_operator_cli_consumer() -> None:
    target = "consume_h2c_persisted_case_from_archives"
    owner_module = repo_root() / (
        "src/investment_orchestrator/offline/mmi_h2c_consume_persisted_case_v1.py"
    )
    observed = []
    for path in (repo_root() / "src").rglob("*.py"):
        if path == owner_module:
            continue
        if target in path.read_text(encoding="utf-8"):
            observed.append(path.relative_to(repo_root()).as_posix())
    assert tuple(sorted(observed)) == (
        "src/investment_orchestrator/cli/run_mmi_h2c_consume_archived.py",
    )
