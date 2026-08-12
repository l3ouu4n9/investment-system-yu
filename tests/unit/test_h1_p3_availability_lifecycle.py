"""P3 lifecycle wiring: an H1 availability claim never outlives its completion.

These oracles prove only what P3 adds — the pre-attempt clear, its ordering
relative to each P2b engine, the post-consume success refresh and the exact
object it carries, and the fail-closed result of a failed clear or an absent
Legacy base context.

Everything else keeps its existing owner and is deliberately not retested here:
the H1 action table and STRICT_STALE precedence (``test_h1_mapped_availability``),
the Legacy availability policy (``test_research_availability``), the Step 2/3/4
and final-safety matrices, and the P2b completion-claim invalidation ordering
(``test_h1_replacement_handoff``).  One Step 2 gate assertion appears below, used
only as the downstream oracle for a missing permission artifact.
"""

from __future__ import annotations

from dataclasses import dataclass, fields
import errno
import json
from pathlib import Path

import pytest

from investment_orchestrator.cli import (
    run_h1_replacement_consume as consume_cli,
)
from investment_orchestrator.cli import (
    run_h1_replacement_prepare as prepare_cli,
)
from investment_orchestrator.common.io import write_json
from investment_orchestrator.state.research_degraded_mode_gate import (
    MISSING_RESEARCH_PERMISSION,
    load_and_evaluate_step2_research_gate,
)
from investment_orchestrator.research.h1_mapped_recognition import (
    H1MappedRecognitionFacts,
)
from investment_orchestrator.workflow import step1_research
from investment_orchestrator.workflow.h1_replacement_handoff import (
    H1ReplacementHandoffError,
    H1ReplacementHandoffErrorCode as Code,
)


H1_STATE = "H1_MAPPED_FRESH_NON_ACTIONABLE"
IDENTITY = "a" * 64
PROMPT_TEXT = "exact grounded prompt bytes"


# --------------------------------------------------------------------------
# Isolated Step 1 root.
# --------------------------------------------------------------------------
@dataclass(frozen=True)
class _Step1Root:
    root: Path
    decision_path: Path
    availability_path: Path
    freshness_path: Path

    @property
    def state_paths(self) -> tuple[Path, ...]:
        return (self.decision_path, self.availability_path, self.freshness_path)


@pytest.fixture
def step1_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> _Step1Root:
    """Redirect the Step 1 root so no test can touch the working tree."""
    monkeypatch.setattr(step1_research, "repo_root", lambda: tmp_path)
    return _Step1Root(
        root=tmp_path,
        decision_path=step1_research.step1_research_degraded_mode_decision_path(),
        availability_path=step1_research.step1_research_availability_path(),
        freshness_path=step1_research.step1_research_freshness_report_path(),
    )


def _write_stale_h1_claim(step1_root: _Step1Root) -> None:
    """Publish an H1 recognition claim into all three state artifacts."""
    for path in step1_root.state_paths:
        write_json(
            path,
            {
                "state": H1_STATE,
                "research_availability": H1_STATE.lower(),
                "allowed_actions": ["HOLD", "NO_TRADE"],
                "blocked_actions": ["SELL", "NEW_BUY", "ORDER_COMPILATION"],
                "manual_review_required": False,
                "h1_mapped_selected": True,
            },
        )


def _write_legacy_base_context(step1_root: _Step1Root) -> None:
    """Provide the minimum current Legacy inputs a rebuild needs to run."""
    write_json(step1_research.step1_research_handoff_candidate_path(), {})
    write_json(step1_research.step1_research_output_path(), {})


