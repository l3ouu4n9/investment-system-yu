"""Contract tests for the dormant H1 prepared-handoff envelope.

Only this envelope's own load-bearing boundaries are proven here: its derived
canonical ceiling, its self-identity, and the expected-identity gate that must
precede any run-context mint.  Grounded-prompt internals, source-capture
permutations, and stable-read filesystem behaviour are already owned by their
existing test matrices and are not repeated.
"""

from __future__ import annotations

from datetime import datetime, timezone
import json

import pytest

from investment_orchestrator.mmi import canonical
from investment_orchestrator.mmi import mmi_h1_prepared_handoff_v1 as owner
from investment_orchestrator.mmi import run_context_resumption as resumption
from investment_orchestrator.mmi.analyst_visible_evidence_view_v2 import (
    build_mmi_analyst_visible_evidence_view_v2,
)
from investment_orchestrator.mmi.canonical import domain_separated_sha256
from investment_orchestrator.mmi.contracts import (
    MmiProjectionRunContext,
    _begin_mmi_projection_run_with_clock,
)
from investment_orchestrator.mmi.evidence_bundle import (
    build_mmi_authenticated_evidence_bundle,
)
from investment_orchestrator.mmi.grounded_prompt_v2 import (
    build_mmi_grounded_prompt_v2,
)
from investment_orchestrator.mmi.policy_projection import (
    build_mmi_policy_projection,
)
from investment_orchestrator.mmi.portfolio_projection import (
    build_mmi_portfolio_snapshot_projection,
)

import _mmi_hermetic_source_checkout as hermetic


PREPARED_TIME = datetime(2026, 7, 31, 12, tzinfo=timezone.utc)
PREPARED_TIMESTAMP = "2026-07-31T12:00:00.000000Z"
IDENTITY_FIELD = "prepared_handoff_identity_sha256"
IDENTITY_DOMAIN = b"mmi_h1_prepared_handoff_v1\0"
INVALID = "MMI_H1_PREPARED_HANDOFF_V1_INVALID"
ZERO = "0" * 64
OTHER_SHA256 = "1" * 64
EXPECTED_FIELDS = (
    "artifact_kind",
    "authority_effect",
    "evaluation_timestamp_utc",
    "grounded_prompt",
    "portfolio_snapshot_source_sha256",
    "prepared_handoff_identity_sha256",
    "report_only",
    "schema_version",
    "strategy_settings_source_sha256",
)


class _FixedClock:
    def now_utc(self) -> datetime:
        return PREPARED_TIME


def _canonical_bytes(value: object) -> bytes:
    """An independent canonical encoder, not the production owner's."""
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


@pytest.fixture(scope="module")
def grounded_prompt(
    tmp_path_factory: pytest.TempPathFactory,
) -> dict[str, object]:
    """One real fixed-clock G2 artifact from the hermetic checkout."""
    checkout = hermetic.build_checkout(tmp_path_factory, "h1-prepared-handoff")
    run_context = _begin_mmi_projection_run_with_clock(_FixedClock())
    assert run_context.evaluation_timestamp_utc == PREPARED_TIMESTAMP
    policy_result = build_mmi_policy_projection(
        checkout.policy_source,
        run_context=run_context,
    )
    assert policy_result.valid, policy_result.reason_codes
    policy = dict(policy_result.projection or {})
    portfolio_result = build_mmi_portfolio_snapshot_projection(
        checkout.portfolio_source,
        policy_projection=policy,
        policy_source=checkout.policy_source,
        run_context=run_context,
    )
    assert portfolio_result.valid, portfolio_result.reason_codes
    portfolio = dict(portfolio_result.projection or {})
    evidence_result = build_mmi_authenticated_evidence_bundle(
        policy_projection=policy,
        policy_source=checkout.policy_source,
        portfolio_projection=portfolio,
        portfolio_source=checkout.portfolio_source,
        run_context=run_context,
    )
    assert evidence_result.valid, evidence_result.reason_codes
    evidence = dict(evidence_result.projection or {})
    view_result = build_mmi_analyst_visible_evidence_view_v2(
        evidence_bundle=evidence,
        policy_projection=policy,
        policy_source=checkout.policy_source,
        portfolio_projection=portfolio,
        portfolio_source=checkout.portfolio_source,
        run_context=run_context,
    )
    assert view_result.valid, view_result.reason_codes
    return build_mmi_grounded_prompt_v2(
        analyst_visible_evidence_view=dict(view_result.projection or {}),
        evidence_bundle=evidence,
        policy_projection=policy,
        policy_source=checkout.policy_source,
        portfolio_projection=portfolio,
        portfolio_source=checkout.portfolio_source,
        run_context=run_context,
    )


