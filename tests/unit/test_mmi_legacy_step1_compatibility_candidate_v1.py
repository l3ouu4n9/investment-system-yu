from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import struct

import pytest
import yaml

from investment_orchestrator.common.paths import repo_root
from investment_orchestrator.common.schema_validation import (
    validate_artifact_schema,
)
from investment_orchestrator.mmi.analyst_visible_evidence_view_v2 import (
    build_mmi_analyst_visible_evidence_view_v2,
)
from investment_orchestrator.mmi.contracts import (
    MmiCapturedSource,
    MmiProjectionRunContext,
    MmiSourceRole,
    _begin_mmi_projection_run_with_clock,
)
from investment_orchestrator.mmi.evidence_bundle import (
    build_mmi_authenticated_evidence_bundle,
)
from investment_orchestrator.mmi.grounded_prompt_v2 import (
    build_mmi_grounded_prompt_v2,
)
from investment_orchestrator.mmi.legacy_step1_compatibility_candidate_v1 import (
    MmiLegacyStep1CompatibilityCandidateV1Error,
    _source_capability_statuses,
    build_mmi_legacy_step1_compatibility_candidate_v1,
    validate_mmi_legacy_step1_compatibility_candidate_v1,
)
from investment_orchestrator.mmi.policy_projection import (
    build_mmi_policy_projection,
)
from investment_orchestrator.mmi.portfolio_projection import (
    build_mmi_portfolio_snapshot_projection,
)
from investment_orchestrator.mmi.raw_response_envelope_v2 import (
    build_mmi_raw_response_envelope_v2,
)
from investment_orchestrator.mmi.source_capture import (
    _capture_mmi_source_at_root,
    capture_current_mmi_source,
)
from investment_orchestrator.mmi.validated_grounded_analysis_response_v2 import (
    build_mmi_validated_grounded_analysis_response_v2,
)


CANDIDATE_DOMAIN = b"mmi_legacy_step1_compatibility_candidate_v1\0"
R2_DOMAIN = b"mmi_validated_grounded_analysis_response_v2\0"
CANDIDATE_IDENTITY_FIELD = (
    "legacy_step1_compatibility_candidate_identity_sha256"
)
R2_IDENTITY_FIELD = "validated_grounded_analysis_response_identity_sha256"
SCHEMA_NAME = "mmi_legacy_step1_compatibility_candidate_v1.schema.json"
EVALUATION_TIME = datetime(2026, 7, 31, 12, tzinfo=timezone.utc)


class _FixedClock:
    def now_utc(self) -> datetime:
        return EVALUATION_TIME


class _Inputs:
    def __init__(
        self,
        *,
        policy: dict[str, object],
        policy_source: MmiCapturedSource,
        portfolio: dict[str, object] | None,
        portfolio_source: MmiCapturedSource | None,
        evidence: dict[str, object],
        run_context: MmiProjectionRunContext,
        view: dict[str, object],
        prompt: dict[str, object],
        envelope: dict[str, object],
        response: dict[str, object],
    ) -> None:
        self.policy = policy
        self.policy_source = policy_source
        self.portfolio = portfolio
        self.portfolio_source = portfolio_source
        self.evidence = evidence
        self.run_context = run_context
        self.view = view
        self.prompt = prompt
        self.envelope = envelope
        self.response = response