def _fresh_h1_facts() -> H1MappedRecognitionFacts:
    """Build typed facts while leaving their upstream factory contract in its own suite."""
    facts = object.__new__(H1MappedRecognitionFacts)
    for field in fields(H1MappedRecognitionFacts):
        object.__setattr__(facts, field.name, f"{field.name}_value")
    object.__setattr__(facts, "source_kind", "H1_ROLE_MAPPED")
    object.__setattr__(facts, "policy_as_of_date", "2026-06-25")
    object.__setattr__(facts, "portfolio_source_date", "2026-06-25")
    object.__setattr__(
        facts, "policy_source_run_timestamp_utc", "2026-06-25T09:00:00.000000Z"
    )
    object.__setattr__(
        facts, "context_evaluation_timestamp_utc", "2026-06-25T12:00:00.000000Z"
    )
    return facts


def _h1_claiming_artifacts(step1_root: _Step1Root) -> list[str]:
    """Return the names of state artifacts still asserting H1 recognition."""
    claiming: list[str] = []
    for path in step1_root.state_paths:
        if not path.is_file():
            continue
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except ValueError:  # pragma: no cover - malformed is not a claim
            continue
        if isinstance(payload, dict) and (
            payload.get("state") == H1_STATE
            or payload.get("h1_mapped_selected") is True
        ):
            claiming.append(path.name)
    return claiming


# --------------------------------------------------------------------------
# Engine stand-ins.  The real engines keep their own tests; P3 only needs to
# observe exactly when they run and what they hand back.
# --------------------------------------------------------------------------
@dataclass(frozen=True)
class _FakePrepareResult:
    workflow_status: str = "AWAITING_OPERATOR_RESPONSE"
    prepared_handoff_identity_sha256: str = IDENTITY
    portfolio_snapshot_presence: str = "PRESENT"
    prompt_text: str = PROMPT_TEXT


@dataclass(frozen=True)
class _FakeConsumeResult:
    mapped_recognition_facts: object
    workflow_status: str = "COMPLETED"
    prepared_handoff_identity_sha256: str = IDENTITY
    mapping_report_identity_sha256: str = "b" * 64
    portfolio_snapshot_presence: str = "PRESENT"


@dataclass(frozen=True)
class _StubAvailability:
    state: str = "INVALID_CONTRACT"
    h1_mapped_selected: bool = False


def _prepare_argv() -> list[str]:
    return [
        "--strategy-settings-expected-sha256",
        IDENTITY,
        "--portfolio-snapshot-expected-sha256",
        "c" * 64,
    ]


def _consume_argv() -> list[str]:
    return ["--expected-prepared-handoff-identity-sha256", IDENTITY]


# --------------------------------------------------------------------------
# Lifecycle.
# --------------------------------------------------------------------------
def test_successful_consume_clears_first_then_refreshes_with_the_exact_facts(
    step1_root: _Step1Root,
    monkeypatch: pytest.MonkeyPatch,
    capsysbinary: pytest.CaptureFixture[bytes],
) -> None:
    _write_stale_h1_claim(step1_root)
    _write_legacy_base_context(step1_root)

    # A sentinel: any copy, reserialization, or rebuild from the mapping report
    # would produce a different object and fail the identity assertion below.
    facts = object()
    events: list[tuple[str, object]] = []

    def fake_consume(*, expected_prepared_handoff_identity_sha256: str):
        events.append(("consume", _h1_claiming_artifacts(step1_root)))
        return _FakeConsumeResult(mapped_recognition_facts=facts)

    def fake_evaluate(*, h1_mapped_facts=None, **_kwargs):
        events.append(("evaluate", h1_mapped_facts))
        return _StubAvailability(), {}

    monkeypatch.setattr(
        consume_cli, "consume_h1_replacement_handoff", fake_consume
    )
    monkeypatch.setattr(
        step1_research,
        "_evaluate_research_availability_report_only",
        fake_evaluate,
    )

    assert consume_cli.main(_consume_argv()) == 0

    # Clear, then engine, then success refresh — in exactly that order.
    assert [name for name, _ in events] == ["evaluate", "consume", "evaluate"]
    # The clear carried no facts and left no claim for the engine to contradict.
    assert events[0][1] is None
    assert events[1][1] == []
    # The refresh received the engine's object itself, not an equal copy.
    assert events[2][1] is facts

    stream = capsysbinary.readouterr()
    assert b"workflow_status=COMPLETED" in stream.out
    assert b"mapping_report_identity_sha256=" + b"b" * 64 in stream.out


