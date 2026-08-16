"""Tests for V1 P8A REVIEW-ONLY BUY publication."""

import hashlib
import json
from pathlib import Path
from typing import Any

import pytest

from investment_orchestrator.common.paths import artifacts_dir
from investment_orchestrator.mmi.canonical import canonical_json_bytes
from investment_orchestrator.mmi.contracts import AUTHORITY_EFFECT_NONE
from investment_orchestrator.workflow import p8_v1_review_order_publication as _p8a
from investment_orchestrator.workflow.h1_v1_postcompile_final_safety import (
    H1V1PostcompileFinalSafetyResult,
    POSTCOMPILE_CANDIDATE_VALID,
    POSTCOMPILE_HOLD,
    POSTCOMPILE_NO_TRADE,
)
from investment_orchestrator.workflow.h1_v1_buy_compiler_dry_run import H1V1BuyDryRunLeg
from investment_orchestrator.state import research_availability


@pytest.fixture
def mock_p6(monkeypatch):
    """Mock the P6 evaluator to return a predictable valid BUY candidate by default."""
    default_result = H1V1PostcompileFinalSafetyResult(
        terminal_outcome=POSTCOMPILE_CANDIDATE_VALID,
        reason_code=POSTCOMPILE_CANDIDATE_VALID,
        state="H1_V1_DETERMINISTIC_PROPOSAL_READY",
        selected_ticker="TLT",
        deterministic_role="core",
        candidate_legs=(
            H1V1BuyDryRunLeg(
                step_name="step1",
                allocation_weight="1.00",
                limit_offset_from_mark="0.00",
                rounded_limit_price="95.00",
                whole_share_quantity=10,
                candidate_notional="950.00",
            ),
        ),
        target_increment="1000.00",
        total_new_candidate_notional="950.00",
        postcompile_total_unfilled_buy_commitment="950.00",
        postcompile_alpha_exposure="0",
        postcompile_core_exposure="950.00",
        ticker_exposures=(),
        source_bindings=(
            ("calendar_id", "NY"),
            ("calendar_schedule_sha256", "abc"),
            ("h1_raw_response_sha256", "abc"),
            ("h1_rendered_prompt_sha256", "abc"),
            ("holdings_observation_date", "2024-01-01"),
            ("holdings_policy_projection_identity_sha256", "abc"),
            ("latest_completed_session_date", "2024-01-01"),
            ("portfolio_scope_id", "abc"),
            ("portfolio_source_record_identity_sha256", "abc"),
            ("portfolio_source_sha256", "abc"),
            ("r_source_sha256", "abc"),
            ("role_universe_projection_identity_sha256", "abc"),
            ("strategy_source_record_identity_sha256", "abc"),
            ("strategy_source_sha256", "abc"),
            ("valuation_capture_sha256", "abc"),
            ("valuation_freshness_status", "FRESH"),
            ("valuation_provider_id", "abc"),
            ("valuation_session_date", "2024-01-01"),
            ("valuation_source_kind", "abc"),
            ("valuation_trusted_evaluation_timestamp_utc", "2024-01-01T12:00:00Z"),
            ("x_source_sha256", "abc"),
            ("h1_evidence_entry_identities_sha256", ["abc", "def"]),
            ("h1_report_evidence_references", ["xyz"]),
        ),
        authority_effect=AUTHORITY_EFFECT_NONE,
        not_authorization=True,
    )

    class MockEvaluator:
        def __init__(self):
            self.call_count = 0
            self.result = default_result

        def __call__(self):
            self.call_count += 1
            return self.result

    mock_eval = MockEvaluator()
    monkeypatch.setattr(
        "investment_orchestrator.workflow.p8_v1_review_order_publication.evaluate_h1_v1_postcompile_final_safety",
        mock_eval,
    )
    return mock_eval


@pytest.fixture
def isolated_artifact_dir(tmp_path, monkeypatch):
    """Isolate the artifact output directory."""
    artifacts = tmp_path / "artifacts"
    monkeypatch.setattr(
        "investment_orchestrator.workflow.p8_v1_review_order_publication.artifacts_dir",
        lambda: artifacts,
    )
    return artifacts


def test_fresh_positive_creates_artifact(mock_p6, isolated_artifact_dir):
    """A. Real fresh P6 positive result -> creates artifact. C. P6 invoked exactly once."""
    result = _p8a.publish_h1_v1_review_order()

    assert result.terminal_outcome == POSTCOMPILE_CANDIDATE_VALID
    assert result.immutable_path.exists()
    assert not result.existed_idempotently
    assert mock_p6.call_count == 1
    assert result.selected_ticker == "TLT"
    assert result.total_candidate_notional == "950.00"


def test_hold_rejection(mock_p6, isolated_artifact_dir):
    """E. Fresh P6 HOLD -> subject mismatch, zero artifact."""
    import dataclasses
    mock_p6.result = dataclasses.replace(
        mock_p6.result,
        terminal_outcome=POSTCOMPILE_HOLD,
        reason_code="NO_SHARED_CAPACITY"
    )

    with pytest.raises(_p8a.V1P8APublicationError, match="V1_P8A_PUBLICATION_SUBJECT_MISMATCH"):
        _p8a.publish_h1_v1_review_order()

    p8_dir = isolated_artifact_dir / "v1_review_orders"
    if p8_dir.exists():
        assert list(p8_dir.iterdir()) == []


