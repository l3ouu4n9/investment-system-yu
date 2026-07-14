"""S1A-0 extraction tests for the deterministic Step 1A compile bundle."""

from __future__ import annotations

import copy
import inspect
import hashlib
import json
from pathlib import Path
from typing import Any

import pytest

from investment_orchestrator.research.actionable_promotion_eligibility import (
    evaluate_actionable_handoff_promotion_eligibility,
)
from investment_orchestrator.research.actionable_handoff_candidate import (
    build_actionable_handoff_candidate,
    build_actionable_handoff_metadata,
)
from investment_orchestrator.research.actionable_handoff_preview import (
    build_actionable_handoff_preview,
)
from investment_orchestrator.research.approval_registry_switch_readiness import SWITCH_TARGET_FAIL_CLOSED
from investment_orchestrator.research.approvals_inclusive_active_registry import (
    CAPTURE_INVALID,
    CapturedResearchAnchorApprovalSource,
    build_active_research_anchor_registry_with_approvals,
    capture_research_anchor_approval_source_text,
)
from investment_orchestrator.research.research_anchor_approval_manifest import (
    compute_operator_completed_anchor_sha256 as sha,
)
from investment_orchestrator.research.support_signals import (
    build_compiled_support_signals,
)
from investment_orchestrator.state.last_good_research_handoff import (
    decision_relevant_settings,
    strategy_settings_hash,
)
from investment_orchestrator.validators.validate_research_handoff import (
    research_handoff_validation_result_to_dict,
    validate_research_handoff,
)
from investment_orchestrator.workflow import step1_research
from investment_orchestrator.workflow.step1a_grounding_compile import (
    SCHEMA_VERSION,
    _workflow_approval_source_identity_mismatch,
    build_step1a_grounding_compile_bundle,
)


FIXTURE = (
    Path(__file__).resolve().parents[1]
    / "fixtures"
    / "step1_contract_failures"
    / "current_step1_raw_output_minimal.txt"
)

APPROVAL_SOURCE_ID = "operator_research_anchor_approvals_yaml"
REVOCATION_SOURCE_ID = "operator_research_anchor_revocations_yaml"


def _settings() -> dict[str, Any]:
    return {
        "as_of": "2026-06-28",
        "benchmark": "QQQ",
        "core_universe": ["QQQ", "VOO", "VTI", "VT"],
        "satellite_universe": ["SMH", "IGV"],
        "user_approved_extended_etf_static_list": ["GRID", "CIBR"],
        "hard_cap_open_orders_budget": 38211.29,
        "target_new_buy_budget_this_run": 12000.00,
        "ticker_role_fallback": {
            "QQQ": "benchmark_carrier_core",
            "VOO": "diversified_core_buffer",
            "VTI": "diversified_core_buffer",
            "VT": "diversified_core_buffer",
            "SMH": "sector_alpha_tilt",
            "IGV": "sector_alpha_tilt",
        },
    }


def _settings_with_cap() -> dict[str, Any]:
    settings = _settings()
    settings["max_new_tickers_per_week"] = {
        "base_universe_new_tickers_per_week": 2,
        "extended_etf_sleeve_new_tickers_per_week": 2,
    }
    return settings


def _anchor(anchor_id: str = "AI_CAPEX_2026H2", ticker: str = "QQQ") -> dict[str, Any]:
    return {
        "anchor_id": anchor_id,
        "anchor_type": "structural_theme",
        "applicable_tickers": [ticker],
        "anchor_date_et": "2026-06-15",
        "valid_from": "2026-06-01",
        "valid_until": "2026-07-31",
        "source_type": "operator",
        "confidence_floor": "medium",
        "summary": "Operator-dated thesis grounding.",
    }


def _approval(anchor: dict[str, Any] | None = None, approval_id: str = "APR-1") -> dict[str, Any]:
    a = anchor or _anchor()
    return {
        "approval_id": approval_id,
        "decision": "approve",
        "operator_completed_anchor": a,
        "operator_completed_anchor_sha256": sha(a),
        "approved_by": "operator",
    }


def _revocation(
    anchor: dict[str, Any] | None = None,
    *,
    approval_id: str = "APR-1",
    anchor_id: str | None = None,
) -> dict[str, Any]:
    a = anchor or _anchor()
    return {
        "revocation_id": "REV-1",
        "target_type": "approval_anchor",
        "approval_id": approval_id,
        "anchor_id": anchor_id or a["anchor_id"],
        "operator_completed_anchor_sha256": sha(a),
        "effective_as_of": "2026-06-28",
        "reason": "Thesis invalidated.",
        "revoked_by": "operator",
    }


def _approval_grounded_memo() -> dict[str, Any]:
    return {
        "schema_version": "analyst_memo_v1",
        "is_llm_generated": True,
        "as_of_date": "2026-06-28",
        "regime_view": "constructive",
        "confidence": "high",
        "ticker_relative_view": [
            {
                "ticker": "QQQ",
                "stance": "prefer",
                "rationale_12m_plus": "Approval-only grounding probe.",
                "anchor_id_refs": ["AI_CAPEX_2026H2"],
            }
        ],
        "avoid_or_deprioritize": [],
        "data_gaps": [],
        "source_notes": [
            {"claim": "AI capex", "source": "10-K", "source_quality": "official"}
        ],
    }


def _write_json(path: Path, payload: Any) -> None:
    path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")


def _setup_repo(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch | None = None,
    *,
    anchors: list[dict[str, Any]] | None = None,
    approvals_payload: Any = "default",
) -> dict[str, Path]:
    if monkeypatch is not None:
        monkeypatch.setattr(step1_research, "repo_root", lambda: tmp_path)
    artifact_dir = tmp_path / "artifacts" / "current" / "step1_research"
    artifact_dir.mkdir(parents=True)
    (artifact_dir / "raw_output.txt").write_text(FIXTURE.read_text(encoding="utf-8"), encoding="utf-8")

    inputs = tmp_path / "inputs" / "current"
    inputs.mkdir(parents=True)
    _write_json(
        inputs / "research_anchors.yaml",
        {
            "schema_version": "research_anchors_v1",
            "is_llm_generated": False,
            "as_of_date": "2026-06-28",
            "anchors": anchors if anchors is not None else [_anchor("BASE_QQQ", "QQQ")],
        },
    )
    if approvals_payload == "default":
        approvals_payload = {
            "schema_version": "research_anchor_approvals_v1",
            "is_llm_generated": False,
            "as_of_date": "2026-06-28",
            "approvals": [_approval()],
            "revocations": [],
        }
    if approvals_payload is not None:
        if isinstance(approvals_payload, str):
            (inputs / "research_anchor_approvals.yaml").write_text(approvals_payload, encoding="utf-8")
        else:
            _write_json(inputs / "research_anchor_approvals.yaml", approvals_payload)
    return {"artifact_dir": artifact_dir, "inputs": inputs, "state": tmp_path / "artifacts" / "state"}


def _bundle(
    tmp_path: Path,
    *,
    optional_candidates: dict[str, Any] | None = None,
    optional_support: dict[str, Any] | None = None,
    captured_approval_source: Any = None,
) -> dict[str, Any]:
    inputs = tmp_path / "inputs" / "current"
    artifact_dir = tmp_path / "artifacts" / "current" / "step1_research"
    return build_step1a_grounding_compile_bundle(
        strategy_settings=_settings(),
        research_anchors_path=inputs / "research_anchors.yaml",
        research_anchor_approvals_path=inputs / "research_anchor_approvals.yaml",
        portfolio_snapshot_text=None,
        portfolio_snapshot_path=inputs / "portfolio_snapshot.txt",
        last_good_available=False,
        last_good_metadata={},
        strategy_settings_path=inputs / "strategy_settings.yaml",
        last_good_metadata_path=tmp_path / "artifacts" / "state" / "last_good_research_handoff_metadata.json",
        active_registry_artifact_path=artifact_dir / "active_research_anchor_registry.json",
        approvals_registry_artifact_path=artifact_dir / "active_research_anchor_registry_with_approvals.json",
        optional_research_anchor_candidates=optional_candidates,
        optional_compiled_support_signals=optional_support,
        captured_approval_source=captured_approval_source,
    )