def test_failed_consume_leaves_no_h1_claim_and_never_restores_the_old_one(
    step1_root: _Step1Root,
    monkeypatch: pytest.MonkeyPatch,
    capsysbinary: pytest.CaptureFixture[bytes],
) -> None:
    _write_stale_h1_claim(step1_root)
    _write_legacy_base_context(step1_root)

    observed: list[list[str]] = []

    def failing_consume(*, expected_prepared_handoff_identity_sha256: str):
        observed.append(_h1_claiming_artifacts(step1_root))
        raise H1ReplacementHandoffError(
            Code.H1_HANDOFF_RESPONSE_INPUT_INVALID
        )

    monkeypatch.setattr(
        consume_cli, "consume_h1_replacement_handoff", failing_consume
    )

    assert consume_cli.main(_consume_argv()) == 3

    # The engine ran only after the claim was already gone, and the failure
    # path neither rereads the mapping report nor rebuilds the old facts.
    assert observed == [[]]
    assert _h1_claiming_artifacts(step1_root) == []
    decision = json.loads(step1_root.decision_path.read_text(encoding="utf-8"))
    assert decision["state"] != H1_STATE
    assert "NEW_BUY" not in decision["allowed_actions"]
    assert "ORDER_COMPILATION" not in decision["allowed_actions"]

    assert b"H1_HANDOFF_CONSUME_FAILED" in capsysbinary.readouterr().err


def test_new_prepare_clears_the_claim_first_and_never_refreshes_h1(
    step1_root: _Step1Root,
    monkeypatch: pytest.MonkeyPatch,
    capsysbinary: pytest.CaptureFixture[bytes],
) -> None:
    _write_stale_h1_claim(step1_root)
    _write_legacy_base_context(step1_root)

    observed: list[list[str]] = []
    supplied_facts: list[object] = []
    real_evaluate = step1_research._evaluate_research_availability_report_only

    def fake_prepare(**_kwargs):
        observed.append(_h1_claiming_artifacts(step1_root))
        return _FakePrepareResult()

    def recording_evaluate(*, h1_mapped_facts=None, **kwargs):
        supplied_facts.append(h1_mapped_facts)
        return real_evaluate(h1_mapped_facts=h1_mapped_facts, **kwargs)

    monkeypatch.setattr(
        prepare_cli, "prepare_h1_replacement_handoff", fake_prepare
    )
    monkeypatch.setattr(
        step1_research,
        "_evaluate_research_availability_report_only",
        recording_evaluate,
    )

    assert prepare_cli.main(_prepare_argv()) == 0

    assert observed == [[]]
    # Preparation evaluates availability exactly once, as the clear. It never
    # establishes H1 recognition — only a later successful consume can.
    assert supplied_facts == [None]
    assert _h1_claiming_artifacts(step1_root) == []

    stream = capsysbinary.readouterr()
    # The prompt-only stdout contract is unchanged.
    assert stream.out == PROMPT_TEXT.encode("utf-8")
    assert b"research_availability_state_after_clear=" in stream.err