def _build(
    grounded_prompt: dict[str, object],
    *,
    portfolio_snapshot_source_sha256: str | None = "b" * 64,
) -> dict[str, object]:
    return owner._build_mmi_h1_prepared_handoff_v1(
        evaluation_timestamp_utc=PREPARED_TIMESTAMP,
        strategy_settings_source_sha256="a" * 64,
        portfolio_snapshot_source_sha256=portfolio_snapshot_source_sha256,
        grounded_prompt=grounded_prompt,
    )


def test_canonical_ceiling_is_the_derived_framing_and_is_enforced(
    grounded_prompt: dict[str, object],
) -> None:
    """Reproduce the Class-B framing arithmetic without the production owner."""
    framing = len(
        _canonical_bytes(
            {
                "schema_version": "mmi_h1_prepared_handoff_v1",
                "artifact_kind": "MMI_H1_PREPARED_HANDOFF",
                "report_only": True,
                "authority_effect": "NONE",
                "evaluation_timestamp_utc": PREPARED_TIMESTAMP,
                "strategy_settings_source_sha256": ZERO,
                "portfolio_snapshot_source_sha256": ZERO,
                "grounded_prompt": {},
                IDENTITY_FIELD: ZERO,
            }
        )
    ) - len(b"{}")
    grounded_prompt_fixed = len(
        _canonical_bytes(
            {
                "schema_version": "mmi_grounded_prompt_v2",
                "artifact_kind": "MMI_GROUNDED_PROMPT",
                "report_only": True,
                "authority_effect": "NONE",
                "analyst_visible_evidence_view_identity_sha256": ZERO,
                "instruction_set_version": (
                    "mmi_grounded_prompt_instruction_set_v2"
                ),
                "expected_response_schema_version": (
                    "mmi_grounded_analysis_response_v2"
                ),
                "manual_handoff_required": True,
                "prompt_context_binding_sha256": ZERO,
                "prompt_text": "",
                "grounded_prompt_artifact_identity_sha256": ZERO,
            }
        )
    )
    # A one-byte control character costs six JSON escape bytes.
    assert len(json.dumps("\x00", ensure_ascii=False).encode("utf-8")) - 2 == 6
    grounded_prompt_maximum = (
        grounded_prompt_fixed
        + canonical.MAXIMUM_GROUNDED_PROMPT_TEXT_BYTES * 6
    )
    assert canonical.MAXIMUM_GROUNDED_PROMPT_TEXT_BYTES == 65_536
    assert framing == 515
    assert grounded_prompt_maximum == 393_852
    assert owner._GROUNDED_PROMPT_MAXIMUM_CANONICAL_BYTES == (
        grounded_prompt_maximum
    )
    assert framing + grounded_prompt_maximum == 394_367
    assert (
        canonical.MAX_MMI_H1_PREPARED_HANDOFF_V1_CANONICAL_BYTES == 394_367
    )
    assert owner._MAXIMUM_CANONICAL_BYTES == 394_367

    # The ceiling is a maximum a real handoff sits well under, and an embedded
    # prompt that would exceed the subordinate bound is rejected.
    prepared = _build(grounded_prompt)
    assert len(_canonical_bytes(prepared)) < 394_367
    with pytest.raises(owner.MmiH1PreparedHandoffV1Error) as raised:
        _build({"too_large": "x" * 393_852})
    assert raised.value.code == INVALID


