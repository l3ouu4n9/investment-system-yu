from __future__ import annotations

from collections.abc import Mapping
from dataclasses import FrozenInstanceError, fields
import hashlib
import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from investment_orchestrator.common.paths import prompt_path, repo_root
from investment_orchestrator.offline.mmi_h2c_case_bundle_v1 import (
    validate_mmi_h2c_case_evidence_bundle_v1,
)
from investment_orchestrator.offline import mmi_h2c_manual_capture_session as session
from investment_orchestrator.offline.mmi_h2c_dual_side_manual_handoff_context_receipt_v1 import (
    MmiH2cDualSideManualHandoffContextReceiptV1Error,
    validate_mmi_h2c_dual_side_manual_handoff_context_receipt_v1_portable_evidence,
)


class _ResponseHandoff:
    def __init__(
        self,
        *,
        h1_prompt_path: Path,
        h1_response_path: Path,
        legacy_response_path: Path,
        legacy_response_bytes: bytes = b"",
        h1_response_override: bytes | None = None,
    ) -> None:
        self.h1_prompt_path = h1_prompt_path
        self.h1_response_path = h1_response_path
        self.legacy_response_path = legacy_response_path
        self.legacy_response_bytes = legacy_response_bytes
        self.h1_response_override = h1_response_override
        self.call_count = 0

    def await_response_files_ready(self) -> None:
        self.call_count += 1
        prompt = self.h1_prompt_path.read_text(encoding="utf-8")
        context = prompt.split(
            "PROMPT_CONTEXT_BINDING_SHA256=", 1
        )[1].splitlines()[0]
        framed = prompt.split("MMI_V2_EVIDENCE_FRAME_START\n", 1)[1]
        view = json.loads(framed.splitlines()[1])
        rows = [
            {
                "ticker": item["ticker"],
                "evidence_status": "EVIDENCE_SUPPORTED",
                "rationale_12m_plus": "R" * 40,
                "references": [f"POLICY.INSTRUMENT.{index:04d}"],
            }
            for index, item in enumerate(
                view["policy_view"]["analysis_instruments"], start=1
            )
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
        response = (
            self.h1_response_override
            if self.h1_response_override is not None
            else json.dumps(
                payload,
                ensure_ascii=False,
                separators=(",", ":"),
                allow_nan=False,
            ).encode("utf-8")
        )
        self.h1_response_path.write_bytes(response)
        self.legacy_response_path.write_bytes(self.legacy_response_bytes)


class _CancelHandoff:
    def __init__(self) -> None:
        self.call_count = 0

    def await_response_files_ready(self) -> None:
        self.call_count += 1
        raise KeyboardInterrupt


def _source_hashes() -> tuple[str, str]:
    settings = (
        repo_root() / "inputs/current/strategy_settings.yaml"
    ).read_bytes()
    portfolio = (
        repo_root() / "inputs/current/portfolio_snapshot.txt"
    ).read_bytes()
    return (
        hashlib.sha256(settings).hexdigest(),
        hashlib.sha256(portfolio).hexdigest(),
    )


def _paths(root: Path) -> dict[str, Path]:
    return {
        "h1_prompt_output_path": root / "h1-prompt",
        "legacy_prompt_output_path": root / "legacy-prompt",
        "h1_response_path": root / "h1-response",
        "legacy_response_path": root / "legacy-response",
        "case_evidence_bundle_output_path": root / "case-bundle",
        "comparison_report_output_path": root / "h2",
        "receipt_output_path": root / "receipt",
    }


def _run(
    root: Path,
    *,
    legacy_response_bytes: bytes = b"",
    h1_response_override: bytes | None = None,
) -> tuple[session.H2cManualCaptureResult, _ResponseHandoff]:
    paths = _paths(root)
    settings_hash, portfolio_hash = _source_hashes()
    handoff = _ResponseHandoff(
        h1_prompt_path=paths["h1_prompt_output_path"],
        h1_response_path=paths["h1_response_path"],
        legacy_response_path=paths["legacy_response_path"],
        legacy_response_bytes=legacy_response_bytes,
        h1_response_override=h1_response_override,
    )
    result = session.run_h2c_manual_capture(
        strategy_settings_expected_sha256=settings_hash,
        portfolio_snapshot_expected_sha256=portfolio_hash,
        operator_handoff=handoff,
        **paths,
    )
    return result, handoff


def test_complete_foreground_session_writes_bundle_h2_then_receipt(
    tmp_path: Path,
) -> None:
    result, handoff = _run(tmp_path)
    paths = _paths(tmp_path)
    assert handoff.call_count == 1
    assert paths["h1_prompt_output_path"].read_bytes()
    assert paths["legacy_prompt_output_path"].read_bytes().endswith(b"\n")
    bundle_bytes = paths["case_evidence_bundle_output_path"].read_bytes()
    h2_bytes = paths["comparison_report_output_path"].read_bytes()
    receipt_bytes = paths["receipt_output_path"].read_bytes()
    assert not bundle_bytes.endswith(b"\n")
    assert not h2_bytes.endswith(b"\n")
    assert len(receipt_bytes) == 1114
    bundle = json.loads(bundle_bytes)
    h2 = json.loads(h2_bytes)
    receipt = json.loads(receipt_bytes)
    assert validate_mmi_h2c_case_evidence_bundle_v1(bundle=bundle) is None
    assert bundle["case_evidence_bundle_identity_sha256"]
    assert h2["comparison_report_identity_sha256"] == (
        result.comparison_report_identity_sha256
    )
    assert receipt["receipt_identity_sha256"] == (
        result.receipt_identity_sha256
    )
    assert receipt["comparison_report_identity_sha256"] == (
        result.comparison_report_identity_sha256
    )
    assert h2["legacy_contract_status"]["raw_parse_status"] == (
        "LEGACY_PARSE_FAILURE"
    )


def _run_completed_capture(
    root: Path,
) -> tuple[session.H2cManualCaptureResult, dict[str, Path]]:
    """Cross the session boundary carrying only result values and paths."""
    result, handoff = _run(root)
    assert handoff.call_count == 1
    return result, _paths(root)


def _portable_evidence_from_disk(
    paths: Mapping[str, Path],
) -> dict[str, object]:
    bundle = json.loads(
        paths["case_evidence_bundle_output_path"].read_bytes()
    )
    assert type(bundle) is dict
    assert validate_mmi_h2c_case_evidence_bundle_v1(bundle=bundle) is None
    return {
        "receipt": json.loads(paths["receipt_output_path"].read_bytes()),
        "comparison_report": json.loads(
            paths["comparison_report_output_path"].read_bytes()
        ),
        "legacy_step1_compatibility_candidate": bundle[
            "legacy_step1_compatibility_candidate"
        ],
        "validated_grounded_analysis_response": bundle[
            "validated_grounded_analysis_response"
        ],
        "raw_response_envelope": bundle["raw_response_envelope"],
        "grounded_prompt": bundle["grounded_prompt"],
        "archived_h1_prompt_bytes": paths[
            "h1_prompt_output_path"
        ].read_bytes(),
        "archived_h1_response_bytes": paths["h1_response_path"].read_bytes(),
        "archived_legacy_response_bytes": paths[
            "legacy_response_path"
        ].read_bytes(),
        "archived_strategy_settings_bytes": (
            repo_root() / "inputs/current/strategy_settings.yaml"
        ).read_bytes(),
        "strategy_settings_source_record": bundle[
            "strategy_settings_source_record"
        ],
        "archived_portfolio_snapshot_bytes": (
            repo_root() / "inputs/current/portfolio_snapshot.txt"
        ).read_bytes(),
        "portfolio_snapshot_source_record": bundle[
            "portfolio_snapshot_source_record"
        ],
        "archived_legacy_prompt_template_bytes": prompt_path(
            "research_dual_lane.txt"
        ).read_bytes(),
        "archived_legacy_prompt_bytes": paths[
            "legacy_prompt_output_path"
        ].read_bytes(),
    }


def _persisted_bundle(paths: Mapping[str, Path]) -> dict[str, object]:
    value = json.loads(
        paths["case_evidence_bundle_output_path"].read_bytes()
    )
    assert type(value) is dict
    return value


def test_production_receipt_composes_with_portable_evidence_validator(
    tmp_path: Path,
) -> None:
    result, paths = _run_completed_capture(tmp_path)
    assert result.comparison_report_identity_sha256
    assert result.receipt_identity_sha256
    evidence = _portable_evidence_from_disk(paths)
    assert (
        validate_mmi_h2c_dual_side_manual_handoff_context_receipt_v1_portable_evidence(
            **evidence
        )
        is None
    )


def test_production_receipt_composition_fails_closed_on_tampered_archive(
    tmp_path: Path,
) -> None:
    _result, paths = _run_completed_capture(tmp_path)
    evidence = _portable_evidence_from_disk(paths)
    tampered = bytearray(evidence["archived_h1_prompt_bytes"])
    tampered[-1] ^= 0xFF
    evidence["archived_h1_prompt_bytes"] = bytes(tampered)
    with pytest.raises(
        MmiH2cDualSideManualHandoffContextReceiptV1Error,
        match="^MMI_H2C_PORTABLE_EVIDENCE_INVALID$",
    ):
        validate_mmi_h2c_dual_side_manual_handoff_context_receipt_v1_portable_evidence(
            **evidence
        )


def test_same_session_bundle_provenance_uses_exactly_two_source_captures(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    observed: list[tuple[object, object]] = []
    original = session.capture_current_mmi_source

    def capture(role: object, **kwargs: object) -> object:
        result = original(role, **kwargs)
        observed.append((role, result))
        return result

    monkeypatch.setattr(session, "capture_current_mmi_source", capture)
    _result, paths = _run_completed_capture(tmp_path)

    assert [role for role, _result in observed] == [
        session.MmiSourceRole.STRATEGY_SETTINGS,
        session.MmiSourceRole.PORTFOLIO_SNAPSHOT,
    ]
    assert len(observed) == 2
    settings_result = observed[0][1]
    portfolio_result = observed[1][1]
    assert type(settings_result) is session.MmiSourceCaptureResult
    assert type(portfolio_result) is session.MmiSourceCaptureResult
    assert settings_result.source is not None
    assert portfolio_result.source is not None

    bundle = _persisted_bundle(paths)
    h2 = json.loads(paths["comparison_report_output_path"].read_bytes())
    receipt = json.loads(paths["receipt_output_path"].read_bytes())
    assert bundle["strategy_settings_source_record"] == dict(
        settings_result.source.source_record
    )
    assert bundle["portfolio_snapshot_source_record"] == dict(
        portfolio_result.source.source_record
    )

    g2 = bundle["grounded_prompt"]
    r1 = bundle["raw_response_envelope"]
    r2 = bundle["validated_grounded_analysis_response"]
    h1 = bundle["legacy_step1_compatibility_candidate"]
    assert type(g2) is dict
    assert type(r1) is dict
    assert type(r2) is dict
    assert type(h1) is dict
    assert type(h2) is dict
    assert type(receipt) is dict
    assert r1["grounded_prompt_artifact_identity_sha256"] == (
        g2["grounded_prompt_artifact_identity_sha256"]
    )
    assert r2["raw_response_envelope_identity_sha256"] == (
        r1["raw_response_envelope_identity_sha256"]
    )
    h1_provenance = h1["provenance"]
    h2_provenance = h2["provenance"]
    assert type(h1_provenance) is dict
    assert type(h2_provenance) is dict
    assert h1_provenance[
        "validated_grounded_analysis_response_identity_sha256"
    ] == r2["validated_grounded_analysis_response_identity_sha256"]
    assert h2_provenance[
        "legacy_step1_compatibility_candidate_identity_sha256"
    ] == h1["legacy_step1_compatibility_candidate_identity_sha256"]
    assert receipt["strategy_settings_source_record_identity_sha256"] == (
        bundle["strategy_settings_source_record"][
            "source_record_identity_sha256"
        ]
    )
    assert receipt["portfolio_snapshot_source_record_identity_sha256"] == (
        bundle["portfolio_snapshot_source_record"][
            "source_record_identity_sha256"
        ]
    )
    evidence = _portable_evidence_from_disk(paths)
    assert (
        validate_mmi_h2c_dual_side_manual_handoff_context_receipt_v1_portable_evidence(
            **evidence
        )
        is None
    )


def test_error_enum_mapping_is_exact_and_error_is_immutable() -> None:
    assert len(session.H2cManualCaptureErrorCode) == 19
    assert len(session.H2cManualCaptureFailureClass) == 8
    assert set(session._ERROR_CLASSES) == set(
        session.H2cManualCaptureErrorCode
    )
    for code, failure_class in session._ERROR_CLASSES.items():
        error = session.H2cManualCaptureError(
            code=code,
            failure_class=failure_class,
            owner_reason_codes=("OWNER_A", "OWNER_A"),
        )
        assert str(error) == code.value
        assert error.args == (code.value,)
        assert error.owner_reason_codes == ("OWNER_A", "OWNER_A")
        with pytest.raises(FrozenInstanceError):
            error.code = code  # type: ignore[misc]
    assert session._ERROR_CLASSES[
        session.H2cManualCaptureErrorCode.H2C_CASE_EVIDENCE_BUNDLE_VALIDATION_INVALID
    ] is session.H2cManualCaptureFailureClass.VALIDATOR_SCHEMA
    assert session._ERROR_CLASSES[
        session.H2cManualCaptureErrorCode.H2C_CASE_EVIDENCE_BUNDLE_PERSISTENCE_FAILED
    ] is session.H2cManualCaptureFailureClass.PERSISTENCE


def test_result_shape_remains_exactly_two_identities() -> None:
    assert tuple(field.name for field in fields(session.H2cManualCaptureResult)) == (
        "comparison_report_identity_sha256",
        "receipt_identity_sha256",
    )


def _preflight_error(
    paths: Mapping[str, object],
    monkeypatch: pytest.MonkeyPatch,
) -> session.H2cManualCaptureError:
    monkeypatch.setattr(
        session,
        "begin_mmi_projection_run",
        lambda: pytest.fail("live run must not begin"),
    )
    settings_hash, portfolio_hash = _source_hashes()
    with pytest.raises(session.H2cManualCaptureError) as captured:
        session.run_h2c_manual_capture(
            strategy_settings_expected_sha256=settings_hash,
            portfolio_snapshot_expected_sha256=portfolio_hash,
            operator_handoff=_CancelHandoff(),
            **paths,  # type: ignore[arg-type]
        )
    return captured.value


@pytest.mark.parametrize(
    "aliased_role",
    (
        "h1_prompt_output_path",
        "legacy_prompt_output_path",
        "h1_response_path",
        "legacy_response_path",
        "comparison_report_output_path",
        "receipt_output_path",
    ),
)
def test_bundle_path_rejects_each_normalized_role_alias_before_live_run(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    aliased_role: str,
) -> None:
    paths = _paths(tmp_path)
    other = paths[aliased_role]
    paths["case_evidence_bundle_output_path"] = Path(
        os.fspath(other.parent / "normalized-away" / ".." / other.name)
    )
    error = _preflight_error(paths, monkeypatch)
    assert error.code is (
        session.H2cManualCaptureErrorCode.H2C_PATH_CONTRACT_INVALID
    )


def test_existing_bundle_leaf_fails_preflight(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paths = _paths(tmp_path)
    paths["case_evidence_bundle_output_path"].write_bytes(b"existing")
    error = _preflight_error(paths, monkeypatch)
    assert error.code is (
        session.H2cManualCaptureErrorCode.H2C_PATH_CONTRACT_INVALID
    )


def test_missing_bundle_parent_fails_preflight(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paths = _paths(tmp_path)
    paths["case_evidence_bundle_output_path"] = (
        tmp_path / "missing-parent" / "bundle"
    )
    error = _preflight_error(paths, monkeypatch)
    assert error.code is (
        session.H2cManualCaptureErrorCode.H2C_PATH_CONTRACT_INVALID
    )


def test_nondirectory_bundle_parent_fails_preflight(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paths = _paths(tmp_path)
    parent = tmp_path / "not-a-directory"
    parent.write_bytes(b"file")
    paths["case_evidence_bundle_output_path"] = parent / "bundle"
    error = _preflight_error(paths, monkeypatch)
    assert error.code is (
        session.H2cManualCaptureErrorCode.H2C_PATH_CONTRACT_INVALID
    )


def test_relative_bundle_path_fails_preflight(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paths = _paths(tmp_path)
    paths["case_evidence_bundle_output_path"] = Path("relative-bundle")
    error = _preflight_error(paths, monkeypatch)
    assert error.code is (
        session.H2cManualCaptureErrorCode.H2C_PATH_CONTRACT_INVALID
    )


def test_wrong_bundle_path_type_fails_preflight(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paths: dict[str, object] = dict(_paths(tmp_path))
    paths["case_evidence_bundle_output_path"] = os.fspath(
        tmp_path / "bundle"
    )
    error = _preflight_error(paths, monkeypatch)
    assert error.code is session.H2cManualCaptureErrorCode.H2C_ARGUMENT_INVALID


def test_dangling_bundle_symlink_fails_preflight(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paths = _paths(tmp_path)
    paths["case_evidence_bundle_output_path"].symlink_to(
        tmp_path / "missing-target"
    )
    error = _preflight_error(paths, monkeypatch)
    assert error.code is (
        session.H2cManualCaptureErrorCode.H2C_PATH_CONTRACT_INVALID
    )


def test_operator_cancellation_leaves_only_inert_prompts(
    tmp_path: Path,
) -> None:
    paths = _paths(tmp_path)
    settings_hash, portfolio_hash = _source_hashes()
    handoff = _CancelHandoff()
    with pytest.raises(session.H2cManualCaptureError) as captured:
        session.run_h2c_manual_capture(
            strategy_settings_expected_sha256=settings_hash,
            portfolio_snapshot_expected_sha256=portfolio_hash,
            operator_handoff=handoff,
            **paths,
        )
    assert captured.value.code is (
        session.H2cManualCaptureErrorCode.H2C_OPERATOR_CANCELLED
    )
    assert handoff.call_count == 1
    assert paths["h1_prompt_output_path"].exists()
    assert paths["legacy_prompt_output_path"].exists()
    assert not paths["case_evidence_bundle_output_path"].exists()
    assert not paths["comparison_report_output_path"].exists()
    assert not paths["receipt_output_path"].exists()


def test_expected_source_hash_mismatch_fails_before_prompt_exposure(
    tmp_path: Path,
) -> None:
    paths = _paths(tmp_path)
    _settings_hash, portfolio_hash = _source_hashes()
    with pytest.raises(session.H2cManualCaptureError) as captured:
        session.run_h2c_manual_capture(
            strategy_settings_expected_sha256="0" * 64,
            portfolio_snapshot_expected_sha256=portfolio_hash,
            operator_handoff=_CancelHandoff(),
            **paths,
        )
    assert captured.value.code is (
        session.H2cManualCaptureErrorCode.H2C_SOURCE_CAPTURE_INVALID
    )
    assert not any(path.exists() for path in paths.values())


@pytest.mark.parametrize("bad_response", (b"", b"\xff", b"{}"))
def test_invalid_h1_response_content_has_one_public_code(
    tmp_path: Path,
    bad_response: bytes,
) -> None:
    with pytest.raises(session.H2cManualCaptureError) as captured:
        _run(tmp_path, h1_response_override=bad_response)
    assert captured.value.code is (
        session.H2cManualCaptureErrorCode.H2C_RESPONSE_CONTENT_INVALID
    )
    assert not (tmp_path / "case-bundle").exists()
    assert not (tmp_path / "h2").exists()
    assert not (tmp_path / "receipt").exists()


def test_malformed_utf8_legacy_response_is_unrepresentable(
    tmp_path: Path,
) -> None:
    with pytest.raises(session.H2cManualCaptureError) as captured:
        _run(tmp_path, legacy_response_bytes=b"\xff")
    assert captured.value.code is (
        session.H2cManualCaptureErrorCode.H2C_RESPONSE_CONTENT_INVALID
    )


def test_response_inode_alias_is_rejected(tmp_path: Path) -> None:
    first = tmp_path / "first"
    second = tmp_path / "second"
    first.write_bytes(b"x")
    os.link(first, second)
    with pytest.raises(session.H2cManualCaptureError) as captured:
        session._stable_read_response_pair(
            h1_path=first,
            legacy_path=second,
        )
    assert captured.value.code is (
        session.H2cManualCaptureErrorCode.H2C_RESPONSE_INPUT_INVALID
    )


@pytest.mark.parametrize("leaf_kind", ("symlink", "directory"))
def test_response_final_component_must_be_regular_and_not_a_symlink(
    tmp_path: Path,
    leaf_kind: str,
) -> None:
    h1 = tmp_path / "h1"
    legacy = tmp_path / "legacy"
    legacy.write_bytes(b"")
    if leaf_kind == "symlink":
        target = tmp_path / "target"
        target.write_bytes(b"{}")
        h1.symlink_to(target)
    else:
        h1.mkdir()
    with pytest.raises(session.H2cManualCaptureError) as captured:
        session._stable_read_response_pair(
            h1_path=h1,
            legacy_path=legacy,
        )
    assert captured.value.code is (
        session.H2cManualCaptureErrorCode.H2C_RESPONSE_INPUT_INVALID
    )


def test_response_stability_witness_rejects_mutation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    h1 = tmp_path / "h1"
    legacy = tmp_path / "legacy"
    h1.write_bytes(b"{}")
    legacy.write_bytes(b"")
    original = session.os.fstat
    calls = 0

    def changed_on_h1_postread(fd: int) -> object:
        nonlocal calls
        calls += 1
        value = original(fd)
        if calls != 3:
            return value
        return SimpleNamespace(
            st_dev=value.st_dev,
            st_ino=value.st_ino,
            st_mode=value.st_mode,
            st_size=value.st_size,
            st_mtime_ns=value.st_mtime_ns + 1,
            st_ctime_ns=value.st_ctime_ns,
        )

    monkeypatch.setattr(session.os, "fstat", changed_on_h1_postread)
    with pytest.raises(session.H2cManualCaptureError) as captured:
        session._stable_read_response_pair(
            h1_path=h1,
            legacy_path=legacy,
        )
    assert captured.value.code is (
        session.H2cManualCaptureErrorCode.H2C_RESPONSE_INPUT_INVALID
    )


def test_response_reader_accepts_owner_maximum_and_zero_legacy(
    tmp_path: Path,
) -> None:
    h1 = tmp_path / "h1"
    legacy = tmp_path / "legacy"
    h1.write_bytes(b"x" * session.MAXIMUM_MMI_RAW_RESPONSE_BYTES)
    legacy.write_bytes(b"")
    h1_bytes, legacy_bytes = session._stable_read_response_pair(
        h1_path=h1,
        legacy_path=legacy,
    )
    assert len(h1_bytes) == session.MAXIMUM_MMI_RAW_RESPONSE_BYTES
    assert legacy_bytes == b""


def test_legacy_conversion_matches_universal_newline_semantics() -> None:
    assert session._legacy_text(b"a\r\nb\rc\n") == "a\nb\nc\n"


def test_bundle_owner_failure_translates_exact_reason_and_class(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail(**_kwargs: object) -> object:
        raise session._case_bundle.MmiH2cCaseEvidenceBundleV1Error(
            "MMI_H2C_CASE_EVIDENCE_BUNDLE_V1_INVALID"
        )

    monkeypatch.setattr(
        session._case_bundle,
        "_build_mmi_h2c_case_evidence_bundle_v1",
        fail,
    )
    with pytest.raises(session.H2cManualCaptureError) as captured:
        _run(tmp_path)
    assert captured.value.code is (
        session.H2cManualCaptureErrorCode.H2C_CASE_EVIDENCE_BUNDLE_VALIDATION_INVALID
    )
    assert captured.value.failure_class is (
        session.H2cManualCaptureFailureClass.VALIDATOR_SCHEMA
    )
    assert captured.value.owner_reason_codes == (
        "MMI_H2C_CASE_EVIDENCE_BUNDLE_V1_INVALID",
    )
    assert not (tmp_path / "case-bundle").exists()
    assert not (tmp_path / "h2").exists()
    assert not (tmp_path / "receipt").exists()


def test_unknown_bundle_owner_code_remains_a_true_bug(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    error = session._case_bundle.MmiH2cCaseEvidenceBundleV1Error(
        "MMI_H2C_CASE_EVIDENCE_BUNDLE_V1_INVALID"
    )
    error.code = "UNKNOWN"  # type: ignore[assignment]

    def fail(**_kwargs: object) -> object:
        raise error

    monkeypatch.setattr(
        session._case_bundle,
        "_build_mmi_h2c_case_evidence_bundle_v1",
        fail,
    )
    with pytest.raises(
        session._case_bundle.MmiH2cCaseEvidenceBundleV1Error
    ) as captured:
        _run(tmp_path)
    assert captured.value is error


def test_bundle_canonicalization_failure_is_controlled_validation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original = session.canonical_json_bytes

    def fail_bundle(value: object, **kwargs: object) -> bytes:
        if (
            isinstance(value, Mapping)
            and value.get("artifact_kind")
            == "MMI_H2C_CASE_EVIDENCE_BUNDLE"
        ):
            raise session.MmiCanonicalizationError("CONTROLLED")
        return original(value, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(session, "canonical_json_bytes", fail_bundle)
    with pytest.raises(session.H2cManualCaptureError) as captured:
        _run(tmp_path)
    assert captured.value.code is (
        session.H2cManualCaptureErrorCode.H2C_CASE_EVIDENCE_BUNDLE_VALIDATION_INVALID
    )
    assert captured.value.failure_class is (
        session.H2cManualCaptureFailureClass.VALIDATOR_SCHEMA
    )
    assert captured.value.owner_reason_codes == ()


def test_all_result_bytes_are_canonical_before_bundle_h2_receipt_writes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paths = _paths(tmp_path)
    artifact_kind_by_path = {
        paths["case_evidence_bundle_output_path"]: (
            "MMI_H2C_CASE_EVIDENCE_BUNDLE"
        ),
        paths["comparison_report_output_path"]: (
            "MMI_LEGACY_STEP1_COMPARISON_REPORT"
        ),
        paths["receipt_output_path"]: (
            "MMI_H2C_DUAL_SIDE_MANUAL_HANDOFF_CONTEXT_RECEIPT"
        ),
    }
    prepared: dict[str, bytes] = {}
    writes: list[Path] = []
    original_canonical = session.canonical_json_bytes
    original_write = session._write_new_exact_file

    def canonical(value: object, **kwargs: object) -> bytes:
        exact_bytes = original_canonical(
            value, **kwargs  # type: ignore[arg-type]
        )
        if isinstance(value, Mapping):
            artifact_kind = value.get("artifact_kind")
            if artifact_kind in set(artifact_kind_by_path.values()):
                assert type(artifact_kind) is str
                prepared[artifact_kind] = exact_bytes
        return exact_bytes

    def write(**kwargs: object) -> object:
        path = kwargs["path"]
        assert isinstance(path, Path)
        if path in artifact_kind_by_path:
            assert set(prepared) == set(artifact_kind_by_path.values())
            assert kwargs["exact_bytes"] == prepared[
                artifact_kind_by_path[path]
            ]
            writes.append(path)
        return original_write(**kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(session, "canonical_json_bytes", canonical)
    monkeypatch.setattr(session, "_write_new_exact_file", write)
    _run(tmp_path)
    assert writes == [
        paths["case_evidence_bundle_output_path"],
        paths["comparison_report_output_path"],
        paths["receipt_output_path"],
    ]


def test_bundle_persistence_failure_prevents_h2_and_receipt_attempts(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paths = _paths(tmp_path)
    original = session._write_new_exact_file
    observed: list[Path] = []

    def fail_bundle(**kwargs: object) -> object:
        path = kwargs["path"]
        assert isinstance(path, Path)
        if path in {
            paths["case_evidence_bundle_output_path"],
            paths["comparison_report_output_path"],
            paths["receipt_output_path"],
        }:
            observed.append(path)
        if path == paths["case_evidence_bundle_output_path"]:
            raise session.H2cManualCaptureError(
                code=(
                    session.H2cManualCaptureErrorCode.H2C_CASE_EVIDENCE_BUNDLE_PERSISTENCE_FAILED
                ),
                failure_class=(
                    session.H2cManualCaptureFailureClass.PERSISTENCE
                ),
            )
        return original(**kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(session, "_write_new_exact_file", fail_bundle)
    with pytest.raises(session.H2cManualCaptureError) as captured:
        _run(tmp_path)
    assert captured.value.code is (
        session.H2cManualCaptureErrorCode.H2C_CASE_EVIDENCE_BUNDLE_PERSISTENCE_FAILED
    )
    assert captured.value.failure_class is (
        session.H2cManualCaptureFailureClass.PERSISTENCE
    )
    assert observed == [paths["case_evidence_bundle_output_path"]]
    assert not paths["case_evidence_bundle_output_path"].exists()
    assert not paths["comparison_report_output_path"].exists()
    assert not paths["receipt_output_path"].exists()


def test_h2_persistence_failure_leaves_bundle_and_prevents_receipt_attempt(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paths = _paths(tmp_path)
    original = session._write_new_exact_file
    observed: list[Path] = []

    def fail_h2(**kwargs: object) -> object:
        path = kwargs["path"]
        assert isinstance(path, Path)
        if path in {
            paths["case_evidence_bundle_output_path"],
            paths["comparison_report_output_path"],
            paths["receipt_output_path"],
        }:
            observed.append(path)
        if path == paths["comparison_report_output_path"]:
            raise session.H2cManualCaptureError(
                code=(
                    session.H2cManualCaptureErrorCode.H2C_H2_PERSISTENCE_FAILED
                ),
                failure_class=(
                    session.H2cManualCaptureFailureClass.PERSISTENCE
                ),
            )
        return original(**kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(session, "_write_new_exact_file", fail_h2)
    with pytest.raises(session.H2cManualCaptureError) as captured:
        _run(tmp_path)
    assert captured.value.code is (
        session.H2cManualCaptureErrorCode.H2C_H2_PERSISTENCE_FAILED
    )
    assert captured.value.failure_class is (
        session.H2cManualCaptureFailureClass.PERSISTENCE
    )
    assert observed == [
        paths["case_evidence_bundle_output_path"],
        paths["comparison_report_output_path"],
    ]
    assert paths["case_evidence_bundle_output_path"].exists()
    assert not paths["comparison_report_output_path"].exists()
    assert not paths["receipt_output_path"].exists()


def test_receipt_persistence_failure_leaves_confirmed_bundle_and_h2(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paths = _paths(tmp_path)
    original = session._write_new_exact_file
    observed: list[Path] = []

    def fail_receipt(**kwargs: object) -> object:
        path = kwargs["path"]
        assert isinstance(path, Path)
        if path in {
            paths["case_evidence_bundle_output_path"],
            paths["comparison_report_output_path"],
            paths["receipt_output_path"],
        }:
            observed.append(path)
        if path == paths["receipt_output_path"]:
            raise session.H2cManualCaptureError(
                code=(
                    session.H2cManualCaptureErrorCode.H2C_RECEIPT_PERSISTENCE_FAILED
                ),
                failure_class=(
                    session.H2cManualCaptureFailureClass.PERSISTENCE
                ),
            )
        return original(**kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(session, "_write_new_exact_file", fail_receipt)
    with pytest.raises(session.H2cManualCaptureError) as captured:
        _run(tmp_path)
    assert captured.value.code is (
        session.H2cManualCaptureErrorCode.H2C_RECEIPT_PERSISTENCE_FAILED
    )
    assert captured.value.failure_class is (
        session.H2cManualCaptureFailureClass.PERSISTENCE
    )
    assert observed == [
        paths["case_evidence_bundle_output_path"],
        paths["comparison_report_output_path"],
        paths["receipt_output_path"],
    ]
    assert paths["case_evidence_bundle_output_path"].exists()
    assert paths["comparison_report_output_path"].exists()
    assert not paths["receipt_output_path"].exists()


@pytest.mark.parametrize("fsync_call", (1, 2))
def test_file_and_parent_fsync_failures_use_bundle_persistence_code(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    fsync_call: int,
) -> None:
    original = session.os.fsync
    calls = 0

    def fail_selected(fd: int) -> None:
        nonlocal calls
        calls += 1
        if calls == fsync_call:
            raise OSError(session.errno.ENOSPC, "controlled")
        original(fd)

    monkeypatch.setattr(session.os, "fsync", fail_selected)
    with pytest.raises(session.H2cManualCaptureError) as captured:
        session._write_new_exact_file(
            path=tmp_path / "out",
            exact_bytes=b"exact",
            failure_code=(
                session.H2cManualCaptureErrorCode.H2C_CASE_EVIDENCE_BUNDLE_PERSISTENCE_FAILED
            ),
        )
    assert captured.value.code is (
        session.H2cManualCaptureErrorCode.H2C_CASE_EVIDENCE_BUNDLE_PERSISTENCE_FAILED
    )
    assert captured.value.failure_class is (
        session.H2cManualCaptureFailureClass.PERSISTENCE
    )


def test_unknown_filesystem_errno_remains_a_true_bug(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def unknown(*_args: object, **_kwargs: object) -> int:
        raise OSError(9999, "unknown")

    monkeypatch.setattr(session.os, "open", unknown)
    with pytest.raises(OSError) as captured:
        session._stable_read_response_pair(
            h1_path=tmp_path / "h1",
            legacy_path=tmp_path / "legacy",
        )
    assert captured.value.errno == 9999


def test_unknown_bundle_persistence_errno_remains_a_true_bug(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def unknown(*_args: object, **_kwargs: object) -> int:
        raise OSError(9999, "unknown")

    monkeypatch.setattr(session.os, "open", unknown)
    with pytest.raises(OSError) as captured:
        session._write_new_exact_file(
            path=tmp_path / "case-bundle",
            exact_bytes=b"exact",
            failure_code=(
                session.H2cManualCaptureErrorCode.H2C_CASE_EVIDENCE_BUNDLE_PERSISTENCE_FAILED
            ),
        )
    assert captured.value.errno == 9999


def test_unknown_named_owner_code_is_a_true_bug() -> None:
    with pytest.raises(RuntimeError, match="undocumented MMI owner error code"):
        session._raise_named_owner(
            observed_code="UNKNOWN",
            allowed_codes=frozenset({"KNOWN"}),
            response_codes=frozenset(),
            response_public_code=(
                session.H2cManualCaptureErrorCode.H2C_RESPONSE_CONTENT_INVALID
            ),
            remaining_public_code=(
                session.H2cManualCaptureErrorCode.H2C_LIVE_CHAIN_INVALID
            ),
        )


def test_documented_result_reasons_translate_but_unknown_reasons_are_bugs() -> None:
    documented = session.MmiPolicyProjectionBuildResult(
        status=session.MmiProjectionResultCategory.PROJECTION_BLOCKED,
        authority_effect=session.AUTHORITY_EFFECT_NONE,
        reason_codes=(
            "MMI_POLICY_SOURCE_BYTES_INVALID",
            "MMI_POLICY_SOURCE_BYTES_INVALID",
        ),
        projection=None,
    )
    with pytest.raises(session.H2cManualCaptureError) as captured:
        session._require_projection_build(
            documented,
            expected_type=session.MmiPolicyProjectionBuildResult,
            allowed_reason_prefixes=session._POLICY_REASON_PREFIXES,
        )
    assert captured.value.owner_reason_codes == documented.reason_codes
    unknown = session.MmiPolicyProjectionBuildResult(
        status=session.MmiProjectionResultCategory.PROJECTION_BLOCKED,
        authority_effect=session.AUTHORITY_EFFECT_NONE,
        reason_codes=("UNKNOWN",),
        projection=None,
    )
    with pytest.raises(RuntimeError, match="malformed MMI result reason codes"):
        session._require_projection_build(
            unknown,
            expected_type=session.MmiPolicyProjectionBuildResult,
            allowed_reason_prefixes=session._POLICY_REASON_PREFIXES,
        )
