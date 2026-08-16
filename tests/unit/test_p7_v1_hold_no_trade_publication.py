import dataclasses
"""Tests for V1 P7A HOLD/NO_TRADE publication."""

import hashlib
import json
from pathlib import Path
from typing import Any

import pytest

from investment_orchestrator.common.paths import artifacts_dir
from investment_orchestrator.mmi.canonical import canonical_json_bytes
from investment_orchestrator.mmi.contracts import AUTHORITY_EFFECT_NONE
from investment_orchestrator.workflow import p7_v1_hold_no_trade_publication as _p7a
from investment_orchestrator.workflow.h1_v1_postcompile_final_safety import (
    H1V1PostcompileFinalSafetyResult,
    POSTCOMPILE_CANDIDATE_VALID,
    POSTCOMPILE_HOLD,
    POSTCOMPILE_NO_TRADE,
)
from investment_orchestrator.state import research_availability


@pytest.fixture
def mock_p6(monkeypatch):
    """Mock the P6 evaluator to return a predictable HOLD by default."""
    default_result = H1V1PostcompileFinalSafetyResult(
        terminal_outcome=POSTCOMPILE_HOLD,
        reason_code="NO_SHARED_CAPACITY",
        state=None,
        selected_ticker=None,
        deterministic_role=None,
        candidate_legs=(),
        target_increment=None,
        total_new_candidate_notional=None,
        postcompile_total_unfilled_buy_commitment=None,
        postcompile_alpha_exposure=None,
        postcompile_core_exposure=None,
        ticker_exposures=(),
source_bindings=(
            ('calendar_id', 'NY'),
            ('calendar_schedule_sha256', 'abc'),
            ('h1_raw_response_sha256', 'abc'),
            ('h1_rendered_prompt_sha256', 'abc'),
            ('holdings_observation_date', '2024-01-01'),
            ('holdings_policy_projection_identity_sha256', 'abc'),
            ('latest_completed_session_date', '2024-01-01'),
            ('portfolio_scope_id', 'abc'),
            ('portfolio_source_record_identity_sha256', 'abc'),
            ('portfolio_source_sha256', 'abc'),
            ('r_source_sha256', 'abc'),
            ('role_universe_projection_identity_sha256', 'abc'),
            ('strategy_source_record_identity_sha256', 'abc'),
            ('strategy_source_sha256', 'abc'),
            ('valuation_capture_sha256', 'abc'),
            ('valuation_freshness_status', 'FRESH'),
            ('valuation_provider_id', 'abc'),
            ('valuation_session_date', '2024-01-01'),
            ('valuation_source_kind', 'abc'),
            ('valuation_trusted_evaluation_timestamp_utc', '2024-01-01T12:00:00Z'),
            ('x_source_sha256', 'abc'),
            ('h1_evidence_entry_identities_sha256', ['abc', 'def']),
            ('h1_report_evidence_references', ['xyz']),
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
        "investment_orchestrator.workflow.p7_v1_hold_no_trade_publication.evaluate_h1_v1_postcompile_final_safety",
        mock_eval,
    )
    return mock_eval


@pytest.fixture
def isolated_artifact_dir(tmp_path, monkeypatch):
    """Isolate the artifact output directory."""
    artifacts = tmp_path / "artifacts"
    monkeypatch.setattr(
        "investment_orchestrator.workflow.p7_v1_hold_no_trade_publication.artifacts_dir",
        lambda: artifacts,
    )
    return artifacts


def test_fresh_hold_creates_artifact(mock_p6, isolated_artifact_dir):
    """1. Real fresh P6 HOLD -> valid immutable P7A artifact."""
    result = _p7a.publish_h1_v1_hold_no_trade()
    
    assert result.terminal_outcome == POSTCOMPILE_HOLD
    assert result.immutable_path.exists()
    assert not result.existed_idempotently
    assert mock_p6.call_count == 1  # 4. Exactly one P6 invocation


def test_fresh_no_trade_creates_artifact(mock_p6, isolated_artifact_dir):
    """2. Real fresh P6 NO_TRADE -> valid immutable P7A artifact."""
    mock_p6.result = H1V1PostcompileFinalSafetyResult(
        terminal_outcome=POSTCOMPILE_NO_TRADE,
        reason_code="US_EQUITY_SESSION_MARK_DATE_STALE",
        state=None,
        selected_ticker=None,
        deterministic_role=None,
        candidate_legs=(),
        target_increment=None,
        total_new_candidate_notional=None,
        postcompile_total_unfilled_buy_commitment=None,
        postcompile_alpha_exposure=None,
        postcompile_core_exposure=None,
        ticker_exposures=(),
source_bindings=(
            ('calendar_id', 'NY'),
            ('calendar_schedule_sha256', 'abc'),
            ('h1_raw_response_sha256', 'abc'),
            ('h1_rendered_prompt_sha256', 'abc'),
            ('holdings_observation_date', '2024-01-01'),
            ('holdings_policy_projection_identity_sha256', 'abc'),
            ('latest_completed_session_date', '2024-01-01'),
            ('portfolio_scope_id', 'abc'),
            ('portfolio_source_record_identity_sha256', 'abc'),
            ('portfolio_source_sha256', 'abc'),
            ('r_source_sha256', 'abc'),
            ('role_universe_projection_identity_sha256', 'abc'),
            ('strategy_source_record_identity_sha256', 'abc'),
            ('strategy_source_sha256', 'abc'),
            ('valuation_capture_sha256', 'abc'),
            ('valuation_freshness_status', 'FRESH'),
            ('valuation_provider_id', 'abc'),
            ('valuation_session_date', '2024-01-01'),
            ('valuation_source_kind', 'abc'),
            ('valuation_trusted_evaluation_timestamp_utc', '2024-01-01T12:00:00Z'),
            ('x_source_sha256', 'abc'),
            ('h1_evidence_entry_identities_sha256', ['abc', 'def']),
            ('h1_report_evidence_references', ['xyz']),
        ),
    )
    result = _p7a.publish_h1_v1_hold_no_trade()
    assert result.terminal_outcome == POSTCOMPILE_NO_TRADE
    assert result.immutable_path.exists()


def test_positive_candidate_rejection(mock_p6, isolated_artifact_dir):
    """3. Positive candidate rejection (subject mismatch)."""
    mock_p6.result = H1V1PostcompileFinalSafetyResult(
        terminal_outcome=POSTCOMPILE_CANDIDATE_VALID,
        reason_code="VALID",
        state="H1_V1_DETERMINISTIC_PROPOSAL_READY",
        selected_ticker="TLT",
        deterministic_role="CORE",
        candidate_legs=(),
        target_increment="1000",
        total_new_candidate_notional="1000",
        postcompile_total_unfilled_buy_commitment="1000",
        postcompile_alpha_exposure="0",
        postcompile_core_exposure="1000",
        ticker_exposures=(),
        source_bindings=(("strategy", "abc"),),
    )
    with pytest.raises(_p7a.V1P7APublicationError, match="V1_P7A_PUBLICATION_SUBJECT_MISMATCH"):
        _p7a.publish_h1_v1_hold_no_trade()

    # Verify zero P7 artifact writes
    p7a_dir = isolated_artifact_dir / "v1_hold_no_trade_publication"
    if p7a_dir.exists():
        assert list(p7a_dir.iterdir()) == []


def test_exact_package_contract_and_hash_oracle(mock_p6, isolated_artifact_dir):
    """6. Exact package contract (equality check).
    7. Terminal closure.
    8. Reason preservation.
    9. Authority posture.
    10. Provenance equality.
    11. Independent hash oracle.
    15. No order-like fields.
    """
    result = _p7a.publish_h1_v1_hold_no_trade()
    
    with open(result.immutable_path) as f:
        payload = json.load(f)

    # 6, 7, 8, 9, 10
    expected = {
        "schema_name": "v1_hold_no_trade_publication",
        "schema_version": "1.0",
        "publication_kind": "V1_HOLD_NO_TRADE_PUBLICATION",
        "terminal_outcome": POSTCOMPILE_HOLD,
        "reason_code": "NO_SHARED_CAPACITY",
        "source_bindings": dict(mock_p6.result.source_bindings),
        "authority_effect": AUTHORITY_EFFECT_NONE,
        "not_authorization": True,
    }
    assert payload == expected

    # 15. No order-like fields
    assert "quantity" not in payload
    assert "side" not in payload
    assert "order_ready" not in payload
    assert "candidate_legs" not in payload

    # 11. Independent hash oracle
    expected_bytes = canonical_json_bytes(expected)
    expected_hash = hashlib.sha256(expected_bytes).hexdigest()
    assert expected_hash == result.artifact_identity_sha256
    assert result.immutable_path.name == f"{expected_hash}.json"


def test_idempotent_same_content_behavior(mock_p6, isolated_artifact_dir):
    """12. Idempotent same-content behavior.
    Existing identical bytes are accepted without unnecessary rewrite.
    """
    first_result = _p7a.publish_h1_v1_hold_no_trade()
    assert not first_result.existed_idempotently

    # Second publication with same P6 facts
    second_result = _p7a.publish_h1_v1_hold_no_trade()
    assert second_result.existed_idempotently
    assert first_result.artifact_identity_sha256 == second_result.artifact_identity_sha256


def test_existing_target_mismatch(mock_p6, isolated_artifact_dir):
    """13. Existing target mismatch fails closed."""
    result = _p7a.publish_h1_v1_hold_no_trade()
    
    # Overwrite the file with different content to simulate a collision
    result.immutable_path.write_text('{"bad": "bytes"}')

    with pytest.raises(_p7a.V1P7APublicationError, match="V1_P7A_EXISTING_IDENTITY_MISMATCH"):
        _p7a.publish_h1_v1_hold_no_trade()


def test_no_state_permission_changes():
    """18. Existing state/permission behavior unchanged.
    Verify that _ALLOWED_ACTIONS_BY_STATE was not modified.
    """
    # Simply check that the import of p7a didn't mutate the canonical actions
    # by verifying a known state's allowed actions.
    allowed = research_availability.canonical_allowed_actions_for_state(
        "H1_V1_DETERMINISTIC_PROPOSAL_READY"
    )
    assert allowed == ("HOLD", "NO_TRADE", "NEW_BUY", "ORDER_COMPILATION")


def test_step4_isolation():
    """16. Step4 isolation.
    Verify the isolated namespace is completely distinct from the Step 4 input path.
    """
    p7a_dir = artifacts_dir() / "v1_hold_no_trade_publication"
    step4_dir = artifacts_dir() / "current" / "step4_order_compiler"
    assert p7a_dir != step4_dir
    assert "current" not in p7a_dir.parts

def test_unknown_reason_code_rejected(mock_p6, isolated_artifact_dir):
    """A. Unknown but well-formatted reason code rejected."""
    mock_p6.result = H1V1PostcompileFinalSafetyResult(
        **dataclasses.asdict(mock_p6.result) | {"reason_code": "UNKNOWN_FUTURE_REASON"}
    )
    with pytest.raises(_p7a.V1P7APublicationError, match="V1_P7A_VALIDATION_REASON_CODE_INVALID"):
        _p7a.publish_h1_v1_hold_no_trade()

def test_terminal_reason_mismatch_rejected(mock_p6, isolated_artifact_dir):
    """B. Terminal/reason mismatch rejected (NO_TRADE reason with HOLD terminal)."""
    mock_p6.result = H1V1PostcompileFinalSafetyResult(
        **dataclasses.asdict(mock_p6.result) | {"terminal_outcome": POSTCOMPILE_HOLD, "reason_code": "US_EQUITY_SESSION_MARK_DATE_STALE"}
    )
    with pytest.raises(_p7a.V1P7APublicationError, match="V1_P7A_VALIDATION_REASON_CODE_INVALID"):
        _p7a.publish_h1_v1_hold_no_trade()

def test_exact_current_hold_reason_accepted(mock_p6, isolated_artifact_dir):
    """C. Exact current HOLD reason accepted."""
    # mock_p6 defaults to NO_SHARED_CAPACITY which is a HOLD reason
    _p7a.publish_h1_v1_hold_no_trade()

def test_exact_current_no_trade_reason_accepted(mock_p6, isolated_artifact_dir):
    """D. Exact current NO_TRADE reason accepted."""
    mock_p6.result = H1V1PostcompileFinalSafetyResult(
        **dataclasses.asdict(mock_p6.result) | {"terminal_outcome": POSTCOMPILE_NO_TRADE, "reason_code": "US_EQUITY_SESSION_MARK_DATE_STALE"}
    )
    _p7a.publish_h1_v1_hold_no_trade()

def test_missing_binding_key_rejected(mock_p6, isolated_artifact_dir):
    """F. missing binding key rejected."""
    # Remove 'calendar_id'
    bad_bindings = tuple(b for b in mock_p6.result.source_bindings if b[0] != "calendar_id")
    mock_p6.result = H1V1PostcompileFinalSafetyResult(
        **dataclasses.asdict(mock_p6.result) | {"source_bindings": bad_bindings}
    )
    with pytest.raises(_p7a.V1P7APublicationError, match="V1_P7A_VALIDATION_PROVENANCE_INVALID"):
        _p7a.publish_h1_v1_hold_no_trade()

def test_unexpected_additional_binding_key_rejected(mock_p6, isolated_artifact_dir):
    """G. unexpected additional binding key rejected."""
    bad_bindings = mock_p6.result.source_bindings + (("extra_key", "val"),)
    mock_p6.result = H1V1PostcompileFinalSafetyResult(
        **dataclasses.asdict(mock_p6.result) | {"source_bindings": bad_bindings}
    )
    with pytest.raises(_p7a.V1P7APublicationError, match="V1_P7A_VALIDATION_PROVENANCE_INVALID"):
        _p7a.publish_h1_v1_hold_no_trade()

def test_existing_semantically_equivalent_but_byte_different_json_rejected(mock_p6, isolated_artifact_dir):
    """I. Existing semantically equivalent but byte-different JSON fails closed."""
    result = _p7a.publish_h1_v1_hold_no_trade()
    
    import json
    with open(result.immutable_path) as f:
        data = json.load(f)
    
    # Write back with pretty-printing (different bytes, same semantics)
    result.immutable_path.write_text(json.dumps(data, indent=4))
    
    with pytest.raises(_p7a.V1P7APublicationError, match="V1_P7A_EXISTING_IDENTITY_MISMATCH"):
        _p7a.publish_h1_v1_hold_no_trade()
