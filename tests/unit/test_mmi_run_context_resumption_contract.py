"""Contract tests for prepared-case-bound MMI run-context resumption.

This module owns exactly two new surfaces: the low-level canonical-timestamp
mint in ``mmi.contracts`` and the authority-bearing resumption wrapper in the
offline prepared-case owner.  Source-capability binding is deliberately absent
here; combining one case's context with another case's captured sources is a
Phase B engine invariant and belongs to that later owner.
"""

from __future__ import annotations

import ast
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import inspect
from typing import Iterator

import pytest

from investment_orchestrator.common.paths import repo_root
from investment_orchestrator.mmi import contracts
from investment_orchestrator.mmi import run_context_resumption as resumption
from investment_orchestrator.mmi.analyst_visible_evidence_view_v2 import (
    build_mmi_analyst_visible_evidence_view_v2,
)
from investment_orchestrator.mmi.contracts import (
    MmiCapturedSource,
    MmiProjectionRunContext,
    MmiRunContextContractError,
    _begin_mmi_projection_run_with_clock,
    begin_mmi_projection_run,
    mint_mmi_projection_run_context_from_canonical_timestamp,
)
from investment_orchestrator.mmi.evidence_bundle import (
    build_mmi_authenticated_evidence_bundle,
)
from investment_orchestrator.mmi.grounded_prompt_v2 import (
    build_mmi_grounded_prompt_v2,
    validate_mmi_grounded_prompt_v2,
)
from investment_orchestrator.mmi.policy_projection import (
    build_mmi_policy_projection,
    validate_mmi_policy_projection,
)
from investment_orchestrator.mmi.portfolio_projection import (
    build_mmi_portfolio_snapshot_projection,
    validate_mmi_portfolio_snapshot_projection,
)
from investment_orchestrator.offline import (
    mmi_h2c_prepared_case_v1 as owner,
)

import _mmi_hermetic_source_checkout as hermetic


TIMESTAMP_NOT_CANONICAL = "MMI_RUN_CONTEXT_TIMESTAMP_NOT_CANONICAL"
PREPARED_CASE_INVALID = "MMI_H2C_PREPARED_CASE_V1_INVALID"
IDENTITY_FIELD = "prepared_case_identity_sha256"
CANONICAL_FORMAT = "%Y-%m-%dT%H:%M:%S.%fZ"

# One fixed Phase A instant.  It is later than the hermetic checkout's default
# ``as_of`` date and its Eastern run timestamp, so the existing future-date
# gates in the policy owner accept it.
PHASE_A_TIME = datetime(2026, 7, 26, 15, 30, 45, 123456, tzinfo=timezone.utc)
PHASE_A_TIMESTAMP = "2026-07-26T15:30:45.123456Z"
OTHER_SHA256 = "1" * 64

PRODUCTION_ROOT = "src/investment_orchestrator"
CONTRACTS_RELATIVE = f"{PRODUCTION_ROOT}/mmi/contracts.py"
PREPARED_CASE_RELATIVE = f"{PRODUCTION_ROOT}/offline/mmi_h2c_prepared_case_v1.py"
RESUMPTION_RELATIVE = f"{PRODUCTION_ROOT}/mmi/run_context_resumption.py"
CONSUME_CASE_RELATIVE = f"{PRODUCTION_ROOT}/offline/mmi_h2c_consume_persisted_case_v1.py"
ARCHIVED_SOURCE_RELATIVE = f"{PRODUCTION_ROOT}/offline/mmi_h2c_archived_source_v1.py"


class _FixedClock:
    """The repository's existing fixed-clock test seam."""

    def now_utc(self) -> datetime:
        return PHASE_A_TIME


class _ForbiddenClock:
    """A clock that fails if any caller instantiates or reads it."""

    def __init__(self) -> None:
        raise AssertionError("resumption must not construct a clock")

    def now_utc(self) -> datetime:
        raise AssertionError("resumption must not read a clock")


@dataclass(frozen=True, slots=True)
class _PhaseA:
    """One complete fixed-clock Phase A chain and its persisted envelope."""

    policy_source: MmiCapturedSource
    portfolio_source: MmiCapturedSource
    view: dict[str, object]
    prompt: dict[str, object]
    prompt_bytes: bytes
    prepared_case: dict[str, object]