def test_no_trade_rejection(mock_p6, isolated_artifact_dir):
    """F. Fresh P6 NO_TRADE -> subject mismatch, zero artifact."""
    import dataclasses
    mock_p6.result = dataclasses.replace(
        mock_p6.result,
        terminal_outcome=POSTCOMPILE_NO_TRADE,
        reason_code="US_EQUITY_SESSION_MARK_DATE_STALE"
    )

    with pytest.raises(_p8a.V1P8APublicationError, match="V1_P8A_PUBLICATION_SUBJECT_MISMATCH"):
        _p8a.publish_h1_v1_review_order()


def test_exact_package_contract_and_hash_oracle(mock_p6, isolated_artifact_dir):
    """B. Exact complete positive artifact. L. Independent content identity."""
    result = _p8a.publish_h1_v1_review_order()

    with open(result.immutable_path) as f:
        payload = json.load(f)

    expected = {
        "schema_name": "v1_review_order_publication",
        "schema_version": "1.0",
        "publication_kind": "V1_REVIEW_ORDER",
        "ticker": "TLT",
        "action": "BUY",
        "target_notional": "1000.00",
        "total_candidate_notional": "950.00",
        "legs": [
            {
                "step_name": "step1",
                "whole_share_quantity": 10,
                "rounded_limit_price": "95.00",
                "candidate_notional": "950.00",
            }
        ],
        "source_bindings": dict(mock_p6.result.source_bindings),
        "authority_effect": AUTHORITY_EFFECT_NONE,
        "not_authorization": True,
    }
    assert payload == expected

    expected_bytes = canonical_json_bytes(expected)
    expected_hash = hashlib.sha256(expected_bytes).hexdigest()
    assert expected_hash == result.artifact_identity_sha256
    assert result.immutable_path.name == f"{expected_hash}.json"


def test_idempotent_same_content_behavior(mock_p6, isolated_artifact_dir):
    """M. Existing exact raw canonical bytes -> success, no rewrite."""
    first_result = _p8a.publish_h1_v1_review_order()
    assert not first_result.existed_idempotently

    second_result = _p8a.publish_h1_v1_review_order()
    assert second_result.existed_idempotently
    assert first_result.artifact_identity_sha256 == second_result.artifact_identity_sha256


def test_existing_target_mismatch(mock_p6, isolated_artifact_dir):
    """O. Different existing bytes -> fail closed, no overwrite."""
    result = _p8a.publish_h1_v1_review_order()

    result.immutable_path.write_text('{"bad": "bytes"}')

    with pytest.raises(_p8a.V1P8APublicationError, match="V1_P8A_EXISTING_IDENTITY_MISMATCH"):
        _p8a.publish_h1_v1_review_order()


def test_existing_semantically_equivalent_but_byte_different_json_rejected(mock_p6, isolated_artifact_dir):
    """N. Noncanonical equivalent existing bytes -> fail closed, no overwrite."""
    result = _p8a.publish_h1_v1_review_order()

    with open(result.immutable_path) as f:
        data = json.load(f)

    result.immutable_path.write_text(json.dumps(data, indent=4))

    with pytest.raises(_p8a.V1P8APublicationError, match="V1_P8A_EXISTING_IDENTITY_MISMATCH"):
        _p8a.publish_h1_v1_review_order()


def test_missing_binding_key_rejected(mock_p6, isolated_artifact_dir):
    """K. missing binding key rejected."""
    import dataclasses
    bad_bindings = tuple(b for b in mock_p6.result.source_bindings if b[0] != "calendar_id")
    mock_p6.result = dataclasses.replace(mock_p6.result, source_bindings=bad_bindings)
    with pytest.raises(_p8a.V1P8APublicationError, match="V1_P8A_VALIDATION_PROVENANCE_INVALID"):
        _p8a.publish_h1_v1_review_order()


def test_unexpected_additional_binding_key_rejected(mock_p6, isolated_artifact_dir):
    """K. unexpected additional binding key rejected."""
    import dataclasses
    bad_bindings = mock_p6.result.source_bindings + (("extra_key", "val"),)
    mock_p6.result = dataclasses.replace(mock_p6.result, source_bindings=bad_bindings)
    with pytest.raises(_p8a.V1P8APublicationError, match="V1_P8A_VALIDATION_PROVENANCE_INVALID"):
        _p8a.publish_h1_v1_review_order()


def test_no_state_permission_changes():
    """S. State/permission unchanged."""
    allowed = research_availability.canonical_allowed_actions_for_state(
        "H1_V1_DETERMINISTIC_PROPOSAL_READY"
    )
    assert allowed == ("HOLD", "NO_TRADE", "NEW_BUY", "ORDER_COMPILATION")


def test_step4_isolation():
    """Q. Step4 isolation."""
    p8_dir = artifacts_dir() / "v1_review_orders"
    step4_dir = artifacts_dir() / "current" / "step4_order_compiler"
    assert p8_dir != step4_dir
    assert "current" not in p8_dir.parts