def _read(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _strip_generated_at(value: Any) -> Any:
    if isinstance(value, dict):
        return {k: _strip_generated_at(v) for k, v in value.items() if k != "generated_at"}
    if isinstance(value, list):
        return [_strip_generated_at(item) for item in value]
    return value


def _active_ids(registry: dict[str, Any]) -> list[str]:
    return sorted(row["anchor_id"] for row in registry.get("active_anchors", []))


def _approval_sourced_active_count(registry: dict[str, Any]) -> int:
    return sum(
        1
        for row in registry.get("active_anchors", [])
        if row.get("source_id") == "operator_research_anchor_approvals_yaml"
    )


def _production_approval_derived_selected_row(
    baseline_registry: dict[str, Any],
) -> dict[str, Any]:
    """Build a complete production-shaped approval row for a hostile candidate."""
    settings = _settings()
    candidate_registry = build_active_research_anchor_registry_with_approvals(
        baseline=baseline_registry,
        approval_source_text=json.dumps(
            {
                "schema_version": "research_anchor_approvals_v1",
                "is_llm_generated": False,
                "as_of_date": settings["as_of"],
                "approvals": [_approval()],
                "revocations": [],
            }
        ),
        approval_source_path="in-memory-selected-registry-contradiction",
        allowed_universe=(
            settings["core_universe"]
            + settings["satellite_universe"]
            + settings["user_approved_extended_etf_static_list"]
        ),
        today=settings["as_of"],
    )
    rows = [
        row
        for row in candidate_registry["active_anchors"]
        if row.get("source_id") == APPROVAL_SOURCE_ID
    ]
    assert len(rows) == 1
    return copy.deepcopy(rows[0])


def _assert_no_approval_grounding_or_promotion(artifacts: dict[str, Any]) -> None:
    """Prove an unsafe approval source cannot escape through downstream views."""
    packet = artifacts["evidence_packet"]
    memo = {
        "schema_version": "analyst_memo_v1",
        "is_llm_generated": True,
        "confidence": "high",
        "ticker_relative_view": [
            {
                "ticker": "QQQ",
                "stance": "prefer",
                "rationale_12m_plus": "Approval-only grounding probe.",
                "anchor_id_refs": ["AI_CAPEX_2026H2"],
            }
        ],
        "avoid_or_deprioritize": [],
        "data_gaps": [],
        "source_notes": [{"claim": "probe", "source": "operator"}],
    }
    signals = build_compiled_support_signals(
        evidence_packet=packet,
        analyst_memo=memo,
        compilation_mode="evidence_plus_memo",
    )
    promotion = evaluate_actionable_handoff_promotion_eligibility(
        evidence_packet=packet,
        compiled_support_signals=signals,
        actionable_preview=None,
        actionable_candidate=None,
        actionable_candidate_validation=None,
        actionable_candidate_metadata=None,
        today=_settings()["as_of"],
    )

    assert signals["accepted_support_signals"] == []
    assert promotion["eligible_for_promotion"] is False


def _promotion_chain_for_packet(
    packet: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Build the real actionable chain so identity rejection is the only delta."""
    settings = _settings_with_cap()
    memo = _approval_grounded_memo()
    active_registry = packet["active_anchor_registry"]
    active_rows = active_registry.get("active_anchors", [])
    evaluated_anchors: list[dict[str, Any]] = []
    for row in active_rows:
        assert isinstance(row, dict)
        validation = row.get("validation")
        validation = validation if isinstance(validation, dict) else {}
        evaluated_anchors.append(
            {
                key: row.get(key)
                for key in (
                    "anchor_id",
                    "anchor_type",
                    "applicable_tickers",
                    "anchor_date_et",
                    "valid_from",
                    "valid_until",
                    "source_type",
                    "confidence_floor",
                    "summary",
                    "blocks_if_stale",
                )
            }
            | {
                "valid": validation.get("valid") is True,
                "stale": validation.get("stale") is True,
                "usable": validation.get("usable") is True,
                "problems": list(validation.get("problems") or []),
            }
        )
    promotion_packet = {
        "schema_version": "evidence_packet_v1",
        "is_llm_generated": False,
        "report_only": True,
        "strategy_settings_hash": strategy_settings_hash(
            decision_relevant_settings(settings)
        ),
        "universe": {
            "core_universe": ["QQQ", "VOO", "VTI", "VT"],
            "satellite_universe": ["SMH", "IGV"],
            "approved_extended_etf": ["GRID", "CIBR"],
            "allowed_buy_tickers": ["QQQ", "VOO", "VTI", "VT", "SMH", "IGV"],
        },
        "budget_settings": {
            "hard_cap_open_orders_budget": settings["hard_cap_open_orders_budget"],
            "target_new_buy_budget_this_run": settings[
                "target_new_buy_budget_this_run"
            ],
            "max_new_tickers_per_week": settings["max_new_tickers_per_week"],
        },
        "research_anchors": {
            "available": True,
            "valid": True,
            "valid_anchor_count": len(evaluated_anchors),
            "stale_anchor_count": 0,
            "invalid_anchor_count": 0,
            "anchors": evaluated_anchors,
            "errors": [],
        },
        "active_anchor_registry": active_registry,
        "data_gaps": [],
    }
    signals = build_compiled_support_signals(
        evidence_packet=promotion_packet,
        analyst_memo=memo,
        compilation_mode="evidence_plus_memo",
    )
    preview = build_actionable_handoff_preview(
        evidence_packet=promotion_packet,
        analyst_memo=memo,
        compiled_support_signals=signals,
    )
    candidate = build_actionable_handoff_candidate(
        evidence_packet=promotion_packet,
        analyst_memo=memo,
        actionable_handoff_preview=preview,
        base_candidate=None,
        strategy_settings=settings,
    )
    validation = research_handoff_validation_result_to_dict(
        validate_research_handoff(candidate, strategy_settings=settings)
    )
    metadata = build_actionable_handoff_metadata(
        candidate=candidate,
        validation=validation,
        actionable_handoff_preview=preview,
        compiled_support_signals=signals,
        evidence_packet=promotion_packet,
        base_candidate=None,
        used_active_compiled_handoff_as_base=False,
    )
    promotion = evaluate_actionable_handoff_promotion_eligibility(
        evidence_packet=promotion_packet,
        compiled_support_signals=signals,
        actionable_preview=preview,
        actionable_candidate=candidate,
        actionable_candidate_validation=validation,
        actionable_candidate_metadata=metadata,
        strategy_settings=settings,
        today=settings["as_of"],
    )
    return signals, promotion


def test_builder_returns_json_serializable_unwired_bundle(tmp_path: Path) -> None:
    paths = _setup_repo(tmp_path)
    before = sorted(p.relative_to(tmp_path) for p in tmp_path.rglob("*") if p.is_file())

    bundle = _bundle(tmp_path)

    after = sorted(p.relative_to(tmp_path) for p in tmp_path.rglob("*") if p.is_file())
    assert after == before
    assert json.loads(json.dumps(bundle))["schema_version"] == SCHEMA_VERSION
    assert bundle["is_llm_generated"] is False
    assert bundle["extraction_only"] is True
    assert bundle["not_wired_to_production"] is True
    assert bundle["permission_effect"] == "none"
    assert bundle["not_authorization"] is True
    assert bundle["not_execution_authorization"] is True
    assert bundle["consumed_by_gates"] is False
    assert bundle["consumed_by_order_path"] is False
    assert bundle["cannot_affect_allowed_actions"] is True
    assert bundle["diagnostics"]["files_written"] == []
    assert paths["artifact_dir"].is_dir()


def test_builder_semantic_parity_with_current_step1_deterministic_artifacts(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paths = _setup_repo(tmp_path, monkeypatch)
    result = step1_research.parse_step1_output(strategy_settings=_settings())
    optional_candidates = _read(paths["artifact_dir"] / "research_anchor_candidates.json")
    optional_support = _read(Path(result["compiled_support_signals_path"]))

    bundle = _bundle(tmp_path, optional_candidates=optional_candidates, optional_support=optional_support)
    artifacts = bundle["artifacts"]

    assert artifacts["active_research_anchor_registry"] == _read(
        paths["artifact_dir"] / "active_research_anchor_registry.json"
    )
    assert artifacts["research_anchor_approvals_validation"] == _read(
        paths["artifact_dir"] / "research_anchor_approvals_validation.json"
    )
    assert artifacts["research_anchor_revocations_validation"] == _read(
        paths["artifact_dir"] / "research_anchor_revocations_validation.json"
    )
    assert artifacts["active_research_anchor_registry_with_approvals"] == _read(
        paths["artifact_dir"] / "active_research_anchor_registry_with_approvals.json"
    )
    assert artifacts["approval_registry_dual_read_diff"] == _read(
        paths["artifact_dir"] / "approval_registry_dual_read_diff.json"
    )
    assert artifacts["approval_registry_switch_readiness"] == _read(
        paths["artifact_dir"] / "approval_registry_switch_readiness.json"
    )
    assert _strip_generated_at(artifacts["evidence_packet"]) == _strip_generated_at(
        _read(paths["artifact_dir"] / "evidence_packet.json")
    )

    current_observatory = _read(paths["artifact_dir"] / "grounding_status_observatory.json")
    new_observatory = artifacts["grounding_status_observatory"]
    assert new_observatory["schema_version"] == current_observatory["schema_version"]
    assert new_observatory["selected_registry"]["active_anchor_count"] == current_observatory["selected_registry"][
        "active_anchor_count"
    ]
    assert new_observatory["candidates_summary"]["candidate_count"] == current_observatory["candidates_summary"][
        "candidate_count"
    ]
    assert new_observatory["support_grounding_summary"]["grounded_claim_count"] == current_observatory[
        "support_grounding_summary"
    ]["grounded_claim_count"]


def test_missing_approvals_source_produces_diagnostics_without_authority(tmp_path: Path) -> None:
    _setup_repo(tmp_path, approvals_payload=None)
    bundle = _bundle(tmp_path)
    artifacts = bundle["artifacts"]

    assert bundle["source_summary"]["research_anchor_approvals_source_state"] == "absent"
    assert artifacts["research_anchor_approvals_validation"]["source_present"] is False
    assert artifacts["research_anchor_revocations_validation"]["source_present"] is False
    assert _approval_sourced_active_count(artifacts["active_research_anchor_registry_with_approvals"]) == 0
    assert artifacts["approval_registry_dual_read_diff"]["added_by_approvals"] == []
    assert artifacts["approval_registry_switch_readiness"]["ready"] is True
    assert artifacts["approval_registry_switch_readiness"][
        "switch_target"
    ] == "approvals_inclusive"
    assert bundle["diagnostics"]["workflow_approval_source_identity_mismatch"] is False
    assert bundle["diagnostics"]["diagnostics_incomplete"] is False
    assert bundle["cannot_affect_allowed_actions"] is True
    assert bundle["consumed_by_order_path"] is False


def test_step1a_absent_source_with_approval_support_claim_fails_closed(
    tmp_path: Path,
) -> None:
    _setup_repo(tmp_path, approvals_payload=None)
    bundle = _bundle(
        tmp_path,
        optional_support={
            "accepted_support_signals": [
                {
                    "anchor_id": "APPROVED_A",
                    "approval_type": "operator_authored",
                }
            ]
        },
    )
    artifacts = bundle["artifacts"]

    assert bundle["diagnostics"]["workflow_approval_source_identity_mismatch"] is True
    assert bundle["diagnostics"]["diagnostics_incomplete"] is True
    assert artifacts["active_research_anchor_registry_with_approvals"]["counts"][
        "approved_active"
    ] == 0
    assert artifacts["approval_registry_dual_read_diff"]["added_by_approvals"] == []
    assert artifacts["approval_registry_switch_readiness"]["ready"] is False


@pytest.mark.parametrize("content", ["", "   ", "\n"])
def test_present_blank_approval_source_fails_bundle_closed(
    tmp_path: Path,
    content: str,
) -> None:
    _setup_repo(tmp_path, approvals_payload=content)
    bundle = _bundle(tmp_path)
    artifacts = bundle["artifacts"]

    assert bundle["source_summary"]["research_anchor_approvals_source_state"] == "present"
    assert artifacts["research_anchor_approvals_validation"]["source_present"] is True
    assert artifacts["research_anchor_approvals_validation"]["source_valid"] is False
    assert artifacts["research_anchor_revocations_validation"]["source_present"] is True
    overlay = artifacts["active_research_anchor_registry_with_approvals"]
    assert overlay["registry_valid"] is False
    assert overlay["counts"]["approved_active"] == 0
    _assert_no_approval_grounding_or_promotion(artifacts)


def test_approval_source_read_error_fails_bundle_closed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paths = _setup_repo(tmp_path)
    source_path = paths["inputs"] / "research_anchor_approvals.yaml"
    original_read_text = Path.read_text

    def failed_read(path: Path, *args: Any, **kwargs: Any) -> str:
        if path == source_path:
            raise PermissionError("injected")
        return original_read_text(path, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", failed_read)
    bundle = _bundle(tmp_path)
    artifacts = bundle["artifacts"]

    assert bundle["source_summary"]["research_anchor_approvals_source_state"] == "read_error"
    approval_validation = artifacts["research_anchor_approvals_validation"]
    assert approval_validation["source_present"] is True
    assert approval_validation["source_valid"] is False
    overlay = artifacts["active_research_anchor_registry_with_approvals"]
    assert overlay["registry_valid"] is False
    assert overlay["counts"]["approved_active"] == 0
    _assert_no_approval_grounding_or_promotion(artifacts)


def test_forged_exact_capture_cannot_supply_step1a_source_summary_identity(
    tmp_path: Path,
) -> None:
    paths = _setup_repo(tmp_path)
    source_path = paths["inputs"] / "research_anchor_approvals.yaml"
    captured = capture_research_anchor_approval_source_text(
        source_path.read_text(encoding="utf-8"),
        source_path=str(source_path),
    )
    forged = object.__new__(CapturedResearchAnchorApprovalSource)
    for field in (
        "source_state",
        "source_path",
        "source_bytes",
        "source_text",
        "source_sha256",
        "read_error",
    ):
        object.__setattr__(forged, field, getattr(captured, field))
    object.__setattr__(forged, "read_error", "approval_source_read_error")

    bundle = _bundle(tmp_path, captured_approval_source=forged)
    artifacts = bundle["artifacts"]

    assert bundle["source_summary"]["research_anchor_approvals_source_state"] == "read_error"
    assert bundle["source_summary"]["research_anchor_approvals_source_sha256"] is None
    assert bundle["diagnostics"]["diagnostics_incomplete"] is True
    approval_validation = artifacts["research_anchor_approvals_validation"]
    assert approval_validation["source_valid"] is False
    assert CAPTURE_INVALID in json.dumps(approval_validation["manifest_errors"])
    overlay = artifacts["active_research_anchor_registry_with_approvals"]
    assert overlay["registry_valid"] is False
    assert overlay["counts"]["approved_active"] == 0
    assert artifacts["approval_registry_dual_read_diff"]["added_by_approvals"] == []
    assert artifacts["approval_registry_switch_readiness"]["ready"] is False
    _assert_no_approval_grounding_or_promotion(artifacts)


def test_stateful_capture_subclass_cannot_rebind_step1a_workflow_identity(
    tmp_path: Path,
) -> None:
    _setup_repo(tmp_path)
    first_text = json.dumps(
        {
            "schema_version": "research_anchor_approvals_v1",
            "is_llm_generated": False,
            "as_of_date": "2026-06-28",
            "approvals": [],
            "revocations": [],
        },
        sort_keys=True,
    )
    second_text = json.dumps(
        {
            "schema_version": "research_anchor_approvals_v1",
            "is_llm_generated": False,
            "as_of_date": "2026-06-28",
            "approvals": [_approval()],
            "revocations": [],
        },
        sort_keys=True,
    )

    class ChangingCapture(CapturedResearchAnchorApprovalSource):
        __slots__ = ("reads",)

        def __getattribute__(self, name: str) -> Any:
            if name in {"source_text", "source_sha256"}:
                reads = object.__getattribute__(self, "reads") + 1
                object.__setattr__(self, "reads", reads)
                text = first_text if reads <= 2 else second_text
                return (
                    text
                    if name == "source_text"
                    else hashlib.sha256(text.encode("utf-8")).hexdigest()
                )
            return object.__getattribute__(self, name)

    source = object.__new__(ChangingCapture)
    object.__setattr__(source, "reads", 0)

    bundle = _bundle(tmp_path, captured_approval_source=source)
    artifacts = bundle["artifacts"]

    assert source.reads == 0
    assert bundle["source_summary"]["research_anchor_approvals_source_state"] == "read_error"
    assert bundle["source_summary"]["research_anchor_approvals_source_sha256"] is None
    assert bundle["diagnostics"]["diagnostics_incomplete"] is True
    assert artifacts["active_research_anchor_registry_with_approvals"]["counts"][
        "approved_active"
    ] == 0
    assert artifacts["approval_registry_dual_read_diff"]["added_by_approvals"] == []
    assert artifacts["approval_registry_switch_readiness"]["ready"] is False
    _assert_no_approval_grounding_or_promotion(artifacts)


def test_malformed_approvals_source_produces_validation_diagnostics(tmp_path: Path) -> None:
    _setup_repo(tmp_path, approvals_payload="approvals: [unterminated\n : :\n")
    artifacts = _bundle(tmp_path)["artifacts"]

    assert artifacts["research_anchor_approvals_validation"]["source_valid"] is False
    assert artifacts["research_anchor_revocations_validation"]["source_valid"] is False
    assert _approval_sourced_active_count(artifacts["active_research_anchor_registry_with_approvals"]) == 0
    _assert_no_approval_grounding_or_promotion(artifacts)


@pytest.mark.parametrize("location", ["top", "approval"])
def test_unknown_combined_source_field_fails_complete_bundle_closed(
    tmp_path: Path,
    location: str,
) -> None:
    manifest = {
        "schema_version": "research_anchor_approvals_v1",
        "is_llm_generated": False,
        "as_of_date": "2026-06-28",
        "approvals": [_approval()],
        "revocations": [],
    }
    if location == "top":
        manifest["unknown_top_level"] = "x"
    else:
        manifest["approvals"][0]["unknown_approval_field"] = "x"
    _setup_repo(tmp_path, approvals_payload=manifest)
    artifacts = _bundle(tmp_path)["artifacts"]

    assert artifacts["research_anchor_approvals_validation"]["source_valid"] is False
    overlay = artifacts["active_research_anchor_registry_with_approvals"]
    assert overlay["registry_valid"] is False
    assert overlay["counts"]["approved_active"] == 0
    assert artifacts["approval_registry_switch_readiness"]["ready"] is False
    assert _approval_sourced_active_count(
        artifacts["evidence_packet"]["active_anchor_registry"]
    ) == 0
    _assert_no_approval_grounding_or_promotion(artifacts)


def test_complete_step1a_bundle_uses_one_immutable_approval_snapshot(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paths = _setup_repo(tmp_path)
    source_path = paths["inputs"] / "research_anchor_approvals.yaml"
    source_a_text = source_path.read_text(encoding="utf-8")
    source_a = source_a_text.encode("utf-8")
    source_b_doc = json.loads(source_a_text)
    source_b_doc["revocations"] = [_revocation()]
    source_b = json.dumps(source_b_doc, indent=2, sort_keys=True)
    original_read_text = Path.read_text
    reads = 0

    def alternating_read(path: Path, *args: Any, **kwargs: Any) -> str:
        nonlocal reads
        if path == source_path:
            reads += 1
            return source_a_text if reads % 2 else source_b
        return original_read_text(path, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", alternating_read)
    bundle = _bundle(tmp_path)
    artifacts = bundle["artifacts"]

    assert reads == 1
    expected_sha = hashlib.sha256(source_a).hexdigest()
    assert artifacts["research_anchor_approvals_validation"]["source_sha256"] == expected_sha
    assert artifacts["research_anchor_revocations_validation"]["source_sha256"] == expected_sha
    overlay = artifacts["active_research_anchor_registry_with_approvals"]
    overlay_sources = {
        entry["source_id"]: entry["sha256"] for entry in overlay["source_manifest"]
    }
    assert overlay_sources["operator_research_anchor_approvals_yaml"] == expected_sha
    assert overlay_sources["operator_research_anchor_revocations_yaml"] == expected_sha
    assert artifacts["approval_registry_dual_read_diff"]["approval_source_sha256"] == expected_sha
    assert artifacts["approval_registry_switch_readiness"]["source_hashes"][
        "research_anchor_approvals_yaml"
    ]["approvals_source_manifest"] == expected_sha
    assert overlay["counts"]["approved_active"] == 1
    embedded = artifacts["evidence_packet"]["active_anchor_registry"]
    embedded_sources = {
        entry["source_id"]: entry["sha256"] for entry in embedded["source_manifest"]
    }
    assert embedded_sources["operator_research_anchor_approvals_yaml"] == expected_sha
    assert artifacts["grounding_status_observatory"]["blockers"] == []


@pytest.mark.parametrize("source_a_revoked", [False, True])
def test_complete_step1a_bundle_sanitizes_public_capture_once_and_never_rereads_it(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    source_a_revoked: bool,
) -> None:
    import investment_orchestrator.research.approvals_inclusive_active_registry as approvals_module
    import investment_orchestrator.research.evidence_packet as evidence_module
    import investment_orchestrator.workflow.step1a_grounding_compile as step1a_module

    _setup_repo(tmp_path)
    source_a_document = {
        "schema_version": "research_anchor_approvals_v1",
        "is_llm_generated": False,
        "as_of_date": "2026-06-28",
        "approvals": [_approval()],
        "revocations": [_revocation()] if source_a_revoked else [],
    }
    source_b_document = {
        **source_a_document,
        "revocations": [] if source_a_revoked else [_revocation()],
    }
    source_a_text = json.dumps(source_a_document, sort_keys=True)
    source_b_text = json.dumps(source_b_document, sort_keys=True)
    public_source = capture_research_anchor_approval_source_text(
        source_a_text,
        source_path="inputs/current/research_anchor_approvals.yaml",
    )
    source_a_sha = hashlib.sha256(source_a_text.encode("utf-8")).hexdigest()
    source_b_bytes = source_b_text.encode("utf-8")
    source_b_sha = hashlib.sha256(source_b_bytes).hexdigest()

    original_sanitizer = approvals_module._sanitize_captured_source
    sanitizations = 0

    def sanitize_once_then_mutate(source: Any) -> Any:
        nonlocal sanitizations
        sanitizations += 1
        snapshot = original_sanitizer(source)
        if type(source) is CapturedResearchAnchorApprovalSource:
            object.__setattr__(source, "source_bytes", source_b_bytes)
            object.__setattr__(source, "source_text", source_b_text)
            object.__setattr__(source, "source_sha256", source_b_sha)
        return snapshot

    # Patch every module-level alias used by the complete workflow. Any public
    # wrapper accidentally re-entered after ownership transfer increments the
    # same counter and makes this regression fail.
    for module in (approvals_module, evidence_module, step1a_module):
        monkeypatch.setattr(module, "_sanitize_captured_source", sanitize_once_then_mutate)

    bundle = _bundle(tmp_path, captured_approval_source=public_source)
    artifacts = bundle["artifacts"]

    assert sanitizations == 1
    assert public_source.source_sha256 == source_b_sha
    assert bundle["source_summary"]["research_anchor_approvals_source_sha256"] == source_a_sha
    assert bundle["diagnostics"]["workflow_approval_source_identity_mismatch"] is False
    assert artifacts["research_anchor_approvals_validation"]["source_sha256"] == source_a_sha
    assert artifacts["research_anchor_revocations_validation"]["source_sha256"] == source_a_sha
    overlay = artifacts["active_research_anchor_registry_with_approvals"]
    assert {
        row["source_id"]: row["sha256"]
        for row in overlay["source_manifest"]
        if row["source_id"].startswith("operator_research_anchor_")
    } == {
        "operator_research_anchor_approvals_yaml": source_a_sha,
        "operator_research_anchor_revocations_yaml": source_a_sha,
    }
    assert overlay["counts"]["approved_active"] == (0 if source_a_revoked else 1)
    assert artifacts["approval_registry_dual_read_diff"]["approval_source_sha256"] == source_a_sha
    assert artifacts["approval_registry_switch_readiness"]["source_hashes"][
        "research_anchor_approvals_yaml"
    ]["approvals_source_manifest"] == source_a_sha
    embedded = artifacts["embedded_active_anchor_registry_selection"]
    assert embedded["dual_read_diff"]["approval_source_sha256"] == source_a_sha
    assert next(
        row["sha256"]
        for row in embedded["approvals_registry"]["source_manifest"]
        if row["source_id"] == "operator_research_anchor_approvals_yaml"
    ) == source_a_sha
    assert next(
        row["sha256"]
        for row in artifacts["evidence_packet"]["active_anchor_registry"]["source_manifest"]
        if row["source_id"] == "operator_research_anchor_approvals_yaml"
    ) == source_a_sha


def test_complete_step1a_bundle_rejects_mismatched_derived_source_identity_before_selection(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import investment_orchestrator.workflow.step1a_grounding_compile as step1a_module

    _setup_repo(tmp_path)
    original = (
        step1a_module._build_step1a_research_anchor_approvals_validation_from_sanitized_source
    )

    def mismatched_validation(**kwargs: Any) -> dict[str, Any]:
        result = original(**kwargs)
        return {**result, "source_sha256": "f" * 64}

    monkeypatch.setattr(
        step1a_module,
        "_build_step1a_research_anchor_approvals_validation_from_sanitized_source",
        mismatched_validation,
    )

    bundle = _bundle(tmp_path)
    artifacts = bundle["artifacts"]
    overlay = artifacts["active_research_anchor_registry_with_approvals"]

    assert bundle["diagnostics"]["workflow_approval_source_identity_mismatch"] is True
    assert bundle["diagnostics"]["diagnostics_incomplete"] is True
    assert overlay["registry_valid"] is False
    assert overlay["counts"]["approved_active"] == 0
    assert "workflow_approval_source_identity_mismatch" in overlay["registry_blockers"]
    assert artifacts["approval_registry_dual_read_diff"]["added_by_approvals"] == []
    assert artifacts["approval_registry_switch_readiness"]["ready"] is False
    assert artifacts["embedded_active_anchor_registry_selection"]["selected_source"] != (
        "approvals_inclusive"
    )
    assert _approval_sourced_active_count(
        artifacts["evidence_packet"]["active_anchor_registry"]
    ) == 0
    assert "workflow_approval_source_identity_mismatch" in artifacts[
        "grounding_status_observatory"
    ]["blockers"]
    _assert_no_approval_grounding_or_promotion(artifacts)


def test_complete_step1_parse_reads_combined_source_once(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paths = _setup_repo(tmp_path, monkeypatch)
    source_path = paths["inputs"] / "research_anchor_approvals.yaml"
    source_text = source_path.read_text(encoding="utf-8")
    source_bytes = source_text.encode("utf-8")
    alternate_document = json.loads(source_text)
    alternate_document["revocations"] = [_revocation()]
    alternate_text = json.dumps(alternate_document, indent=2, sort_keys=True)
    original_read_text = Path.read_text
    reads = 0

    def counted_read(path: Path, *args: Any, **kwargs: Any) -> str:
        nonlocal reads
        if path == source_path:
            reads += 1
            return source_text if reads % 2 else alternate_text
        return original_read_text(path, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", counted_read)
    step1_research.parse_step1_output(strategy_settings=_settings())

    assert reads == 1
    expected_sha = hashlib.sha256(source_bytes).hexdigest()
    artifact_dir = paths["artifact_dir"]
    assert _read(artifact_dir / "research_anchor_approvals_validation.json")[
        "source_sha256"
    ] == expected_sha
    assert _read(artifact_dir / "research_anchor_revocations_validation.json")[
        "source_sha256"
    ] == expected_sha
    assert _read(artifact_dir / "approval_registry_dual_read_diff.json")[
        "approval_source_sha256"
    ] == expected_sha
    assert _read(artifact_dir / "approval_registry_switch_readiness.json")[
        "source_hashes"
    ]["research_anchor_approvals_yaml"]["approvals_source_manifest"] == expected_sha
    assert _read(artifact_dir / "support_signals_dual_ground_diff.json")[
        "approval_source_sha256"
    ] == expected_sha
    overlay = _read(
        artifact_dir / "active_research_anchor_registry_with_approvals.json"
    )
    assert overlay["counts"]["approved_active"] == 1
    assert {
        entry["source_id"]: entry["sha256"]
        for entry in overlay["source_manifest"]
        if entry["source_id"].startswith("operator_research_anchor_")
    } == {
        "operator_research_anchor_approvals_yaml": expected_sha,
        "operator_research_anchor_revocations_yaml": expected_sha,
    }
    evidence_registry = _read(artifact_dir / "evidence_packet.json")[
        "active_anchor_registry"
    ]
    assert next(
        entry["sha256"]
        for entry in evidence_registry["source_manifest"]
        if entry["source_id"] == "operator_research_anchor_approvals_yaml"
    ) == expected_sha
    assert _read(artifact_dir / "grounding_status_observatory.json")["blockers"] == []


@pytest.mark.parametrize("source_a_revoked", [False, True])
def test_complete_step1_parse_sanitizes_public_capture_once_for_all_derivations(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    source_a_revoked: bool,
) -> None:
    import investment_orchestrator.research.approvals_inclusive_active_registry as approvals_module
    import investment_orchestrator.research.evidence_packet as evidence_module
    import investment_orchestrator.workflow.step1a_grounding_compile as step1a_module

    source_a_document = {
        "schema_version": "research_anchor_approvals_v1",
        "is_llm_generated": False,
        "as_of_date": "2026-06-28",
        "approvals": [_approval()],
        "revocations": [_revocation()] if source_a_revoked else [],
    }
    paths = _setup_repo(
        tmp_path,
        monkeypatch,
        approvals_payload=source_a_document,
    )
    source_path = paths["inputs"] / "research_anchor_approvals.yaml"
    source_a_text = source_path.read_text(encoding="utf-8")
    source_b_document = {
        **source_a_document,
        "revocations": [] if source_a_revoked else [_revocation()],
    }
    source_b_text = json.dumps(source_b_document, indent=2, sort_keys=True)
    public_source = capture_research_anchor_approval_source_text(
        source_a_text,
        source_path=str(source_path),
    )
    source_a_sha = hashlib.sha256(source_a_text.encode("utf-8")).hexdigest()
    source_b_bytes = source_b_text.encode("utf-8")
    source_b_sha = hashlib.sha256(source_b_bytes).hexdigest()

    monkeypatch.setattr(
        step1_research,
        "capture_research_anchor_approval_source",
        lambda _path: public_source,
    )
    original_sanitizer = approvals_module._sanitize_captured_source
    sanitizations = 0

    def sanitize_once_then_mutate(source: Any) -> Any:
        nonlocal sanitizations
        sanitizations += 1
        snapshot = original_sanitizer(source)
        if type(source) is CapturedResearchAnchorApprovalSource:
            object.__setattr__(source, "source_bytes", source_b_bytes)
            object.__setattr__(source, "source_text", source_b_text)
            object.__setattr__(source, "source_sha256", source_b_sha)
        return snapshot

    for module in (
        approvals_module,
        evidence_module,
        step1a_module,
        step1_research,
    ):
        monkeypatch.setattr(module, "_sanitize_captured_source", sanitize_once_then_mutate)

    step1_research.parse_step1_output(strategy_settings=_settings())

    assert sanitizations == 1
    assert public_source.source_sha256 == source_b_sha
    artifact_dir = paths["artifact_dir"]
    approvals_validation = _read(
        artifact_dir / "research_anchor_approvals_validation.json"
    )
    revocations_validation = _read(
        artifact_dir / "research_anchor_revocations_validation.json"
    )
    overlay = _read(
        artifact_dir / "active_research_anchor_registry_with_approvals.json"
    )
    diff = _read(artifact_dir / "approval_registry_dual_read_diff.json")
    readiness = _read(artifact_dir / "approval_registry_switch_readiness.json")
    evidence_registry = _read(artifact_dir / "evidence_packet.json")[
        "active_anchor_registry"
    ]
    dual_ground = _read(artifact_dir / "support_signals_dual_ground_diff.json")

    assert approvals_validation["source_sha256"] == source_a_sha
    assert revocations_validation["source_sha256"] == source_a_sha
    assert overlay["counts"]["approved_active"] == (0 if source_a_revoked else 1)
    assert next(
        row["sha256"]
        for row in overlay["source_manifest"]
        if row["source_id"] == "operator_research_anchor_approvals_yaml"
    ) == source_a_sha
    assert diff["approval_source_sha256"] == source_a_sha
    assert readiness["source_hashes"]["research_anchor_approvals_yaml"][
        "approvals_source_manifest"
    ] == source_a_sha
    assert next(
        row["sha256"]
        for row in evidence_registry["source_manifest"]
        if row["source_id"] == "operator_research_anchor_approvals_yaml"
    ) == source_a_sha
    assert dual_ground["approval_source_sha256"] == source_a_sha
    assert _read(artifact_dir / "grounding_status_observatory.json")["blockers"] == []


def _replace_source_manifest_sha(
    registry: dict[str, Any],
    *,
    source_id: str = APPROVAL_SOURCE_ID,
    replacement: str = "f" * 64,
) -> None:
    for entry in registry.get("source_manifest", []):
        if entry.get("source_id") == source_id:
            entry["sha256"] = replacement
            return
    raise AssertionError(f"source manifest entry not found: {source_id}")


def _remove_source_manifest_entry(
    registry: dict[str, Any],
    *,
    source_id: str,
) -> None:
    manifest = registry.get("source_manifest")
    assert isinstance(manifest, list)
    before = len(manifest)
    registry["source_manifest"] = [
        entry
        for entry in manifest
        if not (isinstance(entry, dict) and entry.get("source_id") == source_id)
    ]
    assert len(registry["source_manifest"]) == before - 1


def _identity_registry(source_sha256: str | None) -> dict[str, Any]:
    source_manifest = []
    if source_sha256 is not None:
        source_manifest = [
            {"source_id": APPROVAL_SOURCE_ID, "sha256": source_sha256},
            {"source_id": REVOCATION_SOURCE_ID, "sha256": source_sha256},
        ]
    return {
        "counts": {"approved_active": 0},
        "active_anchors": [],
        "source_manifest": source_manifest,
    }


def _identity_policy_inputs(expected_sha256: str | None) -> dict[str, Any]:
    return {
        "expected_sha": expected_sha256,
        "approvals_validation": {"source_sha256": expected_sha256},
        "revocations_validation": {"source_sha256": expected_sha256},
        "approvals_registry": _identity_registry(expected_sha256),
        "dual_read_diff": {
            "approval_source_sha256": expected_sha256,
            "added_by_approvals": [],
        },
        # Target-only approvals readiness is intentionally not authority.
        "readiness": {
            "ready": True,
            "switch_target": "approvals_inclusive",
            "source_hashes": {
                "research_anchor_approvals_yaml": {
                    "approvals_source_manifest": expected_sha256,
                }
            },
        },
        "embedded_selection": None,
        "evidence_packet": {
            "active_anchor_registry": _identity_registry(expected_sha256)
        },
        "dual_ground_diff": None,
        "compiled_support_signals": None,
        "source_summary_sha256": expected_sha256,
    }


@pytest.mark.parametrize(
    "claim_kind",
    [
        "approved_active",
        "added_by_approvals",
        "selected_registry_row",
        "evidence_registry_row",
        "accepted_approval_support",
    ],
)
def test_each_structured_approval_grounding_claim_requires_evidence_identity(
    claim_kind: str,
) -> None:
    inputs = _identity_policy_inputs("a" * 64)
    evidence_registry = inputs["evidence_packet"]["active_anchor_registry"]
    _remove_source_manifest_entry(
        evidence_registry,
        source_id=APPROVAL_SOURCE_ID,
    )

    if claim_kind == "approved_active":
        inputs["approvals_registry"]["counts"]["approved_active"] = 1
    elif claim_kind == "added_by_approvals":
        inputs["dual_read_diff"]["added_by_approvals"] = ["APPROVED_A"]
    elif claim_kind == "selected_registry_row":
        selected_registry = _identity_registry("a" * 64)
        selected_registry["active_anchors"] = [
            {"anchor_id": "APPROVED_A", "source_id": APPROVAL_SOURCE_ID}
        ]
        inputs["embedded_selection"] = {
            "selected_source": "approvals_inclusive",
            "selected_registry": selected_registry,
            "approvals_registry": copy.deepcopy(inputs["approvals_registry"]),
            "dual_read_diff": copy.deepcopy(inputs["dual_read_diff"]),
            "readiness": copy.deepcopy(inputs["readiness"]),
        }
    elif claim_kind == "evidence_registry_row":
        evidence_registry["active_anchors"] = [
            {"anchor_id": "APPROVED_A", "source_id": APPROVAL_SOURCE_ID}
        ]
    else:
        assert claim_kind == "accepted_approval_support"
        inputs["compiled_support_signals"] = {
            "accepted_support_signals": [
                {"anchor_id": "APPROVED_A", "approval_type": "operator_authored"}
            ]
        }

    assert _workflow_approval_source_identity_mismatch(**inputs) is True


@pytest.mark.parametrize("expected_sha256", [None, "a" * 64])
def test_target_only_baseline_grounding_does_not_require_approval_identities(
    expected_sha256: str | None,
) -> None:
    inputs = _identity_policy_inputs(expected_sha256)
    evidence_registry = inputs["evidence_packet"]["active_anchor_registry"]
    if expected_sha256 is not None:
        _remove_source_manifest_entry(
            evidence_registry,
            source_id=APPROVAL_SOURCE_ID,
        )
        _remove_source_manifest_entry(
            evidence_registry,
            source_id=REVOCATION_SOURCE_ID,
        )
    inputs["compiled_support_signals"] = {
        "accepted_support_signals": [
            {"anchor_id": "BASE_QQQ", "ticker": "QQQ", "stance": "prefer"}
        ]
    }

    assert _workflow_approval_source_identity_mismatch(**inputs) is False


@pytest.mark.parametrize(
    "claim_kind",
    [
        "approved_active",
        "added_by_approvals",
        "selected_registry_row",
        "evidence_registry_row",
        "accepted_approval_support",
    ],
)
def test_absent_source_with_structured_approval_grounding_claim_fails_closed(
    claim_kind: str,
) -> None:
    inputs = _identity_policy_inputs(None)
    if claim_kind == "approved_active":
        inputs["approvals_registry"]["counts"]["approved_active"] = 1
    elif claim_kind == "added_by_approvals":
        inputs["dual_read_diff"]["added_by_approvals"] = ["APPROVED_A"]
    elif claim_kind == "selected_registry_row":
        selected_registry = _identity_registry(None)
        selected_registry["active_anchors"] = [
            {"anchor_id": "APPROVED_A", "source_id": APPROVAL_SOURCE_ID}
        ]
        inputs["embedded_selection"] = {
            "selected_source": "approvals_inclusive",
            "selected_registry": selected_registry,
        }
    elif claim_kind == "evidence_registry_row":
        inputs["evidence_packet"]["active_anchor_registry"]["active_anchors"] = [
            {"anchor_id": "APPROVED_A", "source_id": APPROVAL_SOURCE_ID}
        ]
    else:
        assert claim_kind == "accepted_approval_support"
        inputs["compiled_support_signals"] = {
            "accepted_support_signals": [
                {"anchor_id": "APPROVED_A", "approval_type": "operator_authored"}
            ]
        }

    assert inputs["expected_sha"] is None
    if claim_kind == "selected_registry_row":
        selected_registry = inputs["embedded_selection"]["selected_registry"]
        assert selected_registry["active_anchors"] == [
            {"anchor_id": "APPROVED_A", "source_id": APPROVAL_SOURCE_ID}
        ]
        assert _approval_sourced_active_count(selected_registry) == 1

    assert _workflow_approval_source_identity_mismatch(**inputs) is True


def test_complete_step1a_absent_source_selected_registry_approval_row_fails_closed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A hostile selected registry cannot activate against an ABSENT source."""
    import investment_orchestrator.workflow.step1a_grounding_compile as step1a_module

    _setup_repo(tmp_path, approvals_payload=None)
    original_enforcer = step1a_module._enforce_workflow_approval_source_identity
    pre_adoption: list[dict[str, Any]] = []

    def inject_selected_registry_row(**kwargs: Any) -> tuple[Any, ...]:
        assert kwargs["source_summary_sha256"] is None
        assert kwargs["approval_source"].source_sha256 is None
        assert kwargs["approvals_validation"]["source_present"] is False
        assert kwargs["revocations_validation"]["source_present"] is False
        assert kwargs["approvals_registry"]["counts"]["approved_active"] == 0
        assert kwargs["dual_read_diff"]["added_by_approvals"] == []
        assert _approval_sourced_active_count(
            kwargs["evidence_packet"]["active_anchor_registry"]
        ) == 0
        assert kwargs["compiled_support_signals"] is None

        selection = copy.deepcopy(kwargs["embedded_selection"])
        selected_registry = selection["selected_registry"]
        assert _approval_sourced_active_count(selected_registry) == 0
        selected_registry["active_anchors"] = [
            *selected_registry["active_anchors"],
            _production_approval_derived_selected_row(selected_registry),
        ]
        assert _approval_sourced_active_count(selected_registry) == 1
        pre_adoption.append(copy.deepcopy(selected_registry))
        return original_enforcer(
            **{**kwargs, "embedded_selection": selection}
        )

    monkeypatch.setattr(
        step1a_module,
        "_enforce_workflow_approval_source_identity",
        inject_selected_registry_row,
    )

    bundle = _bundle(tmp_path)
    artifacts = bundle["artifacts"]

    assert len(pre_adoption) == 1
    assert bundle["source_summary"]["research_anchor_approvals_source_state"] == "absent"
    assert bundle["source_summary"]["research_anchor_approvals_source_sha256"] is None
    assert _approval_sourced_active_count(pre_adoption[0]) == 1
    assert bundle["diagnostics"]["workflow_approval_source_identity_mismatch"] is True
    assert bundle["diagnostics"]["diagnostics_incomplete"] is True
    overlay = artifacts["active_research_anchor_registry_with_approvals"]
    assert overlay["registry_valid"] is False
    assert overlay["counts"]["approved_active"] == 0
    assert artifacts["approval_registry_dual_read_diff"]["added_by_approvals"] == []
    readiness = artifacts["approval_registry_switch_readiness"]
    assert readiness["ready"] is False
    assert readiness["switch_target"] != "approvals_inclusive"
    selection = artifacts["embedded_active_anchor_registry_selection"]
    assert selection["selected_source"] != "approvals_inclusive"
    assert _approval_sourced_active_count(selection["selected_registry"]) == 0
    assert _approval_sourced_active_count(
        artifacts["evidence_packet"]["active_anchor_registry"]
    ) == 0
    _assert_no_approval_grounding_or_promotion(artifacts)


def test_complete_step1_absent_source_selected_registry_approval_row_fails_closed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Step 1 sanitizes a hostile selected registry before support or promotion."""
    paths = _setup_repo(tmp_path, monkeypatch, approvals_payload=None)
    artifact_dir = paths["artifact_dir"]
    original_enforcer = step1_research._enforce_complete_step1_workflow_approval_source_identity
    pre_adoption: list[dict[str, Any]] = []

    def inject_selected_registry_row(**kwargs: Any) -> tuple[bool, dict[str, Any] | None]:
        assert kwargs["source_summary_sha256"] is None
        assert kwargs["approval_source"].source_sha256 is None
        approvals_validation = _read(
            artifact_dir / "research_anchor_approvals_validation.json"
        )
        revocations_validation = _read(
            artifact_dir / "research_anchor_revocations_validation.json"
        )
        overlay = _read(
            artifact_dir / "active_research_anchor_registry_with_approvals.json"
        )
        diff = _read(artifact_dir / "approval_registry_dual_read_diff.json")
        evidence = _read(artifact_dir / "evidence_packet.json")
        support = _read(artifact_dir / "compiled_support_signals.json")
        selection_path = artifact_dir / "embedded_active_registry_selection.json"
        selection = _read(selection_path)

        assert approvals_validation["source_present"] is False
        assert revocations_validation["source_present"] is False
        assert overlay["counts"]["approved_active"] == 0
        assert diff["added_by_approvals"] == []
        assert _approval_sourced_active_count(evidence["active_anchor_registry"]) == 0
        assert all("approval_type" not in row for row in support["accepted_support_signals"])
        assert _approval_sourced_active_count(selection["selected_registry"]) == 0
        selection["selected_registry"]["active_anchors"] = [
            *selection["selected_registry"]["active_anchors"],
            _production_approval_derived_selected_row(selection["selected_registry"]),
        ]
        assert _approval_sourced_active_count(selection["selected_registry"]) == 1
        pre_adoption.append(copy.deepcopy(selection["selected_registry"]))
        _write_json(selection_path, selection)
        return original_enforcer(**kwargs)

    monkeypatch.setattr(
        step1_research,
        "_enforce_complete_step1_workflow_approval_source_identity",
        inject_selected_registry_row,
    )

    result = step1_research.parse_step1_output(strategy_settings=_settings_with_cap())

    overlay = _read(artifact_dir / "active_research_anchor_registry_with_approvals.json")
    diff = _read(artifact_dir / "approval_registry_dual_read_diff.json")
    readiness = _read(artifact_dir / "approval_registry_switch_readiness.json")
    selection = _read(artifact_dir / "embedded_active_registry_selection.json")
    evidence = _read(artifact_dir / "evidence_packet.json")
    support = _read(artifact_dir / "compiled_support_signals.json")
    promotion = _read(
        artifact_dir / "compiled_actionable_handoff_promotion_eligibility.json"
    )

    assert len(pre_adoption) == 1
    assert _approval_sourced_active_count(pre_adoption[0]) == 1
    assert result["workflow_approval_source_identity_mismatch"] == "True"
    assert result["grounding_diagnostics_incomplete"] == "True"
    assert overlay["registry_valid"] is False
    assert overlay["counts"]["approved_active"] == 0
    assert diff["added_by_approvals"] == []
    assert readiness["ready"] is False
    assert readiness["switch_target"] != "approvals_inclusive"
    assert selection["selected_source"] != "approvals_inclusive"
    assert _approval_sourced_active_count(selection["selected_registry"]) == 0
    assert _approval_sourced_active_count(evidence["active_anchor_registry"]) == 0
    assert support["accepted_support_signals"] == []
    assert promotion["eligible_for_promotion"] is False


def test_complete_step1_genuine_no_source_preserves_valid_empty_compatibility(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paths = _setup_repo(tmp_path, monkeypatch, approvals_payload=None)
    artifact_dir = paths["artifact_dir"]

    result = step1_research.parse_step1_output(
        strategy_settings=_settings_with_cap()
    )

    overlay = _read(
        artifact_dir / "active_research_anchor_registry_with_approvals.json"
    )
    diff = _read(artifact_dir / "approval_registry_dual_read_diff.json")
    readiness = _read(artifact_dir / "approval_registry_switch_readiness.json")
    selection = _read(artifact_dir / "embedded_active_registry_selection.json")
    support = _read(artifact_dir / "compiled_support_signals.json")

    assert result["workflow_approval_source_identity_mismatch"] == "False"
    assert result["grounding_diagnostics_incomplete"] == "False"
    assert overlay["registry_valid"] is True
    assert overlay["counts"]["approved_active"] == 0
    assert diff["added_by_approvals"] == []
    assert readiness["ready"] is True
    assert readiness["switch_target"] == "approvals_inclusive"
    assert selection["selected_source"] == "approvals_inclusive"
    assert _approval_sourced_active_count(selection["selected_registry"]) == 0
    assert support["accepted_support_signals"] == []


def test_complete_step1_baseline_only_support_preserves_target_only_policy(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paths = _setup_repo(tmp_path, monkeypatch, approvals_payload=None)
    artifact_dir = paths["artifact_dir"]
    memo = _approval_grounded_memo()
    memo["ticker_relative_view"][0]["anchor_id_refs"] = ["BASE_QQQ"]
    memo["ticker_relative_view"][0][
        "rationale_12m_plus"
    ] = "Baseline-only grounding probe."
    (artifact_dir / "analyst_memo_raw_output.txt").write_text(
        json.dumps(memo), encoding="utf-8"
    )

    result = step1_research.parse_step1_output(
        strategy_settings=_settings_with_cap()
    )

    readiness = _read(artifact_dir / "approval_registry_switch_readiness.json")
    evidence = _read(artifact_dir / "evidence_packet.json")
    support = _read(artifact_dir / "compiled_support_signals.json")
    promotion = _read(
        artifact_dir / "compiled_actionable_handoff_promotion_eligibility.json"
    )

    assert result["workflow_approval_source_identity_mismatch"] == "False"
    assert result["grounding_diagnostics_incomplete"] == "False"
    assert readiness["ready"] is True
    assert readiness["switch_target"] == "approvals_inclusive"
    assert _approval_sourced_active_count(evidence["active_anchor_registry"]) == 0
    assert [row["anchor_id"] for row in support["accepted_support_signals"]] == [
        "BASE_QQQ"
    ]
    assert all(
        "approval_type" not in row for row in support["accepted_support_signals"]
    )
    assert promotion["eligible_for_promotion"] is True


@pytest.mark.parametrize(
    "mismatch_kind",
    [
        "approval_validation",
        "approval_validation_missing",
        "revocation_validation",
        "overlay",
        "diff",
        "readiness",
        "embedded_overlay",
        "embedded_diff",
        "embedded_readiness",
        "selected_registry",
        "selected_registry_revocation",
        "selected_registry_missing",
        "selected_registry_revocation_missing",
        "selected_registry_both_missing",
        "selected_registry_approval_malformed",
        "selected_registry_revocation_malformed",
        "evidence_packet",
        "evidence_packet_revocation",
        "evidence_packet_missing_without_embedded",
        "evidence_packet_revocation_missing_without_embedded",
        "evidence_packet_both_missing_without_embedded",
        "evidence_packet_malformed_without_embedded",
        "evidence_packet_revocation_malformed_without_embedded",
        "dual_ground",
        "source_summary",
    ],
)
def test_complete_step1_identity_join_blocks_each_mismatched_candidate_before_adoption(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    mismatch_kind: str,
) -> None:
    paths = _setup_repo(tmp_path, monkeypatch)
    artifact_dir = paths["artifact_dir"]
    (artifact_dir / "analyst_memo_raw_output.txt").write_text(
        json.dumps(_approval_grounded_memo()), encoding="utf-8"
    )

    if mismatch_kind == "source_summary":
        original_summary = step1_research._verified_approval_source_summary

        def mismatched_summary(source: Any) -> tuple[str, str | None, bool]:
            state, source_sha, incomplete = original_summary(source)
            assert source_sha is not None
            return state, "f" * 64, incomplete

        monkeypatch.setattr(
            step1_research,
            "_verified_approval_source_summary",
            mismatched_summary,
        )
    else:
        original_dual_ground_writer = (
            step1_research._write_support_signals_dual_ground_diff_report_only
        )

        def write_then_mutate(**kwargs: Any) -> None:
            original_dual_ground_writer(**kwargs)
            if mismatch_kind == "approval_validation":
                path = artifact_dir / "research_anchor_approvals_validation.json"
                payload = _read(path)
                payload["source_sha256"] = "f" * 64
            elif mismatch_kind == "approval_validation_missing":
                path = artifact_dir / "research_anchor_approvals_validation.json"
                payload = _read(path)
                payload.pop("source_sha256")
            elif mismatch_kind == "revocation_validation":
                path = artifact_dir / "research_anchor_revocations_validation.json"
                payload = _read(path)
                payload["source_sha256"] = "f" * 64
            elif mismatch_kind == "overlay":
                path = artifact_dir / "active_research_anchor_registry_with_approvals.json"
                payload = _read(path)
                _replace_source_manifest_sha(payload)
            elif mismatch_kind == "diff":
                path = artifact_dir / "approval_registry_dual_read_diff.json"
                payload = _read(path)
                payload["approval_source_sha256"] = "f" * 64
            elif mismatch_kind == "readiness":
                path = artifact_dir / "approval_registry_switch_readiness.json"
                payload = _read(path)
                payload["source_hashes"]["research_anchor_approvals_yaml"][
                    "approvals_source_manifest"
                ] = "f" * 64
            elif mismatch_kind.startswith("embedded_") or mismatch_kind.startswith(
                "selected_registry"
            ):
                path = artifact_dir / "embedded_active_registry_selection.json"
                payload = _read(path)
                if mismatch_kind == "embedded_overlay":
                    _replace_source_manifest_sha(payload["approvals_registry"])
                elif mismatch_kind == "embedded_diff":
                    payload["dual_read_diff"]["approval_source_sha256"] = "f" * 64
                elif mismatch_kind == "embedded_readiness":
                    payload["readiness"]["source_hashes"][
                        "research_anchor_approvals_yaml"
                    ]["approvals_source_manifest"] = "f" * 64
                elif mismatch_kind == "selected_registry":
                    _replace_source_manifest_sha(payload["selected_registry"])
                elif mismatch_kind == "selected_registry_revocation":
                    _replace_source_manifest_sha(
                        payload["selected_registry"],
                        source_id="operator_research_anchor_revocations_yaml",
                    )
                elif mismatch_kind == "selected_registry_missing":
                    _remove_source_manifest_entry(
                        payload["selected_registry"],
                        source_id="operator_research_anchor_approvals_yaml",
                    )
                elif mismatch_kind == "selected_registry_revocation_missing":
                    _remove_source_manifest_entry(
                        payload["selected_registry"],
                        source_id="operator_research_anchor_revocations_yaml",
                    )
                elif mismatch_kind == "selected_registry_both_missing":
                    _remove_source_manifest_entry(
                        payload["selected_registry"],
                        source_id=APPROVAL_SOURCE_ID,
                    )
                    _remove_source_manifest_entry(
                        payload["selected_registry"],
                        source_id=REVOCATION_SOURCE_ID,
                    )
                elif mismatch_kind == "selected_registry_approval_malformed":
                    _replace_source_manifest_sha(
                        payload["selected_registry"],
                        source_id=APPROVAL_SOURCE_ID,
                        replacement="not-a-sha256",
                    )
                else:
                    assert mismatch_kind == "selected_registry_revocation_malformed"
                    _replace_source_manifest_sha(
                        payload["selected_registry"],
                        source_id=REVOCATION_SOURCE_ID,
                        replacement="not-a-sha256",
                    )
            elif mismatch_kind.startswith("evidence_packet"):
                path = artifact_dir / "evidence_packet.json"
                payload = _read(path)
                if mismatch_kind.endswith("without_embedded"):
                    (artifact_dir / "embedded_active_registry_selection.json").unlink()
                    if mismatch_kind in {
                        "evidence_packet_missing_without_embedded",
                        "evidence_packet_both_missing_without_embedded",
                    }:
                        _remove_source_manifest_entry(
                            payload["active_anchor_registry"],
                            source_id="operator_research_anchor_approvals_yaml",
                        )
                    if mismatch_kind in {
                        "evidence_packet_revocation_missing_without_embedded",
                        "evidence_packet_both_missing_without_embedded",
                    }:
                        _remove_source_manifest_entry(
                            payload["active_anchor_registry"],
                            source_id="operator_research_anchor_revocations_yaml",
                        )
                    if mismatch_kind in {
                        "evidence_packet_malformed_without_embedded",
                        "evidence_packet_revocation_malformed_without_embedded",
                    }:
                        malformed_source_id = (
                            REVOCATION_SOURCE_ID
                            if mismatch_kind
                            == "evidence_packet_revocation_malformed_without_embedded"
                            else APPROVAL_SOURCE_ID
                        )
                        for entry in payload["active_anchor_registry"]["source_manifest"]:
                            if entry.get("source_id") == malformed_source_id:
                                entry["sha256"] = "not-a-sha256"
                                break
                        else:
                            raise AssertionError(
                                f"source identity not found: {malformed_source_id}"
                            )
                else:
                    _replace_source_manifest_sha(
                        payload["active_anchor_registry"],
                        source_id=(
                            "operator_research_anchor_revocations_yaml"
                            if mismatch_kind == "evidence_packet_revocation"
                            else "operator_research_anchor_approvals_yaml"
                        ),
                    )
            else:
                assert mismatch_kind == "dual_ground"
                path = artifact_dir / "support_signals_dual_ground_diff.json"
                payload = _read(path)
                payload["approval_source_sha256"] = "f" * 64
            _write_json(path, payload)

        monkeypatch.setattr(
            step1_research,
            "_write_support_signals_dual_ground_diff_report_only",
            write_then_mutate,
        )

    result = step1_research.parse_step1_output(strategy_settings=_settings_with_cap())

    overlay = _read(
        artifact_dir / "active_research_anchor_registry_with_approvals.json"
    )
    diff = _read(artifact_dir / "approval_registry_dual_read_diff.json")
    readiness = _read(artifact_dir / "approval_registry_switch_readiness.json")
    selection_path = artifact_dir / "embedded_active_registry_selection.json"
    selection = _read(selection_path) if selection_path.exists() else {}
    evidence = _read(artifact_dir / "evidence_packet.json")
    support = _read(artifact_dir / "compiled_support_signals.json")
    promotion = _read(
        artifact_dir / "compiled_actionable_handoff_promotion_eligibility.json"
    )
    observatory = _read(artifact_dir / "grounding_status_observatory.json")

    assert result["workflow_approval_source_identity_mismatch"] == "True"
    assert result["grounding_diagnostics_incomplete"] == "True"
    assert result["research_anchor_approvals_source_sha256"] == ""
    assert overlay["registry_valid"] is False
    assert overlay["counts"]["approved_active"] == 0
    assert "workflow_approval_source_identity_mismatch" in overlay["registry_blockers"]
    assert diff["added_by_approvals"] == []
    assert readiness["ready"] is False
    assert selection.get("selected_source") != "approvals_inclusive"
    assert _approval_sourced_active_count(evidence["active_anchor_registry"]) == 0
    assert support["accepted_support_signals"] == []
    assert promotion["eligible_for_promotion"] is False
    assert "workflow_approval_source_identity_mismatch" in observatory["blockers"]
    assert observatory["diagnostics"]["diagnostics_incomplete"] is True


def test_complete_step1_accepts_coherent_evidence_identities_without_optional_embedded_selection(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paths = _setup_repo(tmp_path, monkeypatch)
    artifact_dir = paths["artifact_dir"]
    (artifact_dir / "analyst_memo_raw_output.txt").write_text(
        json.dumps(_approval_grounded_memo()), encoding="utf-8"
    )
    original_dual_ground_writer = (
        step1_research._write_support_signals_dual_ground_diff_report_only
    )

    def remove_optional_selection(**kwargs: Any) -> None:
        original_dual_ground_writer(**kwargs)
        (artifact_dir / "embedded_active_registry_selection.json").unlink()

    monkeypatch.setattr(
        step1_research,
        "_write_support_signals_dual_ground_diff_report_only",
        remove_optional_selection,
    )

    result = step1_research.parse_step1_output(
        strategy_settings=_settings_with_cap()
    )

    assert result["workflow_approval_source_identity_mismatch"] == "False"
    assert not (artifact_dir / "embedded_active_registry_selection.json").exists()
    assert _read(artifact_dir / "approval_registry_switch_readiness.json")["ready"] is True
    assert [
        row["anchor_id"]
        for row in _read(artifact_dir / "compiled_support_signals.json")[
            "accepted_support_signals"
        ]
    ] == ["AI_CAPEX_2026H2"]
    coherent_signals, coherent_promotion = _promotion_chain_for_packet(
        _read(artifact_dir / "evidence_packet.json")
    )
    assert len(coherent_signals["accepted_support_signals"]) == 1
    assert coherent_promotion["eligible_for_promotion"] is True


@pytest.mark.parametrize(
    "source_id",
    [APPROVAL_SOURCE_ID, REVOCATION_SOURCE_ID],
    ids=["approval_identity", "revocation_identity"],
)
def test_complete_step1_required_evidence_identity_blocks_otherwise_promotable_chain(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    source_id: str,
) -> None:
    paths = _setup_repo(tmp_path, monkeypatch)
    artifact_dir = paths["artifact_dir"]
    (artifact_dir / "analyst_memo_raw_output.txt").write_text(
        json.dumps(_approval_grounded_memo()), encoding="utf-8"
    )
    original_dual_ground_writer = (
        step1_research._write_support_signals_dual_ground_diff_report_only
    )
    candidate_promotion: dict[str, Any] = {}

    def prove_promotable_then_remove_identity(**kwargs: Any) -> None:
        original_dual_ground_writer(**kwargs)
        packet_path = artifact_dir / "evidence_packet.json"
        packet = _read(packet_path)
        signals, promotion = _promotion_chain_for_packet(packet)
        assert len(signals["accepted_support_signals"]) == 1
        assert promotion["eligible_for_promotion"] is True, promotion[
            "promotion_blockers"
        ]
        candidate_promotion.update(promotion)
        (artifact_dir / "embedded_active_registry_selection.json").unlink()
        _remove_source_manifest_entry(
            packet["active_anchor_registry"],
            source_id=source_id,
        )
        _write_json(packet_path, packet)

    monkeypatch.setattr(
        step1_research,
        "_write_support_signals_dual_ground_diff_report_only",
        prove_promotable_then_remove_identity,
    )

    result = step1_research.parse_step1_output(
        strategy_settings=_settings_with_cap()
    )
    safe_packet = _read(artifact_dir / "evidence_packet.json")
    safe_signals, safe_promotion = _promotion_chain_for_packet(safe_packet)

    assert candidate_promotion["eligible_for_promotion"] is True
    assert result["workflow_approval_source_identity_mismatch"] == "True"
    assert safe_signals["accepted_support_signals"] == []
    assert safe_promotion["eligible_for_promotion"] is False
    assert _read(artifact_dir / "approval_registry_switch_readiness.json")["ready"] is False
    assert _read(artifact_dir / "compiled_support_signals.json")[
        "accepted_support_signals"
    ] == []


def test_complete_step1_identity_join_preserves_coherent_approval_support_and_promotion_policy(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paths = _setup_repo(tmp_path, monkeypatch)
    artifact_dir = paths["artifact_dir"]
    (artifact_dir / "analyst_memo_raw_output.txt").write_text(
        json.dumps(_approval_grounded_memo()), encoding="utf-8"
    )

    result = step1_research.parse_step1_output(strategy_settings=_settings_with_cap())

    overlay = _read(
        artifact_dir / "active_research_anchor_registry_with_approvals.json"
    )
    readiness = _read(artifact_dir / "approval_registry_switch_readiness.json")
    selection = _read(artifact_dir / "embedded_active_registry_selection.json")
    support = _read(artifact_dir / "compiled_support_signals.json")
    promotion = _read(
        artifact_dir / "compiled_actionable_handoff_promotion_eligibility.json"
    )

    assert result["workflow_approval_source_identity_mismatch"] == "False"
    assert result["grounding_diagnostics_incomplete"] == "False"
    assert overlay["counts"]["approved_active"] == 1
    assert readiness["ready"] is True
    assert selection["selected_source"] == "approvals_inclusive"
    assert [row["anchor_id"] for row in support["accepted_support_signals"]] == [
        "AI_CAPEX_2026H2"
    ]
    # This fixture's established actionable-candidate policy does not promote an
    # approval-only anchor, but the identity join must not add a new blocker or
    # remove the otherwise accepted support signal.
    assert promotion["eligible_for_promotion"] is False
    assert "workflow_approval_source_identity_mismatch" not in promotion[
        "promotion_blockers"
    ]


def test_malformed_revocations_fail_closed_in_revocation_validation(tmp_path: Path) -> None:
    _setup_repo(
        tmp_path,
        approvals_payload={
            "schema_version": "research_anchor_approvals_v1",
            "is_llm_generated": False,
            "as_of_date": "2026-06-28",
            "approvals": [_approval()],
            "revocations": "not-a-list",
        },
    )
    artifacts = _bundle(tmp_path)["artifacts"]
    revocations = artifacts["research_anchor_revocations_validation"]

    assert revocations["source_valid"] is False
    assert revocations["revocations_valid"] is False
    assert artifacts["active_research_anchor_registry_with_approvals"][
        "counts"
    ]["approved_active"] == 0
    _assert_no_approval_grounding_or_promotion(artifacts)


def test_matching_revocation_blocks_bundle_grounding_and_promotion(tmp_path: Path) -> None:
    _setup_repo(
        tmp_path,
        approvals_payload={
            "schema_version": "research_anchor_approvals_v1",
            "is_llm_generated": False,
            "as_of_date": "2026-06-28",
            "approvals": [_approval()],
            "revocations": [_revocation()],
        },
    )
    artifacts = _bundle(tmp_path)["artifacts"]
    overlay = artifacts["active_research_anchor_registry_with_approvals"]

    assert overlay["counts"]["approved_active"] == 0
    assert overlay["counts"]["revoked"] == 1
    _assert_no_approval_grounding_or_promotion(artifacts)


def test_invalid_revocation_target_fails_closed_without_runtime_authority(tmp_path: Path) -> None:
    _setup_repo(
        tmp_path,
        approvals_payload={
            "schema_version": "research_anchor_approvals_v1",
            "is_llm_generated": False,
            "as_of_date": "2026-06-28",
            "approvals": [_approval()],
            "revocations": [_revocation(approval_id="APR-UNKNOWN")],
        },
    )
    artifacts = _bundle(tmp_path)["artifacts"]
    revocations = artifacts["research_anchor_revocations_validation"]

    assert revocations["revocations_valid"] is False
    assert any(r["target_binding_status"] == "target_not_found" for r in revocations["revocation_results"])
    assert artifacts["active_research_anchor_registry_with_approvals"]["registry_valid"] is False
    assert artifacts["grounding_status_observatory"]["revocations_summary"]["unknown_target_count"] == 1
    _assert_no_approval_grounding_or_promotion(artifacts)


def test_invalid_baseline_registry_selects_fail_closed_empty(tmp_path: Path) -> None:
    _setup_repo(tmp_path, anchors=[_anchor("DUP", "QQQ"), _anchor("DUP", "VOO")])
    artifacts = _bundle(tmp_path)["artifacts"]

    assert artifacts["active_research_anchor_registry"]["registry_valid"] is False
    assert artifacts["approval_registry_switch_readiness"]["switch_target"] == SWITCH_TARGET_FAIL_CLOSED
    assert artifacts["embedded_active_anchor_registry_selection"]["selected_source"] == SWITCH_TARGET_FAIL_CLOSED
    assert artifacts["evidence_packet"]["active_anchor_registry"]["active_anchors"] == []


def test_unknown_schema_and_partial_optional_inputs_are_observatory_diagnostics(tmp_path: Path) -> None:
    _setup_repo(tmp_path)
    bundle = _bundle(tmp_path, optional_candidates={"schema_version": "future_candidates_v999"})
    observatory = bundle["artifacts"]["grounding_status_observatory"]

    assert observatory["diagnostics"]["diagnostics_incomplete"] is True
    assert {"input": "candidates", "schema_version": "future_candidates_v999"} in observatory["diagnostics"][
        "unknown_schema_sources"
    ]
    assert any(problem["input"] == "support_signals" for problem in observatory["diagnostics"]["input_problems"])
    assert observatory["cannot_affect_allowed_actions"] is True
    assert observatory["consumed_by_gates"] is False


def test_builder_source_has_no_llm_or_support_signal_interpretation_imports() -> None:
    import investment_orchestrator.workflow.step1a_grounding_compile as s1a

    source = inspect.getsource(s1a)
    assert "investment_orchestrator.llm" not in source
    assert "manual_output" not in source
    assert "render_prompt" not in source
    assert "support_signals import" not in source
    assert "write_json" not in source


def test_no_production_consumer_imports_step1a_bundle() -> None:
    import investment_orchestrator.research.approval_registry_switch_readiness as readiness
    import investment_orchestrator.research.evidence_packet as evidence_packet
    import investment_orchestrator.research.support_signals as support_signals
    import investment_orchestrator.state.final_execution_safety_gate as final_gate
    import investment_orchestrator.state.research_availability as availability
    import investment_orchestrator.workflow.step1_research as step1
    import investment_orchestrator.workflow.step2_decision_builder as step2
    import investment_orchestrator.workflow.step3_audit_engine as step3
    import investment_orchestrator.workflow.step4_order_compiler as step4
    import investment_orchestrator.workflow.weekly_orchestrator as weekly

    step1_source = inspect.getsource(step1)
    assert "_write_step1a_grounding_compile_shadow_diff_report_only" in step1_source
    assert "step1a_grounding_compile_shadow_diff.json" in step1_source

    for module in (
        readiness,
        evidence_packet,
        support_signals,
        availability,
        step2,
        step3,
        step4,
        final_gate,
        weekly,
    ):
        assert "step1a_grounding_compile" not in inspect.getsource(module)
        assert "build_step1a_grounding_compile_bundle" not in inspect.getsource(module)


def test_bundle_opens_no_order_or_permission_path(tmp_path: Path) -> None:
    _setup_repo(tmp_path)
    bundle = _bundle(tmp_path)
    blob = json.dumps(bundle)

    assert '"allowed_actions"' not in blob
    assert bundle["safety_invariants"]["no_new_buy_permission"] is True
    assert bundle["safety_invariants"]["no_order_compilation_permission"] is True
    assert bundle["safety_invariants"]["no_step4_enablement"] is True
    assert bundle["safety_invariants"]["no_final_execution"] is True
    assert bundle["safety_invariants"]["no_weekly_automation_change"] is True
    assert bundle["safety_invariants"]["no_broker_live_execution"] is True
    assert bundle["safety_invariants"]["no_executable_order_authority"] is True