def _chain(
    *,
    policy_source: MmiCapturedSource,
    portfolio_source: MmiCapturedSource,
    run_context: MmiProjectionRunContext,
) -> tuple[dict[str, object], dict[str, object]]:
    """Drive the real live chain to a validated G2 for one run context."""
    policy_result = build_mmi_policy_projection(
        policy_source,
        run_context=run_context,
    )
    assert policy_result.valid, policy_result.reason_codes
    policy = dict(policy_result.projection or {})
    policy_validation = validate_mmi_policy_projection(
        policy,
        source=policy_source,
        run_context=run_context,
    )
    assert policy_validation.valid, policy_validation.reason_codes

    portfolio_result = build_mmi_portfolio_snapshot_projection(
        portfolio_source,
        policy_projection=policy,
        policy_source=policy_source,
        run_context=run_context,
    )
    assert portfolio_result.valid, portfolio_result.reason_codes
    portfolio = dict(portfolio_result.projection or {})
    portfolio_validation = validate_mmi_portfolio_snapshot_projection(
        portfolio,
        portfolio_source=portfolio_source,
        policy_projection=policy,
        policy_source=policy_source,
        run_context=run_context,
    )
    assert portfolio_validation.valid, portfolio_validation.reason_codes

    evidence_result = build_mmi_authenticated_evidence_bundle(
        policy_projection=policy,
        policy_source=policy_source,
        portfolio_projection=portfolio,
        portfolio_source=portfolio_source,
        run_context=run_context,
    )
    assert evidence_result.valid, evidence_result.reason_codes
    evidence = dict(evidence_result.projection or {})

    view_result = build_mmi_analyst_visible_evidence_view_v2(
        evidence_bundle=evidence,
        policy_projection=policy,
        policy_source=policy_source,
        portfolio_projection=portfolio,
        portfolio_source=portfolio_source,
        run_context=run_context,
    )
    assert view_result.valid, view_result.reason_codes
    view = dict(view_result.projection or {})

    prompt = build_mmi_grounded_prompt_v2(
        analyst_visible_evidence_view=view,
        evidence_bundle=evidence,
        policy_projection=policy,
        policy_source=policy_source,
        portfolio_projection=portfolio,
        portfolio_source=portfolio_source,
        run_context=run_context,
    )
    validated = validate_mmi_grounded_prompt_v2(
        value=prompt,
        evidence_bundle=evidence,
        policy_projection=policy,
        policy_source=policy_source,
        portfolio_projection=portfolio,
        portfolio_source=portfolio_source,
        run_context=run_context,
    )
    return view, dict(validated)


@pytest.fixture(scope="module")
def phase_a(
    tmp_path_factory: pytest.TempPathFactory,
) -> _PhaseA:
    """Build one real fixed-clock Phase A chain and prepared-case envelope."""
    checkout = hermetic.build_checkout(
        tmp_path_factory,
        "run-context-resumption",
    )
    context = _begin_mmi_projection_run_with_clock(_FixedClock())
    assert context.evaluation_timestamp_utc == PHASE_A_TIMESTAMP
    view, prompt = _chain(
        policy_source=checkout.policy_source,
        portfolio_source=checkout.portfolio_source,
        run_context=context,
    )
    prompt_text = prompt["prompt_text"]
    assert type(prompt_text) is str
    prompt_bytes = prompt_text.encode("utf-8")
    prepared_case = owner._build_mmi_h2c_prepared_case_v1(
        evaluation_timestamp_utc=PHASE_A_TIMESTAMP,
        strategy_settings_source_record=dict(
            checkout.policy_source.source_record
        ),
        portfolio_snapshot_source_record=dict(
            checkout.portfolio_source.source_record
        ),
        legacy_prompt_template_bytes=b"legacy template body\n",
        grounded_prompt=prompt,
        h1_prompt_bytes=prompt_bytes,
        legacy_prompt_bytes=b"legacy prompt body\n",
    )
    return _PhaseA(
        policy_source=checkout.policy_source,
        portfolio_source=checkout.portfolio_source,
        view=view,
        prompt=prompt,
        prompt_bytes=prompt_bytes,
        prepared_case=prepared_case,
    )


