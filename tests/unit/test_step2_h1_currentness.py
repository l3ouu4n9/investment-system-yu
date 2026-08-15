"""Integration-style tests for the report-only H1 currentness observer."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
import hashlib
import json
from pathlib import Path
from typing import Any

import pytest

import _mmi_hermetic_source_checkout as hermetic
import investment_orchestrator.common.io as io_mod
from investment_orchestrator.common.io import write_json
from investment_orchestrator.mmi.contracts import (
    AUTHORITY_EFFECT_NONE,
    MmiProjectionResultCategory,
    MmiSourceCaptureResult,
    MmiSourceRole,
)
from investment_orchestrator.mmi.long_horizon_research_payload_v2 import (
    read_mmi_long_horizon_research_payload_v2_from_captured_source,
)
from investment_orchestrator.workflow import (
    step1_research,
    step2_decision_builder,
    step2_h1_currentness,
)
from investment_orchestrator.workflow.step2_decision_builder import (
    H1Lh2InvocationAdmissionError,
)
from investment_orchestrator.workflow.step2_h1_currentness import (
    STEP2_H1_CURRENTNESS_OBSERVATION_SCHEMA_VERSION,
    evaluate_h1_currentness_workflow,
)
from investment_orchestrator.workflow.step2_h1_provenance import (
    H1_LH2_STRUCTURED_REPORT_PROMPT_CONTRACT_VERSION,
    STEP2_H1_CAPTURE_RECEIPT_SCHEMA_VERSION,
    STEP2_H1_RENDER_COMMITMENT_SCHEMA_VERSION,
)
from investment_orchestrator.workflow.step2_h1_report import (
    STEP2_H1_QUALITATIVE_REPORT_SCHEMA_VERSION,
    STEP2_H1_QUALITATIVE_RESPONSE_SCHEMA_VERSION,
)


CANONICAL_H1_BLOCKED_ACTIONS = (
    "SELL",
    "NEW_BUY",
    "ROTATION",
    "REBALANCE",
    "EXTENDED_ETF_ADMISSION",
    "ORDER_COMPILATION",
)


@dataclass(frozen=True)
class CurrentnessCase:
    root: Path
    artifact_dir: Path
    observation_path: Path
    report_path: Path
    prompt_sha256: str
    raw_sha256: str
    current_evidence_shas: tuple[str, ...]
    capture_calls: list[tuple[MmiSourceRole, str]]
    clock_calls: list[date]


def _h1_permission(*, blocked_actions: tuple[str, ...] = CANONICAL_H1_BLOCKED_ACTIONS) -> dict[str, Any]:
    return {
        "state": "H1_MAPPED_FRESH_NON_ACTIONABLE",
        "source": "H1_ROLE_MAPPED",
        "allowed_actions": ["HOLD", "NO_TRADE"],
        "blocked_actions": list(blocked_actions),
        "manual_review_required": False,
        "blocker_reasons": [],
    }


def _lh2_entry(*, published_at: date, suffix: str) -> dict[str, object]:
    return {
        "publisher": f"Publisher {suffix}",
        "published_at": published_at.isoformat(),
        "source_locator": f"operator/source-{suffix}.txt",
        "tickers": [f"ETF{suffix}"],
        "excerpt_text": f"Qualitative evidence excerpt {suffix}.",
    }


def _snapshot_files(root: Path) -> dict[str, bytes]:
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in root.rglob("*")
        if path.is_file()
    }


def _prepare_currentness_case(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    evaluation_date: date,
    published_dates: tuple[date, ...],
    blocked_actions: tuple[str, ...] = CANONICAL_H1_BLOCKED_ACTIONS,
    commitment_evidence_shas: tuple[str, ...] | None = None,
    reverse_commitment_evidence: bool = False,
) -> CurrentnessCase:
    monkeypatch.setattr(step1_research, "repo_root", lambda: tmp_path)
    monkeypatch.setattr(step2_decision_builder, "repo_root", lambda: tmp_path)
    monkeypatch.setattr(step2_h1_currentness, "repo_root", lambda: tmp_path)

    clock_calls: list[date] = []

    def system_date() -> date:
        clock_calls.append(evaluation_date)
        if len(clock_calls) != 1:
            raise AssertionError("currentness workflow read the system date more than once")
        return evaluation_date

    monkeypatch.setattr(step2_h1_currentness, "system_now_date", system_date)

    write_json(
        step1_research.step1_research_degraded_mode_decision_path(),
        _h1_permission(blocked_actions=blocked_actions),
    )

    lh2_raw = json.dumps(
        {
            "schema_version": "mmi_long_horizon_research_payload_v2",
            "sources": [
                _lh2_entry(published_at=published_at, suffix=str(index))
                for index, published_at in enumerate(published_dates)
            ],
        },
        ensure_ascii=False,
        indent=2,
    ).encode("utf-8")
    source = hermetic.capture_source(
        tmp_path,
        role=MmiSourceRole.LONG_HORIZON_RESEARCH,
        raw=lh2_raw,
    )
    payload = read_mmi_long_horizon_research_payload_v2_from_captured_source(source)
    current_evidence_shas = tuple(
        entry.source_entry_identity_sha256 for entry in payload.sources
    )
    observed_sha256 = hashlib.sha256(lh2_raw).hexdigest()
    write_json(
        tmp_path / "inputs" / "current" / "lh2_manual_capture_receipt.json",
        {
            "schema_version": "lh2_manual_capture_receipt_v1",
            "source_role": "LONG_HORIZON_RESEARCH",
            "observed_sha256": observed_sha256,
            "observed_size_bytes": len(lh2_raw),
        },
    )

    capture_calls: list[tuple[MmiSourceRole, str]] = []

    def capture_current(
        role: MmiSourceRole,
        *,
        expected_source_sha256: str,
    ) -> MmiSourceCaptureResult:
        assert role is MmiSourceRole.LONG_HORIZON_RESEARCH
        assert expected_source_sha256 == observed_sha256
        capture_calls.append((role, expected_source_sha256))
        return MmiSourceCaptureResult(
            status=MmiProjectionResultCategory.PROJECTION_VALID_COMPLETE,
            authority_effect=AUTHORITY_EFFECT_NONE,
            reason_codes=(),
            source=source,
        )

    monkeypatch.setattr(
        step2_decision_builder,
        "capture_current_mmi_source",
        capture_current,
    )

    if commitment_evidence_shas is None:
        commitment_evidence_shas = (
            tuple(reversed(current_evidence_shas))
            if reverse_commitment_evidence
            else current_evidence_shas
        )

    artifact_dir = step2_decision_builder.step2_artifact_dir()
    prompt_bytes = b"Structured qualitative H1 prompt.\n"
    prompt_sha256 = hashlib.sha256(prompt_bytes).hexdigest()
    step2_decision_builder.step2_prompt_path().write_bytes(prompt_bytes)
    write_json(
        step2_decision_builder.step2_render_commitment_path(),
        {
            "schema_version": STEP2_H1_RENDER_COMMITMENT_SCHEMA_VERSION,
            "prompt_contract_version": H1_LH2_STRUCTURED_REPORT_PROMPT_CONTRACT_VERSION,
            "rendered_prompt_sha256": prompt_sha256,
            "evidence_entry_identities_sha256": list(commitment_evidence_shas),
        },
    )

    response = {
        "schema_version": STEP2_H1_QUALITATIVE_RESPONSE_SCHEMA_VERSION,
        "long_horizon_opportunity": "Good opportunity.",
        "valuation_context": "Fairly valued.",
        "portfolio_contribution": "Diversification.",
        "evidence_integrity": "Evidence is solid.",
        "prior_thesis_change": "No change.",
        "evidence_references": list(commitment_evidence_shas),
    }
    raw_bytes = json.dumps(response, ensure_ascii=False).encode("utf-8")
    raw_sha256 = hashlib.sha256(raw_bytes).hexdigest()
    step2_decision_builder.step2_raw_output_path().write_bytes(raw_bytes)
    write_json(
        step2_decision_builder.step2_h1_capture_receipt_path(),
        {
            "schema_version": STEP2_H1_CAPTURE_RECEIPT_SCHEMA_VERSION,
            "rendered_prompt_sha256": prompt_sha256,
            "raw_response_sha256": raw_sha256,
        },
    )

    report_path = artifact_dir / "h1_qualitative_report.json"
    write_json(
        report_path,
        {
            "schema_version": STEP2_H1_QUALITATIVE_REPORT_SCHEMA_VERSION,
            "prompt_contract_version": H1_LH2_STRUCTURED_REPORT_PROMPT_CONTRACT_VERSION,
            "rendered_prompt_sha256": prompt_sha256,
            "raw_response_sha256": raw_sha256,
            "long_horizon_opportunity": response["long_horizon_opportunity"],
            "valuation_context": response["valuation_context"],
            "portfolio_contribution": response["portfolio_contribution"],
            "evidence_integrity": response["evidence_integrity"],
            "prior_thesis_change": response["prior_thesis_change"],
            "evidence_references": response["evidence_references"],
        },
    )

    return CurrentnessCase(
        root=tmp_path,
        artifact_dir=artifact_dir,
        observation_path=(
            artifact_dir / "h1_qualitative_currentness_observation.json"
        ),
        report_path=report_path,
        prompt_sha256=prompt_sha256,
        raw_sha256=raw_sha256,
        current_evidence_shas=current_evidence_shas,
        capture_calls=capture_calls,
        clock_calls=clock_calls,
    )


def _read_observation(case: CurrentnessCase) -> dict[str, Any]:
    observation = json.loads(case.observation_path.read_text(encoding="utf-8"))
    assert isinstance(observation, dict)
    return observation


def test_current_day_180_uses_one_date_one_payload_and_only_writes_observation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    evaluation_date = date(2026, 8, 15)
    case = _prepare_currentness_case(
        tmp_path,
        monkeypatch,
        evaluation_date=evaluation_date,
        published_dates=(evaluation_date - timedelta(days=180),),
    )
    before = _snapshot_files(tmp_path)

    evaluate_h1_currentness_workflow()

    observation = _read_observation(case)
    assert observation == {
        "schema_version": STEP2_H1_CURRENTNESS_OBSERVATION_SCHEMA_VERSION,
        "observed_on": evaluation_date.isoformat(),
        "is_current": True,
        "rendered_prompt_sha256": case.prompt_sha256,
        "raw_response_sha256": case.raw_sha256,
        "reason_code": None,
    }
    assert case.clock_calls == [evaluation_date]
    assert len(case.capture_calls) == 1
    after = _snapshot_files(tmp_path)
    assert set(after) - set(before) == {
        case.observation_path.relative_to(tmp_path).as_posix()
    }
    assert all(after[path] == content for path, content in before.items())


def test_real_shared_h1_prerequisite_rejects_incomplete_blocked_complement(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    evaluation_date = date(2026, 8, 15)
    case = _prepare_currentness_case(
        tmp_path,
        monkeypatch,
        evaluation_date=evaluation_date,
        published_dates=(evaluation_date,),
        blocked_actions=CANONICAL_H1_BLOCKED_ACTIONS[1:],
    )

    evaluate_h1_currentness_workflow()

    observation = _read_observation(case)
    assert observation["is_current"] is False
    assert observation["reason_code"] == "CURRENT_H1_PREREQUISITE_NOT_MET"
    assert case.clock_calls == [evaluation_date]
    assert len(case.capture_calls) == 1
    assert not step2_decision_builder.step2_blocked_by_research_gate_path().exists()


@pytest.mark.parametrize(
    ("ages", "expected_reason"),
    (
        ((181,), "CURRENT_LH2_STALE"),
        ((-1,), "CURRENT_LH2_FUTURE"),
        ((181, -1), "CURRENT_LH2_STALE"),
    ),
    ids=("day_181_stale", "future", "stale_precedes_future"),
)
def test_real_temporal_owner_classifies_stale_future_with_closed_precedence(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    ages: tuple[int, ...],
    expected_reason: str,
) -> None:
    evaluation_date = date(2026, 8, 15)
    case = _prepare_currentness_case(
        tmp_path,
        monkeypatch,
        evaluation_date=evaluation_date,
        published_dates=tuple(
            evaluation_date - timedelta(days=age) for age in ages
        ),
    )

    evaluate_h1_currentness_workflow()

    observation = _read_observation(case)
    assert observation["observed_on"] == evaluation_date.isoformat()
    assert observation["is_current"] is False
    assert observation["reason_code"] == expected_reason
    assert case.clock_calls == [evaluation_date]
    assert len(case.capture_calls) == 1


def test_current_evidence_universe_change_is_reachable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    evaluation_date = date(2026, 8, 15)
    case = _prepare_currentness_case(
        tmp_path,
        monkeypatch,
        evaluation_date=evaluation_date,
        published_dates=(evaluation_date,),
        commitment_evidence_shas=("f" * 64,),
    )

    evaluate_h1_currentness_workflow()

    observation = _read_observation(case)
    assert observation["is_current"] is False
    assert observation["reason_code"] == "CURRENT_EVIDENCE_UNIVERSE_CHANGED"
    assert len(case.capture_calls) == 1


def test_current_evidence_order_is_non_authoritative(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    evaluation_date = date(2026, 8, 15)
    case = _prepare_currentness_case(
        tmp_path,
        monkeypatch,
        evaluation_date=evaluation_date,
        published_dates=(evaluation_date, evaluation_date - timedelta(days=30)),
        reverse_commitment_evidence=True,
    )

    evaluate_h1_currentness_workflow()

    observation = _read_observation(case)
    assert observation["is_current"] is True
    assert observation["reason_code"] is None


def test_persisted_report_derivative_mutation_is_binding_mismatch(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    evaluation_date = date(2026, 8, 15)
    case = _prepare_currentness_case(
        tmp_path,
        monkeypatch,
        evaluation_date=evaluation_date,
        published_dates=(evaluation_date,),
    )
    report = json.loads(case.report_path.read_text(encoding="utf-8"))
    report["long_horizon_opportunity"] = "Manually changed report text."
    write_json(case.report_path, report)

    evaluate_h1_currentness_workflow()

    observation = _read_observation(case)
    assert observation["is_current"] is False
    assert observation["reason_code"] == "REPORT_BINDING_MISMATCH"
    assert case.capture_calls == []


def test_duplicate_decoded_report_key_is_contract_failure_and_preserves_observation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    evaluation_date = date(2026, 8, 15)
    case = _prepare_currentness_case(
        tmp_path,
        monkeypatch,
        evaluation_date=evaluation_date,
        published_dates=(evaluation_date,),
    )
    report = json.loads(case.report_path.read_text(encoding="utf-8"))
    members = [
        f'  "schema_version": {json.dumps(report["schema_version"])}',
        f'  "\\u0073chema_version": {json.dumps(report["schema_version"])}',
        *(
            f"  {json.dumps(key)}: {json.dumps(value)}"
            for key, value in report.items()
            if key != "schema_version"
        ),
    ]
    case.report_path.write_text("{\n" + ",\n".join(members) + "\n}\n", encoding="utf-8")
    prior_observation = b"PRIOR_OBSERVATION_BYTES\n"
    case.observation_path.write_bytes(prior_observation)

    with pytest.raises(ValueError, match="Duplicate JSON key rejected: 'schema_version'"):
        evaluate_h1_currentness_workflow()

    assert case.observation_path.read_bytes() == prior_observation
    assert case.capture_calls == []


def test_current_source_contract_failure_preserves_prior_observation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    evaluation_date = date(2026, 8, 15)
    case = _prepare_currentness_case(
        tmp_path,
        monkeypatch,
        evaluation_date=evaluation_date,
        published_dates=(evaluation_date,),
    )
    prior_observation = b"PRIOR_OBSERVATION_BYTES\n"
    case.observation_path.write_bytes(prior_observation)

    def blocked_capture(
        role: MmiSourceRole,
        *,
        expected_source_sha256: str,
    ) -> MmiSourceCaptureResult:
        return MmiSourceCaptureResult(
            status=MmiProjectionResultCategory.PROJECTION_BLOCKED,
            authority_effect=AUTHORITY_EFFECT_NONE,
            reason_codes=("MMI_SOURCE_EXPECTED_SHA256_MISMATCH",),
            source=None,
        )

    monkeypatch.setattr(
        step2_decision_builder,
        "capture_current_mmi_source",
        blocked_capture,
    )

    with pytest.raises(
        H1Lh2InvocationAdmissionError,
        match="MMI_SOURCE_EXPECTED_SHA256_MISMATCH",
    ):
        evaluate_h1_currentness_workflow()

    assert case.observation_path.read_bytes() == prior_observation


def test_atomic_observation_replace_failure_preserves_previous_bytes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    evaluation_date = date(2026, 8, 15)
    case = _prepare_currentness_case(
        tmp_path,
        monkeypatch,
        evaluation_date=evaluation_date,
        published_dates=(evaluation_date,),
    )
    prior_observation = b"PRIOR_OBSERVATION_BYTES\n"
    case.observation_path.write_bytes(prior_observation)

    def fail_replace(source: Any, destination: Any) -> None:
        raise OSError("simulated observation replace failure")

    monkeypatch.setattr(io_mod.os, "replace", fail_replace)

    with pytest.raises(OSError, match="simulated observation replace failure"):
        evaluate_h1_currentness_workflow()

    assert case.observation_path.read_bytes() == prior_observation
    assert not any(".tmp." in path.name for path in case.artifact_dir.iterdir())