def test_successful_raw_reparse_refutes_no_output_and_leaves_no_decision(
    step1_root: _Step1Root,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _write_stale_h1_claim(step1_root)
    raw_fixture = (
        Path(__file__).resolve().parents[1]
        / "fixtures"
        / "step1_contract_failures"
        / "current_research_output_minimal.json"
    )
    step1_research.step1_raw_output_path().write_text(
        raw_fixture.read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    warrants: list[step1_research._NoOutputWarrantResult] = []
    real_resolve = step1_research._resolve_no_output_warrant_for_h1_refresh

    def recording_resolve() -> step1_research._NoOutputWarrant:
        warrant = real_resolve()
        warrants.append(warrant.result)
        return warrant

    def unexpected_call(**_kwargs: object):
        raise AssertionError("availability evaluator/writer must not run")

    monkeypatch.setattr(
        step1_research,
        "_resolve_no_output_warrant_for_h1_refresh",
        recording_resolve,
    )
    monkeypatch.setattr(
        step1_research,
        "_write_no_output_research_availability_artifacts_report_only",
        unexpected_call,
    )
    monkeypatch.setattr(
        step1_research,
        "_evaluate_research_availability_report_only",
        unexpected_call,
    )

    summary = step1_research.refresh_research_availability_for_h1_replacement(
        h1_mapped_facts=_fresh_h1_facts(),
        strategy_settings={"as_of": "2026-06-30"},
    )

    assert warrants == [step1_research._NoOutputWarrantResult.NO_BASE_CONTEXT]
    assert summary["research_availability_state"] == ""
    assert summary["research_availability_decision_present"] == "False"
    assert summary["h1_mapped_selected"] == "False"
    for path in step1_root.state_paths:
        assert not path.exists(), path.name

    # The existing gate owner supplies the downstream fail-closed result.
    gate = load_and_evaluate_step2_research_gate(step1_root.decision_path)
    assert gate.allowed is False
    assert gate.state == MISSING_RESEARCH_PERMISSION
    assert gate.allowed_actions == ["HOLD", "NO_TRADE"]
    assert gate.new_buy_permission is False
    assert gate.order_compilation_allowed is False
    assert gate.step3_allowed is False
    assert gate.step4_allowed is False


def test_genuine_raw_absence_successful_consume_selects_h1_with_same_facts(
    step1_root: _Step1Root,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    facts = _fresh_h1_facts()
    warrants: list[step1_research._NoOutputWarrantResult] = []
    evaluated_facts: list[object] = []
    real_resolve = step1_research._resolve_no_output_warrant_for_h1_refresh
    real_evaluate = step1_research.evaluate_research_availability

    def recording_resolve() -> step1_research._NoOutputWarrant:
        warrant = real_resolve()
        warrants.append(warrant.result)
        return warrant

    def recording_evaluate(**kwargs: object):
        evaluated_facts.append(kwargs.get("h1_mapped_facts"))
        return real_evaluate(**kwargs)

    settings_path = step1_root.root / "inputs" / "current" / "strategy_settings.yaml"
    settings_path.parent.mkdir(parents=True)
    settings_path.write_text(
        'as_of: "2026-06-30"\ncore_universe: []\nsatellite_universe: []\n',
        encoding="utf-8",
    )
    monkeypatch.setattr(
        consume_cli,
        "consume_h1_replacement_handoff",
        lambda **_kwargs: _FakeConsumeResult(mapped_recognition_facts=facts),
    )
    monkeypatch.setattr(
        step1_research,
        "_resolve_no_output_warrant_for_h1_refresh",
        recording_resolve,
    )
    monkeypatch.setattr(
        step1_research,
        "evaluate_research_availability",
        recording_evaluate,
    )

    assert consume_cli.main(_consume_argv()) == 0

    assert warrants == [
        step1_research._NoOutputWarrantResult.OUTPUT_UNAVAILABLE,
        step1_research._NoOutputWarrantResult.OUTPUT_UNAVAILABLE,
    ]
    assert len(evaluated_facts) == 2
    assert evaluated_facts[0] is None
    assert evaluated_facts[1] is facts
    decision = json.loads(step1_root.decision_path.read_text(encoding="utf-8"))
    assert decision["state"] == H1_STATE
    assert decision["h1_mapped_selected"] is True


def test_schema_invalid_raw_is_f4_output_unavailable(
    step1_root: _Step1Root,
) -> None:
    step1_research.step1_raw_output_path().write_text("{}", encoding="utf-8")

    warrant = step1_research._resolve_no_output_warrant_for_h1_refresh()

    assert warrant.result is step1_research._NoOutputWarrantResult.OUTPUT_UNAVAILABLE
    assert warrant.detail.startswith(
        "Step 1 raw output failed research-output schema validation:"
    )


def test_generic_raw_oserror_propagates_outside_warrant_membership(
    step1_root: _Step1Root,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    failure = OSError(errno.EIO, "raw device failure")
    monkeypatch.setattr(
        step1_research,
        "read_text",
        lambda _path: (_ for _ in ()).throw(failure),
    )
    monkeypatch.setattr(
        step1_research,
        "_write_no_output_research_availability_artifacts_report_only",
        lambda **_kwargs: pytest.fail("NO_OUTPUT writer must not run"),
    )
    monkeypatch.setattr(
        step1_research,
        "_evaluate_research_availability_report_only",
        lambda **_kwargs: pytest.fail("parsed availability evaluator must not run"),
    )

    with pytest.raises(OSError, match="raw device failure") as caught:
        step1_research.refresh_research_availability_for_h1_replacement(
            strategy_settings={"as_of": "2026-06-30"},
        )

    assert caught.value is failure


def test_strict_no_output_refresh_reraises_exact_evaluator_failure(
    step1_root: _Step1Root,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    failure = RuntimeError("sentinel evaluator failure")
    monkeypatch.setattr(
        step1_research,
        "evaluate_research_availability",
        lambda **_kwargs: (_ for _ in ()).throw(failure),
    )

    with pytest.raises(RuntimeError, match="sentinel evaluator failure") as caught:
        step1_research.refresh_research_availability_for_h1_replacement(
            strategy_settings={"as_of": "2026-06-30"},
        )

    assert caught.value is failure


def test_strict_no_output_refresh_reraises_exact_persistence_failure(
    step1_root: _Step1Root,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    failure = OSError(errno.EIO, "sentinel availability write failure")
    write_paths: list[Path] = []

    def failing_write(path: Path, _payload: object):
        write_paths.append(path)
        raise failure

    monkeypatch.setattr(step1_research, "write_json", failing_write)

    with pytest.raises(OSError, match="sentinel availability write failure") as caught:
        step1_research.refresh_research_availability_for_h1_replacement(
            strategy_settings={"as_of": "2026-06-30"},
        )

    assert caught.value is failure
    assert write_paths == [step1_root.availability_path]


def test_a_failed_clear_aborts_before_either_engine_can_invalidate_anything(
    step1_root: _Step1Root,
    monkeypatch: pytest.MonkeyPatch,
    capsysbinary: pytest.CaptureFixture[bytes],
) -> None:
    _write_stale_h1_claim(step1_root)
    _write_legacy_base_context(step1_root)

    real_unlink = Path.unlink

    def refusing_unlink(self: Path, *args: object, **kwargs: object):
        if self.name == step1_root.decision_path.name:
            raise OSError(errno.EACCES, "permission denied")
        return real_unlink(self, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", refusing_unlink)

    entered: list[str] = []
    monkeypatch.setattr(
        prepare_cli,
        "prepare_h1_replacement_handoff",
        lambda **_kwargs: entered.append("prepare") or _FakePrepareResult(),
    )
    monkeypatch.setattr(
        consume_cli,
        "consume_h1_replacement_handoff",
        lambda **_kwargs: entered.append("consume")
        or _FakeConsumeResult(mapped_recognition_facts=object()),
    )

    assert prepare_cli.main(_prepare_argv()) == 4
    assert consume_cli.main(_consume_argv()) == 4

    # Neither engine ran, so the prior mapping completion was never
    # invalidated and the surviving claim still matches it.
    assert entered == []
    assert _h1_claiming_artifacts(step1_root) == [
        path.name for path in step1_root.state_paths
    ]

    err = capsysbinary.readouterr().err
    assert b"H1_AVAILABILITY_CLEAR_FAILED stage=prepare" in err
    assert b"H1_AVAILABILITY_CLEAR_FAILED stage=consume" in err