def _identity(prepared_case: Mapping[str, object]) -> str:
    value = prepared_case[IDENTITY_FIELD]
    assert type(value) is str
    return value


def _resume(prepared_case: Mapping[str, object]) -> MmiProjectionRunContext:
    return owner.resume_mmi_h2c_prepared_case_run_context(
        prepared_case=prepared_case,
        expected_prepared_case_identity_sha256=_identity(prepared_case),
    )


def _production_sources() -> Iterator[tuple[str, str]]:
    root = repo_root()
    for path in sorted((root / PRODUCTION_ROOT).rglob("*.py")):
        yield (
            path.relative_to(root).as_posix(),
            path.read_text(encoding="utf-8"),
        )


def _imports_current_resumption_owner(source: str) -> bool:
    """Recognize every direct production import form for the current owner."""
    owner_module = "investment_orchestrator.mmi.run_context_resumption"
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            if any(alias.name == owner_module for alias in node.names):
                return True
        elif isinstance(node, ast.ImportFrom):
            if node.module == owner_module:
                return True
            if (
                node.module == "investment_orchestrator.mmi"
                and any(
                    alias.name == "run_context_resumption"
                    for alias in node.names
                )
            ):
                return True
    return False


# --------------------------------------------------------------------------
# Low-level canonical-timestamp mint.
# --------------------------------------------------------------------------
def test_canonical_timestamp_mints_one_valid_report_only_context() -> None:
    context = mint_mmi_projection_run_context_from_canonical_timestamp(
        evaluation_timestamp_utc=PHASE_A_TIMESTAMP,
    )
    assert type(context) is MmiProjectionRunContext
    assert context.evaluation_timestamp_utc == PHASE_A_TIMESTAMP
    assert context.evaluation_time_utc == PHASE_A_TIME
    assert context.evaluation_time_utc.tzinfo is timezone.utc
    assert context.authority_effect == "NONE"
    assert contracts._mmi_projection_run_context_provenance_is_valid(context)


def test_mint_signature_is_keyword_only_and_default_free() -> None:
    signature = inspect.signature(
        mint_mmi_projection_run_context_from_canonical_timestamp
    )
    assert tuple(signature.parameters) == ("evaluation_timestamp_utc",)
    assert all(
        item.kind is inspect.Parameter.KEYWORD_ONLY
        and item.default is inspect.Parameter.empty
        for item in signature.parameters.values()
    )


def test_independent_mints_of_one_timestamp_are_distinct_and_equal() -> None:
    first = mint_mmi_projection_run_context_from_canonical_timestamp(
        evaluation_timestamp_utc=PHASE_A_TIMESTAMP,
    )
    second = mint_mmi_projection_run_context_from_canonical_timestamp(
        evaluation_timestamp_utc=PHASE_A_TIMESTAMP,
    )
    assert first is not second
    assert first._provenance_token != second._provenance_token
    assert first.evaluation_timestamp_utc == second.evaluation_timestamp_utc


@pytest.mark.parametrize(
    "value",
    [
        pytest.param("2026-07-26T15:30:45Z", id="missing-microseconds"),
        pytest.param("2026-07-26T15:30:45.123456", id="missing-zulu"),
        pytest.param("2026-07-26 15:30:45.123456Z", id="space-separator"),
        pytest.param("2026-13-26T15:30:45.123456Z", id="impossible-month"),
        pytest.param("", id="empty"),
    ],
)
def test_non_canonical_timestamp_fails_with_the_exact_code(
    value: str,
) -> None:
    with pytest.raises(MmiRunContextContractError) as raised:
        mint_mmi_projection_run_context_from_canonical_timestamp(
            evaluation_timestamp_utc=value,
        )
    assert raised.value.code == TIMESTAMP_NOT_CANONICAL


