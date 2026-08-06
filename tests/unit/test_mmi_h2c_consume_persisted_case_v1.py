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
    H2cConsumeErrorCode,
    H2cConsumeFailureClass,
    H2cConsumeResult,
    consume_h2c_persisted_case,
    _MANIFEST_MAXIMUM_BYTES,
)
from investment_orchestrator.offline.mmi_h2c_prepare_persisted_case_v1 import (
    prepare_h2c_persisted_case,
)
from investment_orchestrator.mmi.canonical import (
    MAXIMUM_MMI_RAW_RESPONSE_BYTES,
    MAX_MMI_H2C_CASE_EVIDENCE_BUNDLE_V1_CANONICAL_BYTES,
    MAX_MMI_LEGACY_STEP1_COMPARISON_REPORT_V1_CANONICAL_BYTES,
    canonical_json_bytes,
)
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

def _create_prepared_case(tmp_path: Path, settings_suffix=b"") -> tuple[Path, str, str, str]:
    case_root = tmp_path / "case_root"
    settings = _settings_bytes() + settings_suffix
    portfolio = _portfolio_bytes()

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
    assert len(H2cConsumeErrorCode) == 14

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
