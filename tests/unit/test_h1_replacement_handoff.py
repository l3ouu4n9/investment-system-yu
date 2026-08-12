"""Composition, continuity, lifecycle, and authority tests for P2b.

These oracles prove only what the prepare/consume composition adds: the
completion-claim lifecycle, restart-safe source and prompt continuity, the
exactly-once response acquisition and its binding, the facts boundary, and H1's
continued dormancy.  Child validators keep their own existing matrices: the
stable reader's filesystem permutations, source capture's path permutations,
grounded-prompt internals, raw-response-envelope validation, H1 mapping
internals, the facts factory, and the availability bridge are not retested.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path

import pytest

from investment_orchestrator.cli import (
    run_h1_replacement_consume as consume_cli,
)
from investment_orchestrator.cli import (
    run_h1_replacement_prepare as prepare_cli,
)
from investment_orchestrator.common.paths import repo_root
from investment_orchestrator.mmi import contracts, source_capture
from investment_orchestrator.mmi.analyst_visible_evidence_view_v2 import (
    build_mmi_analyst_visible_evidence_view_v2,
)
from investment_orchestrator.mmi.canonical import (
    MAXIMUM_MMI_RAW_RESPONSE_BYTES,
)
from investment_orchestrator.mmi.contracts import (
    MmiCapturedSource,
    MmiSourceRole,
    _begin_mmi_projection_run_with_clock,
)
from investment_orchestrator.mmi.evidence_bundle import (
    build_mmi_authenticated_evidence_bundle,
)
from investment_orchestrator.mmi.grounded_prompt_v2 import (
    build_mmi_grounded_prompt_v2,
)
from investment_orchestrator.mmi.mmi_h1_prepared_handoff_v1 import (
    validate_mmi_h1_prepared_handoff_v1,
)
from investment_orchestrator.mmi.policy_projection import (
    build_mmi_policy_projection,
)
from investment_orchestrator.mmi.portfolio_projection import (
    build_mmi_portfolio_snapshot_projection,
)
from investment_orchestrator.research.h1_mapped_recognition import (
    H1MappedRecognitionFacts,
)
from investment_orchestrator.workflow import h1_replacement_handoff as handoff
from investment_orchestrator.workflow.h1_replacement_handoff import (
    H1ReplacementHandoffError,
    H1ReplacementHandoffErrorCode as Code,
    consume_h1_replacement_handoff,
    prepare_h1_replacement_handoff,
)

import _mmi_hermetic_source_checkout as hermetic


PREPARED_TIME = datetime(2026, 7, 31, 12, tzinfo=timezone.utc)
PREPARED_TIMESTAMP = "2026-07-31T12:00:00.000000Z"
PREPARED_LEAF = "h1_prepared_handoff.json"
RESPONSE_LEAF = "h1_response.raw"
MAPPING_LEAF = "h1_legacy_step1_mapping_report.json"
OTHER_SHA256 = "1" * 64

PRODUCTION_ROOT = "src/investment_orchestrator"
NEW_PRODUCTION_PATHS = (
    f"{PRODUCTION_ROOT}/cli/run_h1_replacement_consume.py",
    f"{PRODUCTION_ROOT}/cli/run_h1_replacement_prepare.py",
    f"{PRODUCTION_ROOT}/mmi/mmi_h1_prepared_handoff_v1.py",
    f"{PRODUCTION_ROOT}/workflow/h1_replacement_handoff.py",
)
EXISTING_H1_AVAILABILITY_PARAMETER_PATHS = {
    f"{PRODUCTION_ROOT}/state/research_availability.py",
    f"{PRODUCTION_ROOT}/workflow/step1_research.py",
}


class _FixedClock:
    def now_utc(self) -> datetime:
        return PREPARED_TIME


@dataclass(frozen=True, slots=True)
class _Environment:
    checkout_root: Path
    directory: Path
    strategy_raw: bytes
    portfolio_raw: bytes

    @property
    def strategy_sha256(self) -> str:
        return hashlib.sha256(self.strategy_raw).hexdigest()

    @property
    def portfolio_sha256(self) -> str:
        return hashlib.sha256(self.portfolio_raw).hexdigest()

    def leaf(self, name: str) -> Path:
        return self.directory / name

    def names(self) -> list[str]:
        if not self.directory.exists():
            return []
        return sorted(item.name for item in self.directory.iterdir())

    def install_portfolio(self) -> None:
        hermetic.install_source(
            self.checkout_root,
            role=MmiSourceRole.PORTFOLIO_SNAPSHOT,
            raw=self.portfolio_raw,
        )

    def remove_portfolio(self) -> None:
        (
            self.checkout_root / hermetic.PORTFOLIO_SNAPSHOT_LOCATOR
        ).unlink()


@pytest.fixture
def env(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> _Environment:
    """One hermetic checkout plus a private code-owned handoff directory."""
    checkout_root = tmp_path / "checkout"
    (checkout_root / "inputs" / "current").mkdir(parents=True)
    directory = tmp_path / "artifacts" / "current" / "h1_replacement"

    strategy_raw = hermetic.strategy_settings_bytes()
    portfolio_raw = hermetic.portfolio_snapshot_bytes()
    hermetic.install_source(
        checkout_root,
        role=MmiSourceRole.STRATEGY_SETTINGS,
        raw=strategy_raw,
    )
    hermetic.install_source(
        checkout_root,
        role=MmiSourceRole.PORTFOLIO_SNAPSHOT,
        raw=portfolio_raw,
    )

    def _capture(role, *, expected_source_sha256):
        return source_capture._capture_mmi_source_at_root(
            checkout_root,
            role=role,
            expected_source_sha256=expected_source_sha256,
        )

    def _absence(role):
        return source_capture._capture_mmi_source_absence_at_root(
            checkout_root,
            role=role,
        )

    monkeypatch.setattr(
        source_capture, "capture_current_mmi_source", _capture
    )
    monkeypatch.setattr(
        source_capture, "capture_current_mmi_source_absence", _absence
    )
    monkeypatch.setattr(contracts, "_SystemUtcClock", _FixedClock)
    monkeypatch.setattr(handoff, "_handoff_directory", lambda: directory)
    return _Environment(
        checkout_root=checkout_root,
        directory=directory,
        strategy_raw=strategy_raw,
        portfolio_raw=portfolio_raw,
    )


# --------------------------------------------------------------------------
# Independent chain oracle: the same public owners, driven by the test.
# --------------------------------------------------------------------------
def _independent_prompt(
    env: _Environment,
    *,
    with_portfolio: bool,
) -> tuple[dict[str, object], dict[str, object]]:
    run_context = _begin_mmi_projection_run_with_clock(_FixedClock())
    assert run_context.evaluation_timestamp_utc == PREPARED_TIMESTAMP
    policy_source = _capture(env, MmiSourceRole.STRATEGY_SETTINGS)
    portfolio_source = (
        _capture(env, MmiSourceRole.PORTFOLIO_SNAPSHOT)
        if with_portfolio
        else None
    )
    policy_result = build_mmi_policy_projection(
        policy_source,
        run_context=run_context,
    )
    assert policy_result.valid, policy_result.reason_codes
    policy = dict(policy_result.projection or {})
    portfolio: dict[str, object] | None = None
    if portfolio_source is not None:
        portfolio_result = build_mmi_portfolio_snapshot_projection(
            portfolio_source,
            policy_projection=policy,
            policy_source=policy_source,
            run_context=run_context,
        )
        assert portfolio_result.valid, portfolio_result.reason_codes
        portfolio = dict(portfolio_result.projection or {})
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
    return view, prompt


def _capture(env: _Environment, role: MmiSourceRole) -> MmiCapturedSource:
    raw = (
        env.strategy_raw
        if role is MmiSourceRole.STRATEGY_SETTINGS
        else env.portfolio_raw
    )
    result = source_capture._capture_mmi_source_at_root(
        env.checkout_root,
        role=role,
        expected_source_sha256=hashlib.sha256(raw).hexdigest(),
    )
    assert result.valid, result.reason_codes
    assert result.source is not None
    return result.source


def _response_bytes(
    view: dict[str, object],
    prompt: dict[str, object],
) -> bytes:
    policy_view = view["policy_view"]
    assert type(policy_view) is dict
    instruments = policy_view["analysis_instruments"]
    assert type(instruments) is list
    rows = [
        {
            "ticker": item["ticker"],
            "evidence_status": "EVIDENCE_SUPPORTED",
            "rationale_12m_plus": "Evidence-linked qualitative rationale.",
            "references": [f"POLICY.INSTRUMENT.{index:04d}"],
        }
        for index, item in enumerate(instruments, start=1)
        if type(item) is dict
    ]
    binding = prompt["prompt_context_binding_sha256"]
    assert type(binding) is str
    qualitative = {
        "text": "Report-only qualitative observation.",
        "references": ["VIEW.EVALUATION_TIMESTAMP"],
        "hypothesis": False,
    }
    return json.dumps(
        {
            "response_schema_version": "mmi_grounded_analysis_response_v2",
            "prompt_context_binding_sha256": binding,
            "analysis_status": "QUALITATIVE_ANALYSIS_PROVIDED",
            "instrument_views": rows,
            "anchor_associations_status": "UNAVAILABLE",
            "scheduled_events_status": "UNAVAILABLE",
            "regime_observation_status": "UNAVAILABLE",
            "evidence_observations": [qualitative],
            "risks": [],
            "uncertainties": [],
            "contradictions": [],
            "research_questions": [],
            "summary": dict(qualitative),
        },
        ensure_ascii=False,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def _prepare_present(env: _Environment):
    return prepare_h1_replacement_handoff(
        strategy_settings_expected_sha256=env.strategy_sha256,
        portfolio_snapshot_expected_sha256=env.portfolio_sha256,
        portfolio_snapshot_absent=False,
    )


def _prepare_absent(env: _Environment):
    return prepare_h1_replacement_handoff(
        strategy_settings_expected_sha256=env.strategy_sha256,
        portfolio_snapshot_expected_sha256=None,
        portfolio_snapshot_absent=True,
    )


def _published_handoff(env: _Environment) -> dict[str, object]:
    return json.loads(env.leaf(PREPARED_LEAF).read_bytes())


# --------------------------------------------------------------------------
# Prepare lifecycle.
# --------------------------------------------------------------------------
def test_prepare_invalidates_stale_claims_and_publishes_the_handoff_last(
    env: _Environment,
) -> None:
    env.directory.mkdir(parents=True)
    env.leaf(MAPPING_LEAF).write_bytes(b"stale mapping completion\n")
    env.leaf(PREPARED_LEAF).write_bytes(b"stale prepared handoff\n")

    # Exactly one portfolio input is accepted; neither malformed call may
    # touch a completion claim.
    for expected_sha256, absent in (
        (env.portfolio_sha256, True),
        (None, False),
    ):
        with pytest.raises(H1ReplacementHandoffError) as raised:
            prepare_h1_replacement_handoff(
                strategy_settings_expected_sha256=env.strategy_sha256,
                portfolio_snapshot_expected_sha256=expected_sha256,
                portfolio_snapshot_absent=absent,
            )
        assert raised.value.code is Code.H1_HANDOFF_ARGUMENT_INVALID
    assert env.names() == [MAPPING_LEAF, PREPARED_LEAF]

    # A prepare that fails a required deterministic step still invalidates
    # both stale claims first and publishes nothing.
    with pytest.raises(H1ReplacementHandoffError) as raised:
        prepare_h1_replacement_handoff(
            strategy_settings_expected_sha256="0" * 64,
            portfolio_snapshot_expected_sha256=env.portfolio_sha256,
            portfolio_snapshot_absent=False,
        )
    assert raised.value.code is Code.H1_HANDOFF_SOURCE_CAPTURE_INVALID
    assert env.names() == []

    result = _prepare_present(env)
    assert result.workflow_status == "AWAITING_OPERATOR_RESPONSE"
    assert result.portfolio_snapshot_presence == "PRESENT"
    # The prepared handoff is the sole preparation completion claim: no
    # mapping report and no publication temporary survive.
    assert env.names() == [PREPARED_LEAF]

    published = _published_handoff(env)
    assert (
        validate_mmi_h1_prepared_handoff_v1(prepared_handoff=published)
        == published
    )
    assert published["prepared_handoff_identity_sha256"] == (
        result.prepared_handoff_identity_sha256
    )
    assert published["evaluation_timestamp_utc"] == PREPARED_TIMESTAMP
    assert published["strategy_settings_source_sha256"] == (
        env.strategy_sha256
    )
    assert published["portfolio_snapshot_source_sha256"] == (
        env.portfolio_sha256
    )
    _, prompt = _independent_prompt(env, with_portfolio=True)
    assert published["grounded_prompt"] == prompt
    assert result.prompt_text == prompt["prompt_text"]


# --------------------------------------------------------------------------
# End-to-end consume, facts, and completion lifecycle.
# --------------------------------------------------------------------------
def test_present_portfolio_consume_builds_facts_and_claims_mapping_last(
    env: _Environment,
) -> None:
    prepared = _prepare_present(env)
    view, prompt = _independent_prompt(env, with_portfolio=True)
    env.leaf(RESPONSE_LEAF).write_bytes(_response_bytes(view, prompt))

    consumed = consume_h1_replacement_handoff(
        expected_prepared_handoff_identity_sha256=(
            prepared.prepared_handoff_identity_sha256
        ),
    )
    assert consumed.workflow_status == "COMPLETED"
    assert consumed.portfolio_snapshot_presence == "PRESENT"
    assert env.names() == [MAPPING_LEAF, PREPARED_LEAF, RESPONSE_LEAF]

    facts = consumed.mapped_recognition_facts
    assert type(facts) is H1MappedRecognitionFacts
    assert facts.source_kind == "H1_ROLE_MAPPED"
    assert facts.context_evaluation_timestamp_utc == PREPARED_TIMESTAMP
    assert facts.mapping_report_identity_sha256 == (
        consumed.mapping_report_identity_sha256
    )
    assert facts.raw_response_sha256 == hashlib.sha256(
        env.leaf(RESPONSE_LEAF).read_bytes()
    ).hexdigest()

    mapping_bytes = env.leaf(MAPPING_LEAF).read_bytes()
    mapping = json.loads(mapping_bytes)
    assert mapping["schema_version"] == (
        "mmi_h1_legacy_step1_mapping_report_v1"
    )
    assert mapping["report_only"] is True
    assert mapping["authority_effect"] == "NONE"
    assert mapping["mapping_report_identity_sha256"] == (
        consumed.mapping_report_identity_sha256
    )
    # Facts are in-memory only: no durable artifact carries the bridge shape.
    assert b"H1_ROLE_MAPPED" not in mapping_bytes
    assert b"H1_ROLE_MAPPED" not in env.leaf(PREPARED_LEAF).read_bytes()

    # Re-consuming the same valid prepared handoff is supported and stable.
    again = consume_h1_replacement_handoff(
        expected_prepared_handoff_identity_sha256=(
            prepared.prepared_handoff_identity_sha256
        ),
    )
    assert again.mapping_report_identity_sha256 == (
        consumed.mapping_report_identity_sha256
    )
    assert env.leaf(MAPPING_LEAF).read_bytes() == mapping_bytes

    # A substituted expected identity cannot select whichever handoff exists,
    # and the prior completion has already been invalidated by then.
    with pytest.raises(H1ReplacementHandoffError) as raised:
        consume_h1_replacement_handoff(
            expected_prepared_handoff_identity_sha256=OTHER_SHA256,
        )
    assert raised.value.code is Code.H1_HANDOFF_PREPARED_HANDOFF_INVALID
    assert env.names() == [PREPARED_LEAF, RESPONSE_LEAF]

    # A new prepare after a prior successful consume removes both old claims
    # and itself creates no consume completion.
    env.leaf(MAPPING_LEAF).write_bytes(mapping_bytes)
    reprepared = _prepare_present(env)
    assert reprepared.prepared_handoff_identity_sha256 == (
        prepared.prepared_handoff_identity_sha256
    )
    assert env.names() == [PREPARED_LEAF, RESPONSE_LEAF]


# --------------------------------------------------------------------------
# Source continuity.
# --------------------------------------------------------------------------
def test_changed_sources_or_portfolio_presence_fail_consume_closed(
    env: _Environment,
) -> None:
    prepared = _prepare_present(env)
    view, prompt = _independent_prompt(env, with_portfolio=True)
    env.leaf(RESPONSE_LEAF).write_bytes(_response_bytes(view, prompt))
    expected = prepared.prepared_handoff_identity_sha256

    changed = hermetic.strategy_settings_bytes(
        run_timestamp_et="2026-07-26 11:00 ET",
    )
    assert changed != env.strategy_raw
    hermetic.install_source(
        env.checkout_root,
        role=MmiSourceRole.STRATEGY_SETTINGS,
        raw=changed,
    )
    with pytest.raises(H1ReplacementHandoffError) as raised:
        consume_h1_replacement_handoff(
            expected_prepared_handoff_identity_sha256=expected,
        )
    assert raised.value.code is Code.H1_HANDOFF_SOURCE_CAPTURE_INVALID
    assert raised.value.owner_reason_codes == (
        "MMI_SOURCE_EXPECTED_SHA256_MISMATCH",
    )
    assert MAPPING_LEAF not in env.names()

    hermetic.install_source(
        env.checkout_root,
        role=MmiSourceRole.STRATEGY_SETTINGS,
        raw=env.strategy_raw,
    )
    env.remove_portfolio()
    with pytest.raises(H1ReplacementHandoffError) as raised:
        consume_h1_replacement_handoff(
            expected_prepared_handoff_identity_sha256=expected,
        )
    assert raised.value.code is Code.H1_HANDOFF_SOURCE_CAPTURE_INVALID
    assert raised.value.owner_reason_codes == ("MMI_SOURCE_MISSING",)
    assert MAPPING_LEAF not in env.names()

    # The mirror case: a handoff prepared against a proven-absent portfolio
    # must not consume once a portfolio source exists again.
    absent_prepared = _prepare_absent(env)
    assert (
        _published_handoff(env)["portfolio_snapshot_source_sha256"] is None
    )
    env.install_portfolio()
    with pytest.raises(H1ReplacementHandoffError) as raised:
        consume_h1_replacement_handoff(
            expected_prepared_handoff_identity_sha256=(
                absent_prepared.prepared_handoff_identity_sha256
            ),
        )
    assert raised.value.code is Code.H1_HANDOFF_PORTFOLIO_PRESENCE_INVALID
    assert raised.value.owner_reason_codes == ("MMI_SOURCE_PRESENT",)
    assert MAPPING_LEAF not in env.names()


# --------------------------------------------------------------------------
# Complete prompt equality.
# --------------------------------------------------------------------------
def test_prompt_equality_is_complete_and_not_text_only(
    env: _Environment,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    prepared = _prepare_present(env)
    view, prompt = _independent_prompt(env, with_portfolio=True)
    env.leaf(RESPONSE_LEAF).write_bytes(_response_bytes(view, prompt))

    real_validate = handoff._grounded_prompt_v2.validate_mmi_grounded_prompt_v2

    def _mutating_validate(**kwargs):
        validated = dict(real_validate(**kwargs))
        validated["grounded_prompt_artifact_identity_sha256"] = OTHER_SHA256
        return validated

    monkeypatch.setattr(
        handoff._grounded_prompt_v2,
        "validate_mmi_grounded_prompt_v2",
        _mutating_validate,
    )
    with pytest.raises(H1ReplacementHandoffError) as raised:
        consume_h1_replacement_handoff(
            expected_prepared_handoff_identity_sha256=(
                prepared.prepared_handoff_identity_sha256
            ),
        )
    assert raised.value.code is Code.H1_HANDOFF_PROMPT_CONTINUITY_INVALID
    assert MAPPING_LEAF not in env.names()

    # The rejected object differed from the prepared one in exactly one
    # non-text field, so a prompt-text-only comparison would have passed.
    embedded = _published_handoff(env)["grounded_prompt"]
    assert type(embedded) is dict
    mutated = dict(embedded)
    mutated["grounded_prompt_artifact_identity_sha256"] = OTHER_SHA256
    assert mutated["prompt_text"] == embedded["prompt_text"]
    assert mutated != embedded


# --------------------------------------------------------------------------
# Exactly-one response acquisition and same-object binding.
# --------------------------------------------------------------------------
def test_response_is_acquired_once_only_after_continuity_and_binds_that_object(
    env: _Environment,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    reads: list[tuple[str, int]] = []
    acquired: list[bytes] = []
    real_read = handoff.stable_read_exact_bytes

    def _recording_read(directory_fd, relative_path, *, maximum_bytes):
        result = real_read(
            directory_fd,
            relative_path,
            maximum_bytes=maximum_bytes,
        )
        reads.append((relative_path, maximum_bytes))
        if relative_path == RESPONSE_LEAF:
            acquired.append(result)
        return result

    bound: list[bytes] = []
    real_envelope = (
        handoff._raw_response_v2.build_mmi_raw_response_envelope_v2
    )

    def _recording_envelope(*, raw_response_bytes, **kwargs):
        bound.append(raw_response_bytes)
        return real_envelope(raw_response_bytes=raw_response_bytes, **kwargs)

    monkeypatch.setattr(handoff, "stable_read_exact_bytes", _recording_read)
    monkeypatch.setattr(
        handoff._raw_response_v2,
        "build_mmi_raw_response_envelope_v2",
        _recording_envelope,
    )

    prepared = _prepare_present(env)
    view, prompt = _independent_prompt(env, with_portfolio=True)
    env.leaf(RESPONSE_LEAF).write_bytes(_response_bytes(view, prompt))
    # Preparation never touches the response leaf.
    assert [name for name, _ in reads if name == RESPONSE_LEAF] == []
    expected = prepared.prepared_handoff_identity_sha256

    # A source-continuity failure must abort before any response acquisition.
    env.remove_portfolio()
    with pytest.raises(H1ReplacementHandoffError):
        consume_h1_replacement_handoff(
            expected_prepared_handoff_identity_sha256=expected,
        )
    assert [name for name, _ in reads if name == RESPONSE_LEAF] == []
    assert bound == []

    env.install_portfolio()
    reads.clear()
    consumed = consume_h1_replacement_handoff(
        expected_prepared_handoff_identity_sha256=expected,
    )
    assert consumed.workflow_status == "COMPLETED"

    response_reads = [item for item in reads if item[0] == RESPONSE_LEAF]
    assert response_reads == [(RESPONSE_LEAF, MAXIMUM_MMI_RAW_RESPONSE_BYTES)]
    assert len(acquired) == 1
    assert len(bound) == 1
    # Exactly the acquired object is what the envelope owner received.
    assert bound[0] is acquired[0]
    assert bound[0] == env.leaf(RESPONSE_LEAF).read_bytes()


# --------------------------------------------------------------------------
# Proven-absent portfolio.
# --------------------------------------------------------------------------
def test_absent_portfolio_prepares_and_then_fails_closed_at_the_facts_boundary(
    env: _Environment,
) -> None:
    env.remove_portfolio()
    prepared = _prepare_absent(env)
    assert prepared.portfolio_snapshot_presence == "PROVEN_ABSENT"
    published = _published_handoff(env)
    assert published["portfolio_snapshot_source_sha256"] is None
    assert published["strategy_settings_source_sha256"] == (
        env.strategy_sha256
    )

    view, prompt = _independent_prompt(env, with_portfolio=False)
    assert published["grounded_prompt"] == prompt
    env.leaf(RESPONSE_LEAF).write_bytes(_response_bytes(view, prompt))

    # Restart and continuity succeed, then the existing H1 mapping contract
    # refuses an absent portfolio.  No completion may be published.
    with pytest.raises(H1ReplacementHandoffError) as raised:
        consume_h1_replacement_handoff(
            expected_prepared_handoff_identity_sha256=(
                prepared.prepared_handoff_identity_sha256
            ),
        )
    assert raised.value.code is Code.H1_HANDOFF_MAPPING_INVALID
    assert raised.value.owner_reason_codes == (
        "MMI_H1_LEGACY_MAPPING_UPSTREAM_ARTIFACT_INVALID",
    )
    assert env.names() == [PREPARED_LEAF, RESPONSE_LEAF]

    # A broken checkout is never read as absence.
    (env.checkout_root / "inputs" / "current").rename(
        env.checkout_root / "inputs" / "moved"
    )
    with pytest.raises(H1ReplacementHandoffError) as raised:
        consume_h1_replacement_handoff(
            expected_prepared_handoff_identity_sha256=(
                prepared.prepared_handoff_identity_sha256
            ),
        )
    assert raised.value.code is Code.H1_HANDOFF_SOURCE_CAPTURE_INVALID
    assert env.names() == [PREPARED_LEAF, RESPONSE_LEAF]


# --------------------------------------------------------------------------
# Manual operator handoff surface.
# --------------------------------------------------------------------------
def _options(parser) -> set[str]:
    return {
        option
        for action in parser._actions
        for option in action.option_strings
    }


def test_cli_emits_only_the_exact_prompt_and_offers_no_automation_option(
    env: _Environment,
    capsysbinary: pytest.CaptureFixture[bytes],
) -> None:
    exit_code = prepare_cli.main(
        [
            "--strategy-settings-expected-sha256",
            env.strategy_sha256,
            "--portfolio-snapshot-expected-sha256",
            env.portfolio_sha256,
        ]
    )
    assert exit_code == 0
    prepared_stream = capsysbinary.readouterr()
    view, prompt = _independent_prompt(env, with_portfolio=True)
    # Standard output is the exact prompt bytes and nothing else.
    assert prepared_stream.out == prompt["prompt_text"].encode("utf-8")
    published = _published_handoff(env)
    identity = published["prepared_handoff_identity_sha256"]
    assert type(identity) is str
    assert identity.encode("ascii") in prepared_stream.err
    assert b"workflow_status=AWAITING_OPERATOR_RESPONSE" in prepared_stream.err

    env.leaf(RESPONSE_LEAF).write_bytes(_response_bytes(view, prompt))
    assert (
        consume_cli.main(
            ["--expected-prepared-handoff-identity-sha256", identity]
        )
        == 0
    )
    consumed_stream = capsysbinary.readouterr()
    assert b"workflow_status=COMPLETED" in consumed_stream.out
    assert env.leaf(MAPPING_LEAF).exists()

    # The operator surface offers no provider, model, network, response-path,
    # or availability-activation option.
    assert _options(prepare_cli._parser()) == {
        "-h",
        "--help",
        "--strategy-settings-expected-sha256",
        "--portfolio-snapshot-expected-sha256",
        "--portfolio-snapshot-absent",
    }
    assert _options(consume_cli._parser()) == {
        "-h",
        "--help",
        "--expected-prepared-handoff-identity-sha256",
    }
    with pytest.raises(SystemExit):
        prepare_cli.main(
            [
                "--strategy-settings-expected-sha256",
                env.strategy_sha256,
                "--portfolio-snapshot-expected-sha256",
                env.portfolio_sha256,
                "--portfolio-snapshot-absent",
            ]
        )


# --------------------------------------------------------------------------
# Authority isolation.
# --------------------------------------------------------------------------
def _imports(relative_path: str) -> set[str]:
    tree = ast.parse(
        (repo_root() / relative_path).read_text(encoding="utf-8"),
        filename=relative_path,
    )
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.module is not None:
                names.add(node.module)
    return names


def test_p2b_wires_no_availability_and_no_current_to_offline_dependency() -> None:
    forbidden_prefixes = (
        "investment_orchestrator.offline",
        "investment_orchestrator.state",
        "investment_orchestrator.permissions",
        "investment_orchestrator.orders",
        "investment_orchestrator.broker",
        "investment_orchestrator.observability",
        "investment_orchestrator.workflow.step1_research",
        "openai",
        "anthropic",
        "langchain",
        "google.generativeai",
        "cohere",
        "requests",
        "httpx",
        "urllib",
        "socket",
        "subprocess",
        "importlib",
        "time",
        "asyncio",
        "threading",
    )
    for relative_path in NEW_PRODUCTION_PATHS:
        for imported in _imports(relative_path):
            assert not any(
                imported == prefix or imported.startswith(f"{prefix}.")
                for prefix in forbidden_prefixes
            ), (relative_path, imported)

    # H1 stays dormant: the availability parameter exists only where it
    # already did, and no P2b module mentions it or the availability owner.
    mentioning = {
        path.relative_to(repo_root()).as_posix()
        for path in sorted(
            (repo_root() / PRODUCTION_ROOT).rglob("*.py")
        )
        if "h1_mapped_facts" in path.read_text(encoding="utf-8")
    }
    assert mentioning == EXISTING_H1_AVAILABILITY_PARAMETER_PATHS

    composer_path = (
        f"{PRODUCTION_ROOT}/workflow/h1_replacement_handoff.py"
    )
    composer = (repo_root() / composer_path).read_text(encoding="utf-8")
    for marker in (
        "evaluate_research_availability",
        "h1_mapped_facts",
        "--response-file",
    ):
        assert marker not in composer, marker

    # Exactly one response-acquisition site exists, and it is the private
    # helper that reads the code-owned leaf once.
    tree = ast.parse(composer, filename=composer_path)
    response_leaf_uses = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Name)
        and node.id == "_RESPONSE_LEAF"
        and isinstance(node.ctx, ast.Load)
    ]
    assert len(response_leaf_uses) == 1
    assert [
        node.name
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef)
        and any(
            isinstance(inner, ast.Name)
            and inner.id == "_RESPONSE_LEAF"
            and isinstance(inner.ctx, ast.Load)
            for inner in ast.walk(node)
        )
    ] == ["_acquire_response_bytes"]