def _canonical(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def _record_identity(
    domain: bytes,
    value: dict[str, object],
    identity_field: str,
) -> str:
    preimage = deepcopy(value)
    preimage.pop(identity_field, None)
    canonical_bytes = _canonical(preimage)
    return hashlib.sha256(
        domain
        + struct.pack(">Q", len(canonical_bytes))
        + canonical_bytes
    ).hexdigest()


def _context_kwargs(inputs: _Inputs) -> dict[str, object]:
    return {
        "validated_grounded_analysis_response": deepcopy(inputs.response),
        "raw_response_envelope": deepcopy(inputs.envelope),
        "evidence_bundle": deepcopy(inputs.evidence),
        "policy_projection": deepcopy(inputs.policy),
        "policy_source": inputs.policy_source,
        "portfolio_projection": deepcopy(inputs.portfolio),
        "portfolio_source": inputs.portfolio_source,
        "run_context": inputs.run_context,
    }


def _candidate(inputs: _Inputs) -> dict[str, object]:
    return build_mmi_legacy_step1_compatibility_candidate_v1(
        **_context_kwargs(inputs),
    )


def _payload(
    *,
    view: dict[str, object],
    prompt: dict[str, object],
    supported_rationale_characters: int | None = None,
    large_analysis: bool = False,
) -> dict[str, object]:
    policy_view = view["policy_view"]
    assert type(policy_view) is dict
    instruments = policy_view["analysis_instruments"]
    assert type(instruments) is list
    rows = []
    for index, item in enumerate(instruments, start=1):
        assert type(item) is dict
        ticker = item["ticker"]
        assert type(ticker) is str
        if supported_rationale_characters is None:
            rows.append(
                {
                    "ticker": ticker,
                    "evidence_status": "UNAVAILABLE",
                    "rationale_12m_plus": None,
                    "references": [],
                }
            )
        else:
            rows.append(
                {
                    "ticker": ticker,
                    "evidence_status": "EVIDENCE_SUPPORTED",
                    "rationale_12m_plus": (
                        "R" * supported_rationale_characters
                    ),
                    "references": [f"POLICY.INSTRUMENT.{index:04d}"],
                }
            )
    analysis_items = (
        [
            {
                "text": "E" * 2000,
                "references": ["VIEW.EVALUATION_TIMESTAMP"],
                "hypothesis": False,
            }
            for _ in range(12)
        ]
        if large_analysis
        else []
    )
    context = prompt["prompt_context_binding_sha256"]
    assert type(context) is str
    return {
        "response_schema_version": "mmi_grounded_analysis_response_v2",
        "prompt_context_binding_sha256": context,
        "analysis_status": (
            "QUALITATIVE_ANALYSIS_PROVIDED"
            if supported_rationale_characters is not None
            else "INSUFFICIENT_EVIDENCE"
        ),
        "instrument_views": rows,
        "anchor_associations_status": "UNAVAILABLE",
        "scheduled_events_status": "UNAVAILABLE",
        "regime_observation_status": "UNAVAILABLE",
        "evidence_observations": analysis_items,
        "risks": [],
        "uncertainties": [],
        "contradictions": [],
        "research_questions": [],
        "summary": {
            "text": "S" * (4000 if large_analysis else 48),
            "references": ["VIEW.EVALUATION_TIMESTAMP"],
            "hypothesis": False,
        },
    }


def _build_inputs(*, portfolio_present: bool) -> _Inputs:
    raw = (
        repo_root() / "inputs/current/strategy_settings.yaml"
    ).read_bytes()
    policy_capture = capture_current_mmi_source(
        MmiSourceRole.STRATEGY_SETTINGS,
        expected_source_sha256=hashlib.sha256(raw).hexdigest(),
    )
    assert policy_capture.valid, policy_capture.reason_codes
    assert policy_capture.source is not None
    run_context = _begin_mmi_projection_run_with_clock(_FixedClock())
    policy_result = build_mmi_policy_projection(
        policy_capture.source,
        run_context=run_context,
    )
    assert policy_result.valid, policy_result.reason_codes
    assert policy_result.projection is not None
    policy = dict(policy_result.projection)

    portfolio: dict[str, object] | None = None
    portfolio_source: MmiCapturedSource | None = None
    if portfolio_present:
        portfolio_raw = (
            repo_root() / "inputs/current/portfolio_snapshot.txt"
        ).read_bytes()
        portfolio_capture = capture_current_mmi_source(
            MmiSourceRole.PORTFOLIO_SNAPSHOT,
            expected_source_sha256=hashlib.sha256(
                portfolio_raw
            ).hexdigest(),
        )
        assert portfolio_capture.valid, portfolio_capture.reason_codes
        assert portfolio_capture.source is not None
        portfolio_result = build_mmi_portfolio_snapshot_projection(
            portfolio_capture.source,
            policy_projection=deepcopy(policy),
            policy_source=policy_capture.source,
            run_context=run_context,
        )
        assert portfolio_result.valid, portfolio_result.reason_codes
        assert portfolio_result.projection is not None
        portfolio = dict(portfolio_result.projection)
        portfolio_source = portfolio_capture.source

    evidence_result = build_mmi_authenticated_evidence_bundle(
        policy_projection=deepcopy(policy),
        policy_source=policy_capture.source,
        portfolio_projection=deepcopy(portfolio),
        portfolio_source=portfolio_source,
        run_context=run_context,
    )
    assert evidence_result.valid, evidence_result.reason_codes
    assert evidence_result.projection is not None
    evidence = dict(evidence_result.projection)
    view_result = build_mmi_analyst_visible_evidence_view_v2(
        evidence_bundle=deepcopy(evidence),
        policy_projection=deepcopy(policy),
        policy_source=policy_capture.source,
        portfolio_projection=deepcopy(portfolio),
        portfolio_source=portfolio_source,
        run_context=run_context,
    )
    assert view_result.valid, view_result.reason_codes
    assert view_result.projection is not None
    view = dict(view_result.projection)
    prompt = build_mmi_grounded_prompt_v2(
        analyst_visible_evidence_view=deepcopy(view),
        evidence_bundle=deepcopy(evidence),
        policy_projection=deepcopy(policy),
        policy_source=policy_capture.source,
        portfolio_projection=deepcopy(portfolio),
        portfolio_source=portfolio_source,
        run_context=run_context,
    )
    payload = _payload(view=view, prompt=prompt)
    raw_response = json.dumps(
        payload,
        ensure_ascii=False,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    envelope = build_mmi_raw_response_envelope_v2(
        grounded_prompt=deepcopy(prompt),
        raw_response_bytes=raw_response,
        evidence_bundle=deepcopy(evidence),
        policy_projection=deepcopy(policy),
        policy_source=policy_capture.source,
        portfolio_projection=deepcopy(portfolio),
        portfolio_source=portfolio_source,
        run_context=run_context,
    )
    response = build_mmi_validated_grounded_analysis_response_v2(
        raw_response_envelope=deepcopy(envelope),
        evidence_bundle=deepcopy(evidence),
        policy_projection=deepcopy(policy),
        policy_source=policy_capture.source,
        portfolio_projection=deepcopy(portfolio),
        portfolio_source=portfolio_source,
        run_context=run_context,
    )
    return _Inputs(
        policy=policy,
        policy_source=policy_capture.source,
        portfolio=portfolio,
        portfolio_source=portfolio_source,
        evidence=evidence,
        run_context=run_context,
        view=view,
        prompt=prompt,
        envelope=envelope,
        response=response,
    )


@pytest.fixture(scope="module")
def absent_inputs() -> _Inputs:
    return _build_inputs(portfolio_present=False)


@pytest.fixture(scope="module")
def present_inputs() -> _Inputs:
    return _build_inputs(portfolio_present=True)


def test_portfolio_absent_build_validate_and_repeat_are_exact(
    absent_inputs: _Inputs,
) -> None:
    first = _candidate(absent_inputs)
    second = _candidate(absent_inputs)
    assert first == second
    assert _canonical(first) == _canonical(second)
    assert first[CANDIDATE_IDENTITY_FIELD] == _record_identity(
        CANDIDATE_DOMAIN,
        first,
        CANDIDATE_IDENTITY_FIELD,
    )
    validate_artifact_schema(first, schema_name=SCHEMA_NAME)
    assert validate_mmi_legacy_step1_compatibility_candidate_v1(
        value=first,
        **_context_kwargs(absent_inputs),
    ) == first


def test_portfolio_present_build_and_validation_are_valid(
    present_inputs: _Inputs,
) -> None:
    value = _candidate(present_inputs)
    assert validate_mmi_legacy_step1_compatibility_candidate_v1(
        value=value,
        **_context_kwargs(present_inputs),
    ) == value


def test_projection_uses_only_av2_order_roles_and_r2_qualitative_fields(
    absent_inputs: _Inputs,
) -> None:
    value = _candidate(absent_inputs)
    policy_view = absent_inputs.view["policy_view"]
    payload = absent_inputs.response["response_payload"]
    assert type(policy_view) is dict
    assert type(payload) is dict
    view_rows = policy_view["analysis_instruments"]
    response_rows = payload["instrument_views"]
    candidate_rows = value["ordered_instrument_assessments"]
    assert type(view_rows) is list
    assert type(response_rows) is list
    assert type(candidate_rows) is list
    assert len(view_rows) == len(response_rows) == len(candidate_rows)
    for view_row, response_row, candidate_row in zip(
        view_rows,
        response_rows,
        candidate_rows,
        strict=True,
    ):
        assert type(view_row) is dict
        assert type(response_row) is dict
        assert type(candidate_row) is dict
        assert candidate_row == {
            "ticker": view_row["ticker"],
            "policy_role": view_row["policy_role"],
            "evidence_status": response_row["evidence_status"],
            "rationale_12m_plus": response_row["rationale_12m_plus"],
            "references": response_row["references"],
        }


def test_build_returns_snapshot_independent_of_later_upstream_mutation(
    absent_inputs: _Inputs,
) -> None:
    kwargs = _context_kwargs(absent_inputs)
    response = kwargs["validated_grounded_analysis_response"]
    assert type(response) is dict
    value = build_mmi_legacy_step1_compatibility_candidate_v1(**kwargs)
    before = deepcopy(value)
    payload = response["response_payload"]
    assert type(payload) is dict
    summary = payload["summary"]
    assert type(summary) is dict
    summary["text"] = "Caller mutation after return."
    assert value == before


def test_representative_altered_envelope_is_rejected_by_upstream_owner(
    absent_inputs: _Inputs,
) -> None:
    kwargs = _context_kwargs(absent_inputs)
    envelope = kwargs["raw_response_envelope"]
    assert type(envelope) is dict
    envelope["raw_response_sha256"] = "f" * 64
    with pytest.raises(MmiLegacyStep1CompatibilityCandidateV1Error):
        build_mmi_legacy_step1_compatibility_candidate_v1(**kwargs)


def test_resealed_nonexpected_r2_is_rejected_by_upstream_owner(
    absent_inputs: _Inputs,
) -> None:
    kwargs = _context_kwargs(absent_inputs)
    response = kwargs["validated_grounded_analysis_response"]
    assert type(response) is dict
    payload = response["response_payload"]
    assert type(payload) is dict
    summary = payload["summary"]
    assert type(summary) is dict
    summary["text"] = "Schema-valid independently resealed substitution."
    response[R2_IDENTITY_FIELD] = _record_identity(
        R2_DOMAIN,
        response,
        R2_IDENTITY_FIELD,
    )
    with pytest.raises(MmiLegacyStep1CompatibilityCandidateV1Error):
        build_mmi_legacy_step1_compatibility_candidate_v1(**kwargs)


@pytest.mark.parametrize(
    "mutation",
    ["missing", "duplicate", "foreign", "reordered", "role"],
)
def test_complete_expected_equality_rejects_instrument_mutations(
    absent_inputs: _Inputs,
    mutation: str,
) -> None:
    value = _candidate(absent_inputs)
    rows = value["ordered_instrument_assessments"]
    assert type(rows) is list and len(rows) >= 4
    if mutation == "missing":
        removable_index = next(
            index
            for index, row in enumerate(rows)
            if type(row) is dict
            and row.get("policy_role") == "APPROVED_EXTENDED"
        )
        rows.pop(removable_index)
    elif mutation == "duplicate":
        assert type(rows[0]) is dict and type(rows[-1]) is dict
        rows[-1]["ticker"] = rows[0]["ticker"]
    elif mutation == "foreign":
        assert type(rows[-1]) is dict
        rows[-1]["ticker"] = "ZZZFOREIGN"
    elif mutation == "reordered":
        rows[0], rows[1] = rows[1], rows[0]
    else:
        assert type(rows[0]) is dict
        rows[0]["policy_role"] = "APPROVED_EXTENDED"
    value[CANDIDATE_IDENTITY_FIELD] = _record_identity(
        CANDIDATE_DOMAIN,
        value,
        CANDIDATE_IDENTITY_FIELD,
    )
    with pytest.raises(MmiLegacyStep1CompatibilityCandidateV1Error):
        validate_mmi_legacy_step1_compatibility_candidate_v1(
            value=value,
            **_context_kwargs(absent_inputs),
        )


@pytest.mark.parametrize(
    "field",
    [
        "analyst_visible_evidence_view_identity_sha256",
        "validated_grounded_analysis_response_identity_sha256",
    ],
)
def test_complete_expected_equality_rejects_altered_provenance(
    absent_inputs: _Inputs,
    field: str,
) -> None:
    value = _candidate(absent_inputs)
    provenance = value["provenance"]
    assert type(provenance) is dict
    provenance[field] = "f" * 64
    value[CANDIDATE_IDENTITY_FIELD] = _record_identity(
        CANDIDATE_DOMAIN,
        value,
        CANDIDATE_IDENTITY_FIELD,
    )
    with pytest.raises(MmiLegacyStep1CompatibilityCandidateV1Error):
        validate_mmi_legacy_step1_compatibility_candidate_v1(
            value=value,
            **_context_kwargs(absent_inputs),
        )


def test_intermediate_provenance_identity_is_not_accepted(
    absent_inputs: _Inputs,
) -> None:
    value = _candidate(absent_inputs)
    provenance = value["provenance"]
    assert type(provenance) is dict
    provenance["raw_response_envelope_identity_sha256"] = "f" * 64
    with pytest.raises(MmiLegacyStep1CompatibilityCandidateV1Error):
        validate_mmi_legacy_step1_compatibility_candidate_v1(
            value=value,
            **_context_kwargs(absent_inputs),
        )


def test_source_capability_statuses_are_exact_and_alteration_fails(
    absent_inputs: _Inputs,
) -> None:
    value = _candidate(absent_inputs)
    statuses = value["source_capability_statuses"]
    assert statuses == {
        "anchor_associations_status": "UNAVAILABLE",
        "scheduled_events_status": "UNAVAILABLE",
        "regime_inputs_status": "UNAVAILABLE",
        "target_weights_absence_reason": (
            "POLICY_METHOD_HAS_NO_TARGET_WEIGHTS"
        ),
    }
    assert "UNRESOLVED_LEGACY_CONTRACT" not in _canonical(value).decode()
    assert _source_capability_statuses(
        view={
            "research_component_statuses": {
                "anchor_associations": "AV2_ANCHOR_DO_NOT_USE",
                "scheduled_events": "AV2_EVENTS_DO_NOT_USE",
                "regime_inputs": "AV2_REGIME_DO_NOT_USE",
            },
            "policy_view": {
                "target_weights_absence_reason": "AV2_TARGET_OWNER",
            },
        },
        response_payload={
            "anchor_associations_status": "R2_ANCHOR_OWNER",
            "scheduled_events_status": "R2_EVENTS_OWNER",
            "regime_observation_status": "R2_REGIME_OWNER",
        },
    ) == {
        "anchor_associations_status": "R2_ANCHOR_OWNER",
        "scheduled_events_status": "R2_EVENTS_OWNER",
        "regime_inputs_status": "R2_REGIME_OWNER",
        "target_weights_absence_reason": "AV2_TARGET_OWNER",
    }
    assert type(statuses) is dict
    statuses["regime_inputs_status"] = "AVAILABLE"
    value[CANDIDATE_IDENTITY_FIELD] = _record_identity(
        CANDIDATE_DOMAIN,
        value,
        CANDIDATE_IDENTITY_FIELD,
    )
    with pytest.raises(MmiLegacyStep1CompatibilityCandidateV1Error):
        validate_mmi_legacy_step1_compatibility_candidate_v1(
            value=value,
            **_context_kwargs(absent_inputs),
        )


def test_nested_qualitative_mutation_is_identity_bound_and_nonexpected(
    absent_inputs: _Inputs,
) -> None:
    value = _candidate(absent_inputs)
    summary = value["summary"]
    assert type(summary) is dict
    summary["text"] = "Altered qualitative summary."
    original_identity = value[CANDIDATE_IDENTITY_FIELD]
    assert _record_identity(
        CANDIDATE_DOMAIN,
        value,
        CANDIDATE_IDENTITY_FIELD,
    ) != original_identity
    value[CANDIDATE_IDENTITY_FIELD] = _record_identity(
        CANDIDATE_DOMAIN,
        value,
        CANDIDATE_IDENTITY_FIELD,
    )
    with pytest.raises(MmiLegacyStep1CompatibilityCandidateV1Error):
        validate_mmi_legacy_step1_compatibility_candidate_v1(
            value=value,
            **_context_kwargs(absent_inputs),
        )


def _large_settings() -> dict[str, object]:
    core = ["C00000000000001"]
    satellite = ["S00000000000001"]
    extended = [f"E{index:014d}" for index in range(1, 255)]
    return {
        "as_of": "2026-07-30",
        "run_timestamp_et": "2026-07-30 10:00 ET",
        "benchmark": core[0],
        "hard_cap_open_orders_budget": 38211.29,
        "target_new_buy_budget_this_run": 12000.00,
        "relative_rotation_enabled": True,
        "relative_rotation_guardrails": {
            "require_same_role_for_rotation": True,
            "min_score_gap_to_rotate": 2,
            "do_not_rotate_if_current_holding_still_role_valid": True,
            "no_rotation_on_one_rank_change_only": True,
        },
        "core_universe": core,
        "satellite_universe": satellite,
        "user_approved_extended_etf_static_list": extended,
        "user_approved_extended_etf_theme_map": {
            ticker: {"theme_bucket": f"theme_{index:04d}"}
            for index, ticker in enumerate(extended, start=1)
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
            "require_distinct_theme_buckets_when_multiple_extended_etfs": (
                True
            ),
        },
    }


def _large_source_bound_inputs(root: Path) -> _Inputs:
    raw = yaml.safe_dump(
        _large_settings(),
        sort_keys=False,
        allow_unicode=True,
    ).encode("utf-8")
    path = root / "inputs/current/strategy_settings.yaml"
    path.parent.mkdir(parents=True)
    path.write_bytes(raw)
    capture = _capture_mmi_source_at_root(
        root,
        role=MmiSourceRole.STRATEGY_SETTINGS,
        expected_source_sha256=hashlib.sha256(raw).hexdigest(),
    )
    assert capture.valid, capture.reason_codes
    assert capture.source is not None
    run_context = _begin_mmi_projection_run_with_clock(_FixedClock())
    policy_result = build_mmi_policy_projection(
        capture.source,
        run_context=run_context,
    )
    assert policy_result.valid, policy_result.reason_codes
    assert policy_result.projection is not None
    policy = dict(policy_result.projection)
    evidence_result = build_mmi_authenticated_evidence_bundle(
        policy_projection=deepcopy(policy),
        policy_source=capture.source,
        portfolio_projection=None,
        portfolio_source=None,
        run_context=run_context,
    )
    assert evidence_result.valid, evidence_result.reason_codes
    assert evidence_result.projection is not None
    evidence = dict(evidence_result.projection)
    view_result = build_mmi_analyst_visible_evidence_view_v2(
        evidence_bundle=deepcopy(evidence),
        policy_projection=deepcopy(policy),
        policy_source=capture.source,
        portfolio_projection=None,
        portfolio_source=None,
        run_context=run_context,
    )
    assert view_result.valid, view_result.reason_codes
    assert view_result.projection is not None
    view = dict(view_result.projection)
    prompt = build_mmi_grounded_prompt_v2(
        analyst_visible_evidence_view=deepcopy(view),
        evidence_bundle=deepcopy(evidence),
        policy_projection=deepcopy(policy),
        policy_source=capture.source,
        portfolio_projection=None,
        portfolio_source=None,
        run_context=run_context,
    )
    payload = _payload(
        view=view,
        prompt=prompt,
        supported_rationale_characters=600,
        large_analysis=True,
    )
    raw_response = json.dumps(
        payload,
        ensure_ascii=False,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    envelope = build_mmi_raw_response_envelope_v2(
        grounded_prompt=deepcopy(prompt),
        raw_response_bytes=raw_response,
        evidence_bundle=deepcopy(evidence),
        policy_projection=deepcopy(policy),
        policy_source=capture.source,
        portfolio_projection=None,
        portfolio_source=None,
        run_context=run_context,
    )
    response = build_mmi_validated_grounded_analysis_response_v2(
        raw_response_envelope=deepcopy(envelope),
        evidence_bundle=deepcopy(evidence),
        policy_projection=deepcopy(policy),
        policy_source=capture.source,
        portfolio_projection=None,
        portfolio_source=None,
        run_context=run_context,
    )
    return _Inputs(
        policy=policy,
        policy_source=capture.source,
        portfolio=None,
        portfolio_source=None,
        evidence=evidence,
        run_context=run_context,
        view=view,
        prompt=prompt,
        envelope=envelope,
        response=response,
    )


def test_large_public_256_instrument_fixture_builds_below_ceiling(
    tmp_path: Path,
) -> None:
    inputs = _large_source_bound_inputs(tmp_path)
    value = _candidate(inputs)
    rows = value["ordered_instrument_assessments"]
    assert type(rows) is list and len(rows) == 256
    roles = [row["policy_role"] for row in rows if type(row) is dict]
    assert roles.count("CORE") == 1
    assert roles.count("SATELLITE") == 1
    assert roles.count("APPROVED_EXTENDED") == 254
    size = len(_canonical(value))
    assert 200_000 < size < 262_144
    assert value[CANDIDATE_IDENTITY_FIELD] == _record_identity(
        CANDIDATE_DOMAIN,
        value,
        CANDIDATE_IDENTITY_FIELD,
    )
    assert validate_mmi_legacy_step1_compatibility_candidate_v1(
        value=value,
        **_context_kwargs(inputs),
    ) == value