def test_self_identity_is_domain_separated_recomputable_and_tamper_evident(
    grounded_prompt: dict[str, object],
) -> None:
    prepared = _build(grounded_prompt)
    assert tuple(sorted(prepared)) == EXPECTED_FIELDS
    assert prepared["report_only"] is True
    assert prepared["authority_effect"] == "NONE"
    assert prepared["grounded_prompt"] == grounded_prompt

    # Independent oracle: the zero placeholder never enters the preimage, so
    # the identity is exactly the domain-separated hash of the other eight
    # fields under this owner's dedicated domain and ceiling.
    preimage = {
        key: value for key, value in prepared.items() if key != IDENTITY_FIELD
    }
    expected = domain_separated_sha256(
        IDENTITY_DOMAIN,
        preimage,
        maximum_bytes=394_367,
    )
    assert prepared[IDENTITY_FIELD] == expected
    assert (
        domain_separated_sha256(
            IDENTITY_DOMAIN,
            {**preimage, IDENTITY_FIELD: ZERO},
            maximum_bytes=394_367,
        )
        != expected
    )
    assert (
        owner.validate_mmi_h1_prepared_handoff_v1(prepared_handoff=prepared)
        == prepared
    )

    # A proven-absent portfolio is a distinct null-valued shape, never a
    # digest sentinel, and it carries its own identity.
    absent = _build(grounded_prompt, portfolio_snapshot_source_sha256=None)
    assert absent["portfolio_snapshot_source_sha256"] is None
    assert absent[IDENTITY_FIELD] != prepared[IDENTITY_FIELD]
    owner.validate_mmi_h1_prepared_handoff_v1(prepared_handoff=absent)
    for sentinel in ("", "ABSENT", "0" * 63, "A" * 64):
        with pytest.raises(owner.MmiH1PreparedHandoffV1Error):
            _build(
                grounded_prompt,
                portfolio_snapshot_source_sha256=sentinel,
            )

    for field, replacement in (
        (IDENTITY_FIELD, OTHER_SHA256),
        ("evaluation_timestamp_utc", "2026-07-31T12:00:00.000001Z"),
        ("strategy_settings_source_sha256", OTHER_SHA256),
        ("portfolio_snapshot_source_sha256", None),
        ("report_only", False),
        ("authority_effect", "REPORT_ONLY"),
        ("artifact_kind", "MMI_H1_PREPARED_HANDOFF_V2"),
        ("schema_version", "mmi_h1_prepared_handoff_v2"),
    ):
        tampered = dict(prepared)
        tampered[field] = replacement
        with pytest.raises(owner.MmiH1PreparedHandoffV1Error) as raised:
            owner.validate_mmi_h1_prepared_handoff_v1(
                prepared_handoff=tampered
            )
        assert raised.value.code == INVALID

    extra = dict(prepared)
    extra["convenience_field"] = "no"
    with pytest.raises(owner.MmiH1PreparedHandoffV1Error):
        owner.validate_mmi_h1_prepared_handoff_v1(prepared_handoff=extra)
    missing = dict(prepared)
    del missing["portfolio_snapshot_source_sha256"]
    with pytest.raises(owner.MmiH1PreparedHandoffV1Error):
        owner.validate_mmi_h1_prepared_handoff_v1(prepared_handoff=missing)


def test_expected_identity_gates_resumption_before_any_mint(
    grounded_prompt: dict[str, object],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    prepared = _build(grounded_prompt)
    identity = prepared[IDENTITY_FIELD]
    assert type(identity) is str

    observed: list[str] = []
    actual_mint = (
        resumption._contracts
        .mint_mmi_projection_run_context_from_canonical_timestamp
    )

    def _recording_mint(
        *,
        evaluation_timestamp_utc: str,
    ) -> MmiProjectionRunContext:
        observed.append(evaluation_timestamp_utc)
        return actual_mint(evaluation_timestamp_utc=evaluation_timestamp_utc)

    monkeypatch.setattr(
        resumption._contracts,
        "mint_mmi_projection_run_context_from_canonical_timestamp",
        _recording_mint,
    )

    # A substituted expectation never reaches the mint.
    with pytest.raises(owner.MmiH1PreparedHandoffV1Error) as raised:
        owner.resume_mmi_h1_prepared_handoff_run_context(
            prepared_handoff=prepared,
            expected_prepared_handoff_identity_sha256=OTHER_SHA256,
        )
    assert raised.value.code == INVALID
    assert observed == []

    # A stale envelope whose own identity was rewritten to match the expected
    # argument still fails, because the owner recomputes it.
    stale = dict(prepared)
    stale[IDENTITY_FIELD] = OTHER_SHA256
    with pytest.raises(owner.MmiH1PreparedHandoffV1Error):
        owner.resume_mmi_h1_prepared_handoff_run_context(
            prepared_handoff=stale,
            expected_prepared_handoff_identity_sha256=OTHER_SHA256,
        )
    assert observed == []

    resumed = owner.resume_mmi_h1_prepared_handoff_run_context(
        prepared_handoff=prepared,
        expected_prepared_handoff_identity_sha256=identity,
    )
    assert observed == [PREPARED_TIMESTAMP]
    assert type(resumed) is MmiProjectionRunContext
    assert resumed.evaluation_timestamp_utc == PREPARED_TIMESTAMP
    assert resumed.evaluation_time_utc == PREPARED_TIME
    assert resumed.authority_effect == "NONE"