@pytest.mark.parametrize(
    "value",
    [
        pytest.param(None, id="none"),
        pytest.param(b"2026-07-26T15:30:45.123456Z", id="bytes"),
        pytest.param(1753544445, id="epoch-int"),
        pytest.param(PHASE_A_TIME, id="datetime"),
    ],
)
def test_non_string_timestamp_fails_with_the_exact_code(
    value: object,
) -> None:
    with pytest.raises(MmiRunContextContractError) as raised:
        mint_mmi_projection_run_context_from_canonical_timestamp(
            evaluation_timestamp_utc=value,  # type: ignore[arg-type]
        )
    assert raised.value.code == TIMESTAMP_NOT_CANONICAL


def test_error_type_rejects_any_other_code() -> None:
    with pytest.raises(TypeError):
        MmiRunContextContractError("MMI_RUN_CONTEXT_SOMETHING_ELSE")  # type: ignore[arg-type]


def test_direct_construction_remains_blocked() -> None:
    with pytest.raises(TypeError):
        MmiProjectionRunContext()
    with pytest.raises(TypeError):
        MmiProjectionRunContext(
            evaluation_time_utc=PHASE_A_TIME,
            evaluation_timestamp_utc=PHASE_A_TIMESTAMP,
        )


def test_forged_context_is_rejected_by_an_existing_public_validator(
    phase_a: _PhaseA,
) -> None:
    legitimate = mint_mmi_projection_run_context_from_canonical_timestamp(
        evaluation_timestamp_utc=PHASE_A_TIMESTAMP,
    )
    forged = object.__new__(MmiProjectionRunContext)
    object.__setattr__(forged, "evaluation_time_utc", PHASE_A_TIME)
    object.__setattr__(
        forged,
        "evaluation_timestamp_utc",
        PHASE_A_TIMESTAMP,
    )
    object.__setattr__(forged, "authority_effect", "NONE")
    object.__setattr__(
        forged,
        "_provenance_token",
        legitimate._provenance_token,
    )
    object.__setattr__(
        forged,
        "_provenance_seal",
        legitimate._provenance_seal,
    )
    assert not contracts._mmi_projection_run_context_provenance_is_valid(
        forged
    )
    result = build_mmi_policy_projection(
        phase_a.policy_source,
        run_context=forged,
    )
    assert result.reason_codes == (
        "MMI_PROJECTION_RUN_CONTEXT_PROVENANCE_INVALID",
    )
    assert result.projection is None


# --------------------------------------------------------------------------
# Prepared-case-bound resumption wrapper.
# --------------------------------------------------------------------------
def test_valid_prepared_case_resumes_the_exact_phase_a_time(
    phase_a: _PhaseA,
) -> None:
    resumed = _resume(phase_a.prepared_case)
    assert type(resumed) is MmiProjectionRunContext
    assert resumed.evaluation_timestamp_utc == PHASE_A_TIMESTAMP
    assert resumed.evaluation_time_utc == PHASE_A_TIME
    assert resumed.authority_effect == "NONE"
    assert contracts._mmi_projection_run_context_provenance_is_valid(resumed)


def test_wrong_expected_identity_fails_closed(phase_a: _PhaseA) -> None:
    assert _identity(phase_a.prepared_case) != OTHER_SHA256
    with pytest.raises(owner.MmiH2cPreparedCaseV1Error) as raised:
        owner.resume_mmi_h2c_prepared_case_run_context(
            prepared_case=phase_a.prepared_case,
            expected_prepared_case_identity_sha256=OTHER_SHA256,
        )
    assert raised.value.code == PREPARED_CASE_INVALID


