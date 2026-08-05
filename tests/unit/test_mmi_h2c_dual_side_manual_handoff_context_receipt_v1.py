from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import io
import json
from collections.abc import Mapping

import pytest

from investment_orchestrator.common.paths import prompt_path
from investment_orchestrator.llm.legacy_step1_prompt_compiler import (
    compile_legacy_step1_prompt_text,
    derive_legacy_approved_extended_etf_json,
)
from investment_orchestrator.mmi.analyst_visible_evidence_view_v2 import (
    build_mmi_analyst_visible_evidence_view_v2,
)
from investment_orchestrator.mmi.canonical import (
    MAX_MMI_H2C_DUAL_SIDE_MANUAL_HANDOFF_CONTEXT_RECEIPT_V1_CANONICAL_BYTES,
    _MMI_H2C_DUAL_SIDE_MANUAL_HANDOFF_CONTEXT_RECEIPT_V1_IDENTITY_DOMAIN,
    record_identity_sha256,
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
from investment_orchestrator.offline.mmi_h2c_dual_side_manual_handoff_context_receipt_v1 import (
    MmiH2cDualSideManualHandoffContextReceiptV1Error,
    validate_mmi_h2c_dual_side_manual_handoff_context_receipt_v1,
    validate_mmi_h2c_dual_side_manual_handoff_context_receipt_v1_portable_evidence,
)
from investment_orchestrator.offline.mmi_legacy_step1_comparison_report_v1 import (
    build_mmi_legacy_step1_comparison_report_v1,
)
from investment_orchestrator.validators.strategy_settings import (
    parse_strategy_settings_text,
)
import _mmi_hermetic_source_checkout as hermetic


# Test-owned source dates, fixed before the frozen evaluation timestamp below
# so that an operational ``inputs/current`` refresh cannot reach this module.
SOURCE_AS_OF = "2026-08-01"
SOURCE_RUN_TIMESTAMP_ET = "2026-08-01 10:00 ET"


class _FixedClock:
    def now_utc(self) -> datetime:
        return datetime(2026, 8, 2, 12, tzinfo=timezone.utc)


@dataclass(frozen=True)
class _PortableEvidence:
    receipt: dict[str, object]
    h2: dict[str, object]
    h1: dict[str, object]
    r2: dict[str, object]
    r1: dict[str, object]
    g2: dict[str, object]
    h1_prompt_bytes: bytes
    h1_response_bytes: bytes
    legacy_response_bytes: bytes
    settings_bytes: bytes
    settings_record: Mapping[str, object]
    portfolio_bytes: bytes
    portfolio_record: Mapping[str, object]
    template_bytes: bytes
    legacy_prompt_bytes: bytes

    def kwargs(self) -> dict[str, object]:
        return {
            "receipt": deepcopy(self.receipt),
            "comparison_report": deepcopy(self.h2),
            "legacy_step1_compatibility_candidate": deepcopy(self.h1),
            "validated_grounded_analysis_response": deepcopy(self.r2),
            "raw_response_envelope": deepcopy(self.r1),
            "grounded_prompt": deepcopy(self.g2),
            "archived_h1_prompt_bytes": self.h1_prompt_bytes,
            "archived_h1_response_bytes": self.h1_response_bytes,
            "archived_legacy_response_bytes": self.legacy_response_bytes,
            "archived_strategy_settings_bytes": self.settings_bytes,
            "strategy_settings_source_record": dict(self.settings_record),
            "archived_portfolio_snapshot_bytes": self.portfolio_bytes,
            "portfolio_snapshot_source_record": dict(self.portfolio_record),
            "archived_legacy_prompt_template_bytes": self.template_bytes,
            "archived_legacy_prompt_bytes": self.legacy_prompt_bytes,
        }


def _legacy_text(value: bytes) -> str:
    return io.TextIOWrapper(
        io.BytesIO(value),
        encoding="utf-8",
        errors="strict",
        newline=None,
    ).read()


def _projection(result: object) -> dict[str, object]:
    assert result.valid, result.reason_codes  # type: ignore[attr-defined]
    value = result.projection  # type: ignore[attr-defined]
    assert isinstance(value, Mapping)
    return dict(value)


def _response_payload(
    *,
    view: dict[str, object],
    prompt: dict[str, object],
) -> dict[str, object]:
    policy_view = view["policy_view"]
    assert type(policy_view) is dict
    instruments = policy_view["analysis_instruments"]
    assert type(instruments) is list
    rows = []
    for index, item in enumerate(instruments, start=1):
        assert type(item) is dict
        rows.append(
            {
                "ticker": item["ticker"],
                "evidence_status": "EVIDENCE_SUPPORTED",
                "rationale_12m_plus": "R" * 40,
                "references": [f"POLICY.INSTRUMENT.{index:04d}"],
            }
        )
    return {
        "response_schema_version": "mmi_grounded_analysis_response_v2",
        "prompt_context_binding_sha256": prompt[
            "prompt_context_binding_sha256"
        ],
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


@pytest.fixture(scope="module", autouse=True)
def _no_live_operational_inputs():
    with hermetic.live_operational_input_access_forbidden():
        yield


@pytest.fixture(scope="module")
def checkout(
    tmp_path_factory: pytest.TempPathFactory,
) -> hermetic.HermeticSourceCheckout:
    return hermetic.build_checkout(
        tmp_path_factory,
        "h2c-receipt-hermetic-checkout",
        as_of=SOURCE_AS_OF,
        run_timestamp_et=SOURCE_RUN_TIMESTAMP_ET,
        updated=SOURCE_AS_OF,
    )


@pytest.fixture(scope="module")
def evidence(
    checkout: hermetic.HermeticSourceCheckout,
) -> _PortableEvidence:
    settings_bytes = checkout.strategy_settings_raw
    portfolio_bytes = checkout.portfolio_snapshot_raw
    settings_source: MmiCapturedSource = checkout.policy_source
    portfolio_source: MmiCapturedSource = checkout.portfolio_source
    run_context: MmiProjectionRunContext = (
        _begin_mmi_projection_run_with_clock(_FixedClock())
    )
    policy = _projection(
        build_mmi_policy_projection(
            settings_source,
            run_context=run_context,
        )
    )
    portfolio = _projection(
        build_mmi_portfolio_snapshot_projection(
            portfolio_source,
            policy_projection=policy,
            policy_source=settings_source,
            run_context=run_context,
        )
    )
    bundle = _projection(
        build_mmi_authenticated_evidence_bundle(
            policy_projection=policy,
            policy_source=settings_source,
            portfolio_projection=portfolio,
            portfolio_source=portfolio_source,
            run_context=run_context,
        )
    )
    view = _projection(
        build_mmi_analyst_visible_evidence_view_v2(
            evidence_bundle=bundle,
            policy_projection=policy,
            policy_source=settings_source,
            portfolio_projection=portfolio,
            portfolio_source=portfolio_source,
            run_context=run_context,
        )
    )
    g2 = build_mmi_grounded_prompt_v2(
        analyst_visible_evidence_view=view,
        evidence_bundle=bundle,
        policy_projection=policy,
        policy_source=settings_source,
        portfolio_projection=portfolio,
        portfolio_source=portfolio_source,
        run_context=run_context,
    )
    h1_response_bytes = json.dumps(
        _response_payload(view=view, prompt=g2),
        ensure_ascii=False,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    r1 = build_mmi_raw_response_envelope_v2(
        grounded_prompt=g2,
        raw_response_bytes=h1_response_bytes,
        evidence_bundle=bundle,
        policy_projection=policy,
        policy_source=settings_source,
        portfolio_projection=portfolio,
        portfolio_source=portfolio_source,
        run_context=run_context,
    )
    r2 = build_mmi_validated_grounded_analysis_response_v2(
        raw_response_envelope=r1,
        evidence_bundle=bundle,
        policy_projection=policy,
        policy_source=settings_source,
        portfolio_projection=portfolio,
        portfolio_source=portfolio_source,
        run_context=run_context,
    )
    h1 = build_mmi_legacy_step1_compatibility_candidate_v1(
        validated_grounded_analysis_response=r2,
        raw_response_envelope=r1,
        evidence_bundle=bundle,
        policy_projection=policy,
        policy_source=settings_source,
        portfolio_projection=portfolio,
        portfolio_source=portfolio_source,
        run_context=run_context,
    )
    settings_text = _legacy_text(settings_bytes)
    portfolio_text = _legacy_text(portfolio_bytes)
    template_bytes = prompt_path("research_dual_lane.txt").read_bytes()
    legacy_prompt_bytes = compile_legacy_step1_prompt_text(
        template_text=_legacy_text(template_bytes),
        strategy_settings_text=settings_text,
        portfolio_snapshot_text=portfolio_text,
        approved_extended_etf_json=(
            derive_legacy_approved_extended_etf_json(
                strategy_settings_text=settings_text
            )
        ),
    ).encode("utf-8")
    legacy_response_bytes = b""
    h2 = build_mmi_legacy_step1_comparison_report_v1(
        legacy_step1_compatibility_candidate=h1,
        validated_grounded_analysis_response=r2,
        raw_response_envelope=r1,
        evidence_bundle=bundle,
        policy_projection=policy,
        policy_source=settings_source,
        portfolio_projection=portfolio,
        portfolio_source=portfolio_source,
        run_context=run_context,
        legacy_research_raw_bytes=legacy_response_bytes,
        legacy_strategy_settings=parse_strategy_settings_text(settings_text),
    )
    receipt: dict[str, object] = {
        "schema_version": (
            "mmi_h2c_dual_side_manual_handoff_context_receipt_v1"
        ),
        "artifact_kind": (
            "MMI_H2C_DUAL_SIDE_MANUAL_HANDOFF_CONTEXT_RECEIPT"
        ),
        "capture_contract_version": "mmi_h2c_manual_capture_v1",
        "report_only": True,
        "authority_effect": "NONE",
        "live_context_validated_at_capture": True,
        "operator_h1_response_bytes_bound_at_capture": True,
        "operator_legacy_response_bytes_bound_at_capture": True,
        "provider_origin_authentication": "NOT_ESTABLISHED",
        "evaluation_timestamp_utc": run_context.evaluation_timestamp_utc,
        "strategy_settings_source_record_identity_sha256": (
            settings_source.source_record["source_record_identity_sha256"]
        ),
        "portfolio_snapshot_source_record_identity_sha256": (
            portfolio_source.source_record["source_record_identity_sha256"]
        ),
        "legacy_prompt_template_sha256": hashlib.sha256(
            template_bytes
        ).hexdigest(),
        "legacy_prompt_sha256": hashlib.sha256(
            legacy_prompt_bytes
        ).hexdigest(),
        "comparison_report_identity_sha256": h2[
            "comparison_report_identity_sha256"
        ],
        "receipt_identity_sha256": "0" * 64,
    }
    receipt["receipt_identity_sha256"] = record_identity_sha256(
        receipt,
        identity_field="receipt_identity_sha256",
        domain=(
            _MMI_H2C_DUAL_SIDE_MANUAL_HANDOFF_CONTEXT_RECEIPT_V1_IDENTITY_DOMAIN
        ),
        maximum_bytes=(
            MAX_MMI_H2C_DUAL_SIDE_MANUAL_HANDOFF_CONTEXT_RECEIPT_V1_CANONICAL_BYTES
        ),
    )
    prompt_text = g2["prompt_text"]
    assert type(prompt_text) is str
    return _PortableEvidence(
        receipt=receipt,
        h2=h2,
        h1=h1,
        r2=r2,
        r1=r1,
        g2=g2,
        h1_prompt_bytes=prompt_text.encode("utf-8"),
        h1_response_bytes=h1_response_bytes,
        legacy_response_bytes=legacy_response_bytes,
        settings_bytes=settings_bytes,
        settings_record=settings_source.source_record,
        portfolio_bytes=portfolio_bytes,
        portfolio_record=portfolio_source.source_record,
        template_bytes=template_bytes,
        legacy_prompt_bytes=legacy_prompt_bytes,
    )


def _independent_canonical_json_bytes(value: object) -> bytes:
    """Reimplements the canonical JSON encoding without calling
    ``canonical_json_bytes`` -- an independent serialization oracle."""
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def _independent_receipt_identity_sha256(receipt: dict[str, object]) -> str:
    """Independent identity oracle: reimplements the domain-separated
    framing and hashing from first principles. Deliberately does not call
    ``record_identity_sha256``, ``domain_separated_sha256``, or any private
    helper owned by the receipt module."""
    preimage = deepcopy(receipt)
    preimage.pop("receipt_identity_sha256")
    preimage_bytes = _independent_canonical_json_bytes(preimage)
    length_frame = len(preimage_bytes).to_bytes(
        8, byteorder="big", signed=False
    )
    framed = (
        _MMI_H2C_DUAL_SIDE_MANUAL_HANDOFF_CONTEXT_RECEIPT_V1_IDENTITY_DOMAIN
        + length_frame
        + preimage_bytes
    )
    return hashlib.sha256(framed).hexdigest()


def test_receipt_exact_shape_identity_and_arithmetic(
    evidence: _PortableEvidence,
) -> None:
    assert validate_mmi_h2c_dual_side_manual_handoff_context_receipt_v1(
        receipt=evidence.receipt
    ) is None
    assert len(evidence.receipt) == 16

    preimage = deepcopy(evidence.receipt)
    preimage.pop("receipt_identity_sha256")
    assert "receipt_identity_sha256" not in preimage
    assert len(preimage) == 15
    assert set(preimage) == set(evidence.receipt) - {
        "receipt_identity_sha256"
    }

    domain = (
        _MMI_H2C_DUAL_SIDE_MANUAL_HANDOFF_CONTEXT_RECEIPT_V1_IDENTITY_DOMAIN
    )
    preimage_bytes = _independent_canonical_json_bytes(preimage)
    length_frame = len(preimage_bytes).to_bytes(
        8, byteorder="big", signed=False
    )
    framed = domain + length_frame + preimage_bytes
    complete_receipt_bytes = _independent_canonical_json_bytes(
        evidence.receipt
    )

    assert len(domain) == 52
    assert len(length_frame) == 8
    assert len(preimage_bytes) == 1021
    assert len(framed) == 1081
    assert len(complete_receipt_bytes) == 1114

    expected_identity = hashlib.sha256(framed).hexdigest()
    assert expected_identity == evidence.receipt["receipt_identity_sha256"]
    assert expected_identity == _independent_receipt_identity_sha256(
        evidence.receipt
    )


def test_independent_oracle_detects_a_mutated_identity_covered_field(
    evidence: _PortableEvidence,
) -> None:
    mutated = deepcopy(evidence.receipt)
    stored_identity = mutated["receipt_identity_sha256"]
    mutated["legacy_prompt_sha256"] = "0" * 64
    assert (
        mutated["legacy_prompt_sha256"]
        != evidence.receipt["legacy_prompt_sha256"]
    )

    recomputed_identity = _independent_receipt_identity_sha256(mutated)

    assert recomputed_identity != stored_identity


def test_complete_portable_structural_chain_validates(
    evidence: _PortableEvidence,
) -> None:
    assert (
        validate_mmi_h2c_dual_side_manual_handoff_context_receipt_v1_portable_evidence(
            **evidence.kwargs()
        )
        is None
    )


@pytest.mark.parametrize(
    ("field", "replacement"),
    (
        ("archived_h1_prompt_bytes", b"changed prompt"),
        ("archived_h1_response_bytes", b"{}"),
        ("archived_legacy_response_bytes", b"changed"),
        ("archived_strategy_settings_bytes", b"changed"),
        ("archived_portfolio_snapshot_bytes", b"changed"),
        ("archived_legacy_prompt_template_bytes", b"changed"),
        ("archived_legacy_prompt_bytes", b"changed"),
    ),
)
def test_changed_archived_bytes_fail_portable_structural_validation(
    evidence: _PortableEvidence,
    field: str,
    replacement: bytes,
) -> None:
    kwargs = evidence.kwargs()
    kwargs[field] = replacement
    with pytest.raises(
        MmiH2cDualSideManualHandoffContextReceiptV1Error,
        match="^MMI_H2C_PORTABLE_EVIDENCE_INVALID$",
    ) as captured:
        validate_mmi_h2c_dual_side_manual_handoff_context_receipt_v1_portable_evidence(
            **kwargs
        )
    assert captured.value.code == "MMI_H2C_PORTABLE_EVIDENCE_INVALID"


def test_wrong_g2_identity_and_r1_to_g2_link_are_rejected(
    evidence: _PortableEvidence,
) -> None:
    kwargs = evidence.kwargs()
    grounded_prompt = kwargs["grounded_prompt"]
    assert type(grounded_prompt) is dict
    grounded_prompt["grounded_prompt_artifact_identity_sha256"] = "f" * 64
    with pytest.raises(MmiH2cDualSideManualHandoffContextReceiptV1Error):
        validate_mmi_h2c_dual_side_manual_handoff_context_receipt_v1_portable_evidence(
            **kwargs
        )


def test_unknown_receipt_field_and_non_bytes_are_rejected(
    evidence: _PortableEvidence,
) -> None:
    receipt = deepcopy(evidence.receipt)
    receipt["context_proven"] = True
    with pytest.raises(
        MmiH2cDualSideManualHandoffContextReceiptV1Error,
        match="^MMI_H2C_RECEIPT_V1_INVALID$",
    ):
        validate_mmi_h2c_dual_side_manual_handoff_context_receipt_v1(
            receipt=receipt
        )
    kwargs = evidence.kwargs()
    kwargs["archived_h1_prompt_bytes"] = bytearray(
        evidence.h1_prompt_bytes
    )
    with pytest.raises(
        MmiH2cDualSideManualHandoffContextReceiptV1Error,
        match="^MMI_H2C_PORTABLE_EVIDENCE_INVALID$",
    ):
        validate_mmi_h2c_dual_side_manual_handoff_context_receipt_v1_portable_evidence(
            **kwargs
        )


def test_sources_are_test_owned_and_live_inputs_are_unreachable(
    checkout: hermetic.HermeticSourceCheckout,
) -> None:
    hermetic.assert_checkout_resolves_both_locators(checkout.root)
    hermetic.assert_test_owned_source(
        checkout.policy_source,
        role=MmiSourceRole.STRATEGY_SETTINGS,
        raw=checkout.strategy_settings_raw,
    )
    hermetic.assert_test_owned_source(
        checkout.portfolio_source,
        role=MmiSourceRole.PORTFOLIO_SNAPSHOT,
        raw=checkout.portfolio_snapshot_raw,
    )
    assert checkout.policy_source.source_record[
        "repository_relative_locator"
    ] == hermetic.STRATEGY_SETTINGS_LOCATOR
    assert checkout.portfolio_source.source_record[
        "repository_relative_locator"
    ] == hermetic.PORTFOLIO_SNAPSHOT_LOCATOR
    hermetic.assert_live_operational_inputs_are_unreachable()
