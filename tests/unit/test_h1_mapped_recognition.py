from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, fields
from datetime import datetime, timezone
import inspect
import json
from pathlib import Path

import pytest

from investment_orchestrator.common import schema_validation
from investment_orchestrator.mmi.analyst_visible_evidence_view_v2 import (
    build_mmi_analyst_visible_evidence_view_v2,
)
from investment_orchestrator.mmi.contracts import (
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
    build_mmi_legacy_step1_compatibility_candidate_v1,
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
from investment_orchestrator.mmi.validated_grounded_analysis_response_v2 import (
    build_mmi_validated_grounded_analysis_response_v2,
)
from investment_orchestrator.offline.mmi_h1_legacy_step1_mapping_report_v1 import (
    build_mmi_h1_legacy_step1_mapping_report_v1,
)
from investment_orchestrator.research import h1_mapped_recognition as bridge
from investment_orchestrator.research.h1_mapped_recognition import (
    H1MappedRecognitionError,
    H1MappedRecognitionFacts,
    build_validated_h1_mapped_recognition_facts,
)
from investment_orchestrator.validators.validate_research_handoff import (
    LEGACY_RESEARCH_HANDOFF_STRICT_VALIDATOR_CONTRACT_VERSION,
)

import _mmi_hermetic_source_checkout as hermetic


EVALUATION_TIME = datetime(2026, 7, 31, 12, tzinfo=timezone.utc)
EXPECTED_FACT_FIELDS = (
    "source_kind",
    "mapping_schema_version",
    "mapping_report_identity_sha256",
    "role_map_version",
    "target_legacy_validator_contract_version",
    "strategy_settings_source_record_identity_sha256",
    "policy_projection_identity_sha256",
    "universe_projection_identity_sha256",
    "portfolio_source_record_identity_sha256",
    "portfolio_projection_identity_sha256",
    "evidence_bundle_identity_sha256",
    "analyst_visible_evidence_view_identity_sha256",
    "grounded_prompt_artifact_identity_sha256",
    "prompt_context_binding_sha256",
    "raw_response_envelope_identity_sha256",
    "raw_response_sha256",
    "validated_grounded_analysis_response_identity_sha256",
    "legacy_step1_compatibility_candidate_identity_sha256",
    "policy_as_of_date",
    "policy_source_run_timestamp_utc",
    "portfolio_source_date",
    "context_evaluation_timestamp_utc",
)


class _FixedClock:
    def now_utc(self) -> datetime:
        return EVALUATION_TIME


@dataclass
class _Inputs:
    mapping_report: dict[str, object]
    candidate: dict[str, object]
    response: dict[str, object]
    envelope: dict[str, object]
    evidence: dict[str, object]
    policy: dict[str, object]
    policy_source: object
    portfolio: dict[str, object]
    portfolio_source: object
    run_context: MmiProjectionRunContext


def _payload(
    *,
    view: dict[str, object],
    prompt: dict[str, object],
) -> dict[str, object]:
    policy_view = view["policy_view"]
    assert type(policy_view) is dict
    instruments = policy_view["analysis_instruments"]
    assert type(instruments) is list
    rows: list[dict[str, object]] = []
    for index, item in enumerate(instruments, start=1):
        assert type(item) is dict
        rows.append(
            {
                "ticker": item["ticker"],
                "evidence_status": "EVIDENCE_SUPPORTED",
                "rationale_12m_plus": "Evidence-linked qualitative rationale.",
                "references": [f"POLICY.INSTRUMENT.{index:04d}"],
            }
        )
    context = prompt["prompt_context_binding_sha256"]
    assert type(context) is str
    return {
        "response_schema_version": "mmi_grounded_analysis_response_v2",
        "prompt_context_binding_sha256": context,
        "analysis_status": "QUALITATIVE_ANALYSIS_PROVIDED",
        "instrument_views": rows,
        "anchor_associations_status": "UNAVAILABLE",
        "scheduled_events_status": "UNAVAILABLE",
        "regime_observation_status": "UNAVAILABLE",
        "evidence_observations": [
            {
                "text": "Evidence observation remains report-only.",
                "references": ["VIEW.EVALUATION_TIMESTAMP"],
                "hypothesis": False,
            }
        ],
        "risks": [],
        "uncertainties": [],
        "contradictions": [],
        "research_questions": [],
        "summary": {
            "text": "Research-only summary.",
            "references": ["VIEW.EVALUATION_TIMESTAMP"],
            "hypothesis": False,
        },
    }


def _inputs(
    tmp_path_factory: pytest.TempPathFactory,
    *,
    name: str = "h1-mapped-recognition",
    as_of: str = hermetic.DEFAULT_AS_OF,
    run_timestamp_et: str | None = hermetic.DEFAULT_RUN_TIMESTAMP_ET,
    portfolio_updated: str = hermetic.DEFAULT_PORTFOLIO_UPDATED,
) -> _Inputs:
    checkout = hermetic.build_checkout(
        tmp_path_factory,
        name,
        as_of=as_of,
        run_timestamp_et=(
            hermetic.DEFAULT_RUN_TIMESTAMP_ET
            if run_timestamp_et is None
            else run_timestamp_et
        ),
        updated=portfolio_updated,
    )
    policy_source = checkout.policy_source
    if run_timestamp_et is None:
        settings_without_timestamp = b"".join(
            line
            for line in checkout.strategy_settings_raw.splitlines(
                keepends=True
            )
            if not line.startswith(b"run_timestamp_et:")
        )
        policy_source = hermetic.capture_source(
            checkout.root,
            role=MmiSourceRole.STRATEGY_SETTINGS,
            raw=settings_without_timestamp,
        )
    run_context = _begin_mmi_projection_run_with_clock(_FixedClock())
    policy_result = build_mmi_policy_projection(
        policy_source, run_context=run_context
    )
    assert policy_result.valid, policy_result.reason_codes
    assert policy_result.projection is not None
    policy = dict(policy_result.projection)
    portfolio_result = build_mmi_portfolio_snapshot_projection(
        checkout.portfolio_source,
        policy_projection=deepcopy(policy),
        policy_source=policy_source,
        run_context=run_context,
    )
    assert portfolio_result.valid, portfolio_result.reason_codes
    assert portfolio_result.projection is not None
    portfolio = dict(portfolio_result.projection)
    evidence_result = build_mmi_authenticated_evidence_bundle(
        policy_projection=deepcopy(policy),
        policy_source=policy_source,
        portfolio_projection=deepcopy(portfolio),
        portfolio_source=checkout.portfolio_source,
        run_context=run_context,
    )
    assert evidence_result.valid, evidence_result.reason_codes
    assert evidence_result.projection is not None
    evidence = dict(evidence_result.projection)
    view_result = build_mmi_analyst_visible_evidence_view_v2(
        evidence_bundle=deepcopy(evidence),
        policy_projection=deepcopy(policy),
        policy_source=policy_source,
        portfolio_projection=deepcopy(portfolio),
        portfolio_source=checkout.portfolio_source,
        run_context=run_context,
    )
    assert view_result.valid, view_result.reason_codes
    assert view_result.projection is not None
    view = dict(view_result.projection)
    prompt = build_mmi_grounded_prompt_v2(
        analyst_visible_evidence_view=deepcopy(view),
        evidence_bundle=deepcopy(evidence),
        policy_projection=deepcopy(policy),
        policy_source=policy_source,
        portfolio_projection=deepcopy(portfolio),
        portfolio_source=checkout.portfolio_source,
        run_context=run_context,
    )
    raw_response = json.dumps(
        _payload(view=view, prompt=prompt),
        ensure_ascii=False,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    envelope = build_mmi_raw_response_envelope_v2(
        grounded_prompt=deepcopy(prompt),
        raw_response_bytes=raw_response,
        evidence_bundle=deepcopy(evidence),
        policy_projection=deepcopy(policy),
        policy_source=policy_source,
        portfolio_projection=deepcopy(portfolio),
        portfolio_source=checkout.portfolio_source,
        run_context=run_context,
    )
    response = build_mmi_validated_grounded_analysis_response_v2(
        raw_response_envelope=deepcopy(envelope),
        evidence_bundle=deepcopy(evidence),
        policy_projection=deepcopy(policy),
        policy_source=policy_source,
        portfolio_projection=deepcopy(portfolio),
        portfolio_source=checkout.portfolio_source,
        run_context=run_context,
    )
    candidate = build_mmi_legacy_step1_compatibility_candidate_v1(
        validated_grounded_analysis_response=deepcopy(response),
        raw_response_envelope=deepcopy(envelope),
        evidence_bundle=deepcopy(evidence),
        policy_projection=deepcopy(policy),
        policy_source=policy_source,
        portfolio_projection=deepcopy(portfolio),
        portfolio_source=checkout.portfolio_source,
        run_context=run_context,
    )
    mapping_report = build_mmi_h1_legacy_step1_mapping_report_v1(
        legacy_step1_compatibility_candidate=deepcopy(candidate),
        validated_grounded_analysis_response=deepcopy(response),
        raw_response_envelope=deepcopy(envelope),
        evidence_bundle=deepcopy(evidence),
        policy_projection=deepcopy(policy),
        policy_source=policy_source,
        portfolio_projection=deepcopy(portfolio),
        portfolio_source=checkout.portfolio_source,
        run_context=run_context,
    )
    return _Inputs(
        mapping_report=mapping_report,
        candidate=candidate,
        response=response,
        envelope=envelope,
        evidence=evidence,
        policy=policy,
        policy_source=policy_source,
        portfolio=portfolio,
        portfolio_source=checkout.portfolio_source,
        run_context=run_context,
    )


def _kwargs(inputs: _Inputs) -> dict[str, object]:
    return {
        "mapping_report": deepcopy(inputs.mapping_report),
        "legacy_step1_compatibility_candidate": deepcopy(inputs.candidate),
        "validated_grounded_analysis_response": deepcopy(inputs.response),
        "raw_response_envelope": deepcopy(inputs.envelope),
        "evidence_bundle": deepcopy(inputs.evidence),
        "policy_projection": deepcopy(inputs.policy),
        "policy_source": inputs.policy_source,
        "portfolio_projection": deepcopy(inputs.portfolio),
        "portfolio_source": inputs.portfolio_source,
        "run_context": inputs.run_context,
    }


def _build(inputs: _Inputs) -> H1MappedRecognitionFacts:
    return build_validated_h1_mapped_recognition_facts(**_kwargs(inputs))


def _facts_as_dict(facts: H1MappedRecognitionFacts) -> dict[str, object]:
    return {field.name: getattr(facts, field.name) for field in fields(facts)}


def test_valid_factory_returns_exact_ephemeral_facts_and_reuses_pr2_validator(
    tmp_path_factory: pytest.TempPathFactory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    inputs = _inputs(tmp_path_factory)
    calls = 0
    real_validate = bridge.validate_mmi_h1_legacy_step1_mapping_report_v1

    def counted_validate(**kwargs: object) -> dict[str, object]:
        nonlocal calls
        calls += 1
        return real_validate(**kwargs)

    monkeypatch.setattr(
        bridge,
        "validate_mmi_h1_legacy_step1_mapping_report_v1",
        counted_validate,
    )
    facts = _build(inputs)
    assert calls == 1
    chain = inputs.mapping_report["upstream_identity_chain"]
    assert type(chain) is dict
    expected = {
        "source_kind": "H1_ROLE_MAPPED",
        "mapping_schema_version": "mmi_h1_legacy_step1_mapping_report_v1",
        "mapping_report_identity_sha256": inputs.mapping_report[
            "mapping_report_identity_sha256"
        ],
        "role_map_version": "h1_legacy_step1_role_map_v1",
        "target_legacy_validator_contract_version": (
            LEGACY_RESEARCH_HANDOFF_STRICT_VALIDATOR_CONTRACT_VERSION
        ),
        **chain,
        "raw_response_sha256": inputs.envelope["raw_response_sha256"],
        "policy_as_of_date": inputs.policy["policy_as_of_date"],
        "policy_source_run_timestamp_utc": inputs.policy[
            "source_run_timestamp_utc"
        ],
        "portfolio_source_date": inputs.portfolio["portfolio_source_date"],
        "context_evaluation_timestamp_utc": inputs.run_context.evaluation_timestamp_utc,
    }
    assert tuple(_facts_as_dict(facts)) == EXPECTED_FACT_FIELDS
    assert _facts_as_dict(facts) == expected
    assert _build(inputs) == facts


def test_facts_are_factory_only_immutable_and_contain_no_authority_fields(
    tmp_path_factory: pytest.TempPathFactory,
) -> None:
    assert tuple(field.name for field in fields(H1MappedRecognitionFacts)) == (
        EXPECTED_FACT_FIELDS
    )
    with pytest.raises(TypeError):
        H1MappedRecognitionFacts()
    facts = _build(_inputs(tmp_path_factory))
    with pytest.raises(AttributeError):
        facts.policy_as_of_date = "2026-01-01"  # type: ignore[misc]
    prohibited = {
        "availability",
        "state",
        "freshness",
        "allowed_actions",
        "permissions",
        "target_weights",
        "budgets",
        "caps",
        "quantities",
        "gates",
        "publication",
        "pointers",
        "orders",
    }
    assert not prohibited & set(EXPECTED_FACT_FIELDS)


@pytest.mark.parametrize(
    ("field", "value", "code"),
    (
        ("role_map_version", "unknown_role_map_v9", "H1_RECOGNITION_VERSION_UNSUPPORTED"),
        (
            "target_legacy_validator_contract_version",
            "unknown_validator_v9",
            "H1_RECOGNITION_VERSION_UNSUPPORTED",
        ),
        ("report_only", False, "H1_RECOGNITION_UPSTREAM_INVALID"),
        ("full_legacy_compatibility", True, "H1_RECOGNITION_UPSTREAM_INVALID"),
    ),
)
def test_mapping_contract_assertions_fail_closed(
    tmp_path_factory: pytest.TempPathFactory,
    field: str,
    value: object,
    code: str,
) -> None:
    kwargs = _kwargs(_inputs(tmp_path_factory))
    report = kwargs["mapping_report"]
    assert type(report) is dict
    report[field] = value
    with pytest.raises(H1MappedRecognitionError) as exc_info:
        build_validated_h1_mapped_recognition_facts(**kwargs)
    assert exc_info.value.code == code


def test_invalid_pr2_report_fails_through_authoritative_revalidation(
    tmp_path_factory: pytest.TempPathFactory,
) -> None:
    kwargs = _kwargs(_inputs(tmp_path_factory))
    report = kwargs["mapping_report"]
    assert type(report) is dict
    report["mapping_report_identity_sha256"] = "0" * 64
    with pytest.raises(H1MappedRecognitionError) as exc_info:
        build_validated_h1_mapped_recognition_facts(**kwargs)
    assert exc_info.value.code == "H1_RECOGNITION_IDENTITY_MISMATCH"


def test_current_strategy_and_portfolio_source_mismatch_fail_closed(
    tmp_path_factory: pytest.TempPathFactory,
) -> None:
    inputs = _inputs(tmp_path_factory)
    changed = hermetic.build_checkout(
        tmp_path_factory,
        "h1-mapped-recognition-current-mismatch",
        run_timestamp_et="2026-07-26 11:00 ET",
        updated="2026-07-25",
    )
    for field, source in (
        ("policy_source", changed.policy_source),
        ("portfolio_source", changed.portfolio_source),
    ):
        kwargs = _kwargs(inputs)
        kwargs[field] = source
        with pytest.raises(H1MappedRecognitionError) as exc_info:
            build_validated_h1_mapped_recognition_facts(**kwargs)
        assert exc_info.value.code == "H1_RECOGNITION_CURRENT_SOURCE_MISMATCH"


@pytest.mark.parametrize(
    ("container", "field"),
    (
        ("policy_projection", "policy_projection_identity_sha256"),
        ("policy_projection", "universe_projection_identity_sha256"),
        ("portfolio_projection", "portfolio_projection_identity_sha256"),
    ),
)
def test_current_policy_universe_and_portfolio_identity_mismatch_fail_closed(
    tmp_path_factory: pytest.TempPathFactory,
    container: str,
    field: str,
) -> None:
    kwargs = _kwargs(_inputs(tmp_path_factory))
    artifact = kwargs[container]
    assert type(artifact) is dict
    artifact[field] = "0" * 64
    with pytest.raises(H1MappedRecognitionError) as exc_info:
        build_validated_h1_mapped_recognition_facts(**kwargs)
    assert exc_info.value.code == "H1_RECOGNITION_CURRENT_SOURCE_MISMATCH"


def test_archived_internally_valid_chain_cannot_masquerade_as_current(
    tmp_path_factory: pytest.TempPathFactory,
) -> None:
    archived = _inputs(
        tmp_path_factory,
        name="h1-mapped-recognition-archived",
        as_of="2001-01-01",
        run_timestamp_et=None,
        portfolio_updated="2001-01-02",
    )
    current = hermetic.build_checkout(
        tmp_path_factory,
        "h1-mapped-recognition-current",
        run_timestamp_et="2026-07-26 11:00 ET",
        updated="2026-07-25",
    )
    kwargs = _kwargs(archived)
    kwargs["policy_source"] = current.policy_source
    kwargs["portfolio_source"] = current.portfolio_source
    with pytest.raises(H1MappedRecognitionError) as exc_info:
        build_validated_h1_mapped_recognition_facts(**kwargs)
    assert exc_info.value.code == "H1_RECOGNITION_CURRENT_SOURCE_MISMATCH"


def test_old_but_valid_temporal_facts_are_transport_only(
    tmp_path_factory: pytest.TempPathFactory,
) -> None:
    facts = _build(
        _inputs(
            tmp_path_factory,
            name="h1-mapped-recognition-old",
            as_of="2001-01-01",
            run_timestamp_et=None,
            portfolio_updated="2001-01-02",
        )
    )
    assert facts.policy_as_of_date == "2001-01-01"
    assert facts.policy_source_run_timestamp_utc is None
    assert facts.portfolio_source_date == "2001-01-02"
    assert not any(
        marker in field
        for field in EXPECTED_FACT_FIELDS
        for marker in ("fresh", "stale", "availability")
    )


@pytest.mark.parametrize(
    ("container", "field", "value"),
    (
        ("policy_projection", "policy_as_of_date", "2026-02-30"),
        ("policy_projection", "source_run_timestamp_utc", "not-a-timestamp"),
        ("portfolio_projection", "portfolio_source_date", None),
    ),
)
def test_malformed_or_missing_temporal_facts_fail_closed(
    tmp_path_factory: pytest.TempPathFactory,
    container: str,
    field: str,
    value: object,
) -> None:
    kwargs = _kwargs(_inputs(tmp_path_factory))
    artifact = kwargs[container]
    assert type(artifact) is dict
    artifact[field] = value
    with pytest.raises(H1MappedRecognitionError) as exc_info:
        build_validated_h1_mapped_recognition_facts(**kwargs)
    assert exc_info.value.code == "H1_RECOGNITION_TEMPORAL_CONTRACT_INVALID"


@pytest.mark.parametrize("mutation", ("raw-sha", "envelope", "context"))
def test_raw_response_and_prompt_context_binding_fail_closed(
    tmp_path_factory: pytest.TempPathFactory,
    mutation: str,
) -> None:
    kwargs = _kwargs(_inputs(tmp_path_factory))
    envelope = kwargs["raw_response_envelope"]
    response = kwargs["validated_grounded_analysis_response"]
    assert type(envelope) is dict
    assert type(response) is dict
    if mutation == "raw-sha":
        envelope["raw_response_sha256"] = "0" * 64
    elif mutation == "envelope":
        response["raw_response_envelope_identity_sha256"] = "0" * 64
    else:
        payload = response["response_payload"]
        assert type(payload) is dict
        payload["prompt_context_binding_sha256"] = "0" * 64
    with pytest.raises(H1MappedRecognitionError) as exc_info:
        build_validated_h1_mapped_recognition_facts(**kwargs)
    assert exc_info.value.code == "H1_RECOGNITION_CAPTURE_BINDING_INVALID"


def test_missing_input_fails_without_partial_facts(
    tmp_path_factory: pytest.TempPathFactory,
) -> None:
    kwargs = _kwargs(_inputs(tmp_path_factory))
    kwargs["mapping_report"] = None
    with pytest.raises(H1MappedRecognitionError) as exc_info:
        build_validated_h1_mapped_recognition_facts(**kwargs)
    assert exc_info.value.code == "H1_RECOGNITION_INPUT_MISSING"


def test_factory_is_side_effect_free_and_does_not_import_authority_owners(
    tmp_path_factory: pytest.TempPathFactory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    inputs = _inputs(tmp_path_factory)

    def fail_write(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("bridge reached a filesystem writer")

    monkeypatch.setattr(schema_validation, "write_json", fail_write)
    monkeypatch.setattr(schema_validation, "write_validated_json", fail_write)
    monkeypatch.setattr(Path, "write_bytes", fail_write)
    monkeypatch.setattr(Path, "write_text", fail_write)
    assert _build(inputs).source_kind == "H1_ROLE_MAPPED"

    imports = {
        module.__name__
        for _, module in inspect.getmembers(bridge, inspect.ismodule)
    }
    prohibited_prefixes = (
        "investment_orchestrator.state",
        "investment_orchestrator.workflow",
        "investment_orchestrator.orders",
        "investment_orchestrator.broker",
        "investment_orchestrator.permissions",
        "openai",
        "anthropic",
        "requests",
        "httpx",
    )
    assert not any(
        imported == prefix or imported.startswith(f"{prefix}.")
        for imported in imports
        for prefix in prohibited_prefixes
    )