def test_expected_identity_mismatch_fails_before_mint(
    phase_a: _PhaseA,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    called = False

    def _forbidden_mint(*, evaluation_timestamp_utc: str) -> object:
        nonlocal called
        called = True
        raise AssertionError(evaluation_timestamp_utc)

    monkeypatch.setattr(
        resumption._contracts,
        "mint_mmi_projection_run_context_from_canonical_timestamp",
        _forbidden_mint,
    )
    with pytest.raises(owner.MmiH2cPreparedCaseV1Error) as raised:
        owner.resume_mmi_h2c_prepared_case_run_context(
            prepared_case=phase_a.prepared_case,
            expected_prepared_case_identity_sha256=OTHER_SHA256,
        )
    assert raised.value.code == PREPARED_CASE_INVALID
    assert not called


@pytest.mark.parametrize(
    "expected",
    [
        pytest.param("", id="empty"),
        pytest.param("0" * 63, id="too-short"),
        pytest.param("0" * 65, id="too-long"),
        pytest.param("A" * 64, id="uppercase"),
        pytest.param(None, id="none"),
    ],
)
def test_malformed_expected_identity_fails_closed(
    phase_a: _PhaseA,
    expected: object,
) -> None:
    with pytest.raises(owner.MmiH2cPreparedCaseV1Error) as raised:
        owner.resume_mmi_h2c_prepared_case_run_context(
            prepared_case=phase_a.prepared_case,
            expected_prepared_case_identity_sha256=expected,  # type: ignore[arg-type]
        )
    assert raised.value.code == PREPARED_CASE_INVALID


def test_tampered_embedded_identity_fails_through_the_owning_validator(
    phase_a: _PhaseA,
) -> None:
    tampered = dict(phase_a.prepared_case)
    tampered[IDENTITY_FIELD] = OTHER_SHA256
    # The expected argument agrees with the tampered envelope, so only the
    # owning validator's recomputation can reject this.
    with pytest.raises(owner.MmiH2cPreparedCaseV1Error) as raised:
        owner.resume_mmi_h2c_prepared_case_run_context(
            prepared_case=tampered,
            expected_prepared_case_identity_sha256=OTHER_SHA256,
        )
    assert raised.value.code == PREPARED_CASE_INVALID
    with pytest.raises(owner.MmiH2cPreparedCaseV1Error):
        owner.validate_mmi_h2c_prepared_case_v1(prepared_case=tampered)


def test_tampered_timestamp_fails_through_prepared_case_validation(
    phase_a: _PhaseA,
) -> None:
    tampered = dict(phase_a.prepared_case)
    tampered["evaluation_timestamp_utc"] = "2026-07-27T15:30:45.123456Z"
    with pytest.raises(owner.MmiH2cPreparedCaseV1Error) as raised:
        _resume(tampered)
    assert raised.value.code == PREPARED_CASE_INVALID
    with pytest.raises(owner.MmiH2cPreparedCaseV1Error):
        owner.validate_mmi_h2c_prepared_case_v1(prepared_case=tampered)


def test_non_canonical_embedded_timestamp_never_reaches_the_mint(
    phase_a: _PhaseA,
) -> None:
    tampered = dict(phase_a.prepared_case)
    tampered["evaluation_timestamp_utc"] = "2026-07-26T15:30:45Z"
    with pytest.raises(owner.MmiH2cPreparedCaseV1Error) as raised:
        _resume(tampered)
    assert raised.value.code == PREPARED_CASE_INVALID


def test_invalid_artifact_fails_before_mint(
    phase_a: _PhaseA,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    called = False

    def _forbidden_mint(*, evaluation_timestamp_utc: str) -> object:
        nonlocal called
        called = True
        raise AssertionError(evaluation_timestamp_utc)

    tampered = dict(phase_a.prepared_case)
    tampered["evaluation_timestamp_utc"] = "2026-07-26T15:30:45Z"
    monkeypatch.setattr(
        resumption._contracts,
        "mint_mmi_projection_run_context_from_canonical_timestamp",
        _forbidden_mint,
    )
    with pytest.raises(owner.MmiH2cPreparedCaseV1Error) as raised:
        _resume(tampered)
    assert raised.value.code == PREPARED_CASE_INVALID
    assert not called


class _FlippingMapping(Mapping[str, object]):
    """A mapping that serves tampered content after its first read."""

    def __init__(
        self,
        valid: Mapping[str, object],
        tampered: Mapping[str, object],
    ) -> None:
        self._valid = dict(valid)
        self._tampered = dict(tampered)
        self.materializations = 0

    def _active(self) -> dict[str, object]:
        return self._valid if self.materializations <= 1 else self._tampered

    def __iter__(self) -> Iterator[str]:
        self.materializations += 1
        return iter(self._active())

    def __len__(self) -> int:
        return len(self._active())

    def __getitem__(self, key: str) -> object:
        return self._active()[key]


def test_unstable_mapping_cannot_bypass_snapshot_once(
    phase_a: _PhaseA,
) -> None:
    tampered = dict(phase_a.prepared_case)
    tampered["evaluation_timestamp_utc"] = "2026-07-27T15:30:45.123456Z"
    # The tampered content is genuinely rejected on its own, so serving it on
    # a second read would change the outcome if any second read existed.
    with pytest.raises(owner.MmiH2cPreparedCaseV1Error):
        owner.validate_mmi_h2c_prepared_case_v1(prepared_case=tampered)

    unstable = _FlippingMapping(phase_a.prepared_case, tampered)
    resumed = owner.resume_mmi_h2c_prepared_case_run_context(
        prepared_case=unstable,
        expected_prepared_case_identity_sha256=_identity(
            phase_a.prepared_case
        ),
    )
    assert unstable.materializations == 1
    assert resumed.evaluation_timestamp_utc == PHASE_A_TIMESTAMP


def test_resumption_mints_only_the_validated_snapshot_timestamp(
    phase_a: _PhaseA,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    actual_mint = (
        resumption._contracts
        .mint_mmi_projection_run_context_from_canonical_timestamp
    )
    observed: list[str] = []

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
    resumed = _resume(phase_a.prepared_case)
    assert observed == [PHASE_A_TIMESTAMP]
    assert resumed.evaluation_timestamp_utc == PHASE_A_TIMESTAMP


# --------------------------------------------------------------------------
# Exact T1 / G2 / prompt-byte reproduction.
# --------------------------------------------------------------------------
def test_resumed_context_reproduces_view_g2_and_exact_prompt_bytes(
    phase_a: _PhaseA,
) -> None:
    resumed = _resume(phase_a.prepared_case)
    rebuilt_view, rebuilt_prompt = _chain(
        policy_source=phase_a.policy_source,
        portfolio_source=phase_a.portfolio_source,
        run_context=resumed,
    )
    # Independently constructed fixed-clock expectations, not a second call
    # through the same context.
    assert rebuilt_view == phase_a.view
    assert (
        rebuilt_view["analyst_visible_evidence_view_identity_sha256"]
        == phase_a.view["analyst_visible_evidence_view_identity_sha256"]
    )
    assert rebuilt_view["evaluation_timestamp_utc"] == PHASE_A_TIMESTAMP
    assert rebuilt_prompt == phase_a.prompt

    rebuilt_text = rebuilt_prompt["prompt_text"]
    assert type(rebuilt_text) is str
    rebuilt_bytes = rebuilt_text.encode("utf-8")
    assert rebuilt_bytes == phase_a.prompt_bytes

    # Anchored to the persisted envelope's own recorded prompt digest.
    h1_prompt = phase_a.prepared_case["h1_prompt"]
    assert type(h1_prompt) is dict
    assert h1_prompt["sha256"] == hashlib.sha256(rebuilt_bytes).hexdigest()
    assert h1_prompt["byte_length"] == len(rebuilt_bytes)
    assert (
        phase_a.prepared_case["grounded_prompt"] == rebuilt_prompt
    )


def test_a_fresh_live_context_would_not_reproduce_the_persisted_chain(
    phase_a: _PhaseA,
) -> None:
    """A different evaluation time changes G2, which is why resumption exists."""
    other = mint_mmi_projection_run_context_from_canonical_timestamp(
        evaluation_timestamp_utc="2026-07-26T16:30:45.123456Z",
    )
    _, other_prompt = _chain(
        policy_source=phase_a.policy_source,
        portfolio_source=phase_a.portfolio_source,
        run_context=other,
    )
    assert other_prompt != phase_a.prompt
    other_text = other_prompt["prompt_text"]
    assert type(other_text) is str
    assert other_text.encode("utf-8") != phase_a.prompt_bytes


# --------------------------------------------------------------------------
# Deterministic clock oracles.
# --------------------------------------------------------------------------
def test_live_factory_still_reads_the_system_clock_object(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    patched = datetime(2026, 8, 5, 1, 2, 3, 456789, tzinfo=timezone.utc)

    class _PatchedSystemClock:
        def now_utc(self) -> datetime:
            return patched

    monkeypatch.setattr(contracts, "_SystemUtcClock", _PatchedSystemClock)
    context = begin_mmi_projection_run()
    assert context.evaluation_timestamp_utc == patched.strftime(
        CANONICAL_FORMAT
    )
    assert context.evaluation_time_utc == patched


def test_resumption_and_mint_never_touch_a_clock(
    phase_a: _PhaseA,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(contracts, "_SystemUtcClock", _ForbiddenClock)
    resumed = _resume(phase_a.prepared_case)
    assert resumed.evaluation_timestamp_utc == PHASE_A_TIMESTAMP
    minted = mint_mmi_projection_run_context_from_canonical_timestamp(
        evaluation_timestamp_utc=PHASE_A_TIMESTAMP,
    )
    assert minted.evaluation_timestamp_utc == PHASE_A_TIMESTAMP
    with pytest.raises(AssertionError):
        begin_mmi_projection_run()


# --------------------------------------------------------------------------
# Exact consumer and private-seam restrictions.
# --------------------------------------------------------------------------
def test_low_level_mint_has_exactly_one_production_consumer() -> None:
    consumers = [
        relative
        for relative, source in _production_sources()
        if relative != CONTRACTS_RELATIVE
        and "mint_mmi_projection_run_context_from_canonical_timestamp"
        in source
    ]
    assert consumers == [RESUMPTION_RELATIVE]


def test_offline_wrapper_delegates_to_the_current_resumption_owner() -> None:
    source = dict(_production_sources())[PREPARED_CASE_RELATIVE]
    assert (
        "investment_orchestrator.mmi.run_context_resumption" in source
    )
    assert (
        "mint_mmi_projection_run_context_from_canonical_timestamp"
        not in source
    )


def test_current_resumption_owner_has_exactly_one_production_consumer() -> None:
    consumers = [
        relative
        for relative, source in _production_sources()
        if relative != RESUMPTION_RELATIVE
        and _imports_current_resumption_owner(source)
    ]
    assert consumers == [PREPARED_CASE_RELATIVE]


def test_current_resumption_owner_has_no_bare_timestamp_api() -> None:
    signature = inspect.signature(
        resumption.resume_mmi_projection_run_context_from_validated_artifact
    )
    assert tuple(signature.parameters) == (
        "artifact",
        "expected_artifact_identity_sha256",
        "validate_artifact",
        "artifact_identity_field",
        "maximum_canonical_bytes",
    )
    assert all(
        item.kind is inspect.Parameter.KEYWORD_ONLY
        and item.default is inspect.Parameter.empty
        for item in signature.parameters.values()
    )
    assert not {
        name for name in signature.parameters if "timestamp" in name
    }
    assert (
        "mint_mmi_projection_run_context_from_canonical_timestamp"
        not in resumption.__all__
    )


def test_resumption_wrapper_has_zero_production_consumers() -> None:
    consumers = [
        relative
        for relative, source in _production_sources()
        if relative != PREPARED_CASE_RELATIVE
        and "resume_mmi_h2c_prepared_case_run_context" in source
    ]
    assert set(consumers) == {CONSUME_CASE_RELATIVE, ARCHIVED_SOURCE_RELATIVE}


@pytest.mark.parametrize(
    "seam",
    [
        "_begin_mmi_projection_run_with_clock",
        "_SystemUtcClock",
        "_new_mmi_projection_run_context",
    ],
)
def test_private_clock_seams_have_no_production_importer(seam: str) -> None:
    consumers = [
        relative
        for relative, source in _production_sources()
        if relative != CONTRACTS_RELATIVE and seam in source
    ]
    assert consumers == []


def test_contracts_owner_still_holds_every_private_clock_seam() -> None:
    source = dict(_production_sources())[CONTRACTS_RELATIVE]
    for seam in (
        "_begin_mmi_projection_run_with_clock",
        "_SystemUtcClock",
        "_new_mmi_projection_run_context",
    ):
        assert seam in source
