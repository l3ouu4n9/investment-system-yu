from __future__ import annotations

from datetime import date, timedelta
import hashlib
import json
from pathlib import Path
from typing import Any

import pytest

import _mmi_hermetic_source_checkout as hermetic
from investment_orchestrator.common.io import write_json, write_text
from investment_orchestrator.mmi.contracts import (
    AUTHORITY_EFFECT_NONE,
    MmiProjectionResultCategory,
    MmiSourceCaptureResult,
    MmiSourceRole,
)
from investment_orchestrator.parsers import extract_template2_and_decision_packet as step2_parser
from investment_orchestrator.state import research_degraded_mode_gate as research_gate
from investment_orchestrator.state.research_degraded_mode_gate import (
    ResearchDegradedModeGateError,
)
from investment_orchestrator.state.upstream_artifact_guard import UpstreamArtifactGuardError
from investment_orchestrator.workflow import (
    step1_research,
    step2_decision_builder,
    step3_audit_engine,
)


SOURCE_ARTIFACT = "artifacts/current/step1_research/research_degraded_mode_decision.json"
BAD_RESEARCH_SENTINEL = "BAD_RESEARCH_SENTINEL_SHOULD_NOT_ENTER_PROMPT"
BLOCKED_PARSE_STATES = (
    ("STRICT_STALE", False),
    ("STRICT_FRESH_EVIDENCE_ONLY", False),
    ("STRICT_FRESH_GROUNDED_MEMO_NON_ACTIONABLE", False),
    ("STRICT_FRESH_COMPILED_ACTIONABLE_PENDING_GATES", False),
    ("STRICT_FRESH_COMPILED_ACTIONABLE_STEP3_AUDIT_ONLY", False),
    ("DEGRADED_WITH_LAST_GOOD", False),
    ("DEGRADED_NO_RESEARCH", False),
    ("INVALID_CONTRACT", False),
    ("NO_OUTPUT", False),
    ("MANUAL_REVIEW_REQUIRED", True),
    ("H1_MAPPED_FRESH_NON_ACTIONABLE", False),
)


def strict_fresh_permission() -> dict[str, Any]:
    return {
        "state": "STRICT_FRESH",
        "research_availability": "strict_fresh",
        "fresh_research_available": True,
        "handoff_valid": True,
        "handoff_stale": False,
        "settings_hash_match": True,
        "universe_match": True,
        "allowed_actions": [
            "HOLD",
            "NO_TRADE",
            "SELL",
            "NEW_BUY",
            "ROTATION",
            "REBALANCE",
            "EXTENDED_ETF_ADMISSION",
            "ORDER_COMPILATION",
        ],
        "blocked_actions": [],
        "manual_review_required": False,
        "blocker_reasons": [],
        "non_blocker_reasons": [],
        "report_only": True,
    }


def blocked_permission(state: str, *, manual_review_required: bool = False) -> dict[str, Any]:
    return {
        "state": state,
        "research_availability": state.lower(),
        "fresh_research_available": False,
        "handoff_valid": False,
        "handoff_stale": state == "STRICT_STALE",
        "settings_hash_match": None,
        "universe_match": None,
        "allowed_actions": ["HOLD", "NO_TRADE"],
        "blocked_actions": ["NEW_BUY", "ORDER_COMPILATION"],
        "manual_review_required": manual_review_required,
        "blocker_reasons": [f"{state} blocks order-generating Step 2."],
        "non_blocker_reasons": [],
        "report_only": True,
    }


# Independently declared here as literals, not imported from the production
# owner: this is the canonical H1 blocked complement an R1 render admission may
# recognize, and nothing else.
CANONICAL_H1_BLOCKED_ACTIONS = (
    "SELL",
    "NEW_BUY",
    "ROTATION",
    "REBALANCE",
    "EXTENDED_ETF_ADMISSION",
    "ORDER_COMPILATION",
)


def h1_permission(**overrides: Any) -> dict[str, Any]:
    permission = blocked_permission("H1_MAPPED_FRESH_NON_ACTIONABLE")
    permission["source"] = "H1_ROLE_MAPPED"
    permission["blocked_actions"] = list(CANONICAL_H1_BLOCKED_ACTIONS)
    permission.update(overrides)
    return permission


def _lh2_entry(
    *,
    published_at: date,
    suffix: str,
) -> dict[str, object]:
    return {
        "publisher": f"Publisher {suffix}",
        "published_at": published_at.isoformat(),
        "source_locator": f"operator/source-{suffix}.txt",
        "tickers": [f"ETF{suffix}"],
        "excerpt_text": f"Qualitative evidence excerpt {suffix}.",
    }


def _prepare_h1_lh2_inputs(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    published_dates: tuple[date, ...],
    raw_bytes: bytes | None = None,
    receipt_sha256: str | None = None,
    receipt_size_bytes: int | None = None,
    capture_status: MmiProjectionResultCategory = (
        MmiProjectionResultCategory.PROJECTION_VALID_COMPLETE
    ),
    capture_reason_codes: tuple[str, ...] = (),
    source_in_result: bool = True,
) -> tuple[bytes, list[tuple[MmiSourceRole, str]]]:
    raw = (
        raw_bytes
        if raw_bytes is not None
        else json.dumps(
            {
                "schema_version": "mmi_long_horizon_research_payload_v2",
                "sources": [
                    _lh2_entry(published_at=value, suffix=str(index))
                    for index, value in enumerate(published_dates)
                ],
            },
            ensure_ascii=False,
            indent=2,
        ).encode("utf-8")
    )
    source = hermetic.capture_source(
        tmp_path,
        role=MmiSourceRole.LONG_HORIZON_RESEARCH,
        raw=raw,
    )
    observed_sha256 = hashlib.sha256(raw).hexdigest()
    write_json(
        tmp_path / "inputs" / "current" / "lh2_manual_capture_receipt.json",
        {
            "schema_version": "lh2_manual_capture_receipt_v1",
            "source_role": "LONG_HORIZON_RESEARCH",
            "observed_sha256": receipt_sha256 or observed_sha256,
            "observed_size_bytes": (
                len(raw) if receipt_size_bytes is None else receipt_size_bytes
            ),
        },
    )
    calls: list[tuple[MmiSourceRole, str]] = []

    def capture(
        role: MmiSourceRole,
        *,
        expected_source_sha256: str,
    ) -> MmiSourceCaptureResult:
        calls.append((role, expected_source_sha256))
        return MmiSourceCaptureResult(
            status=capture_status,
            authority_effect=AUTHORITY_EFFECT_NONE,
            reason_codes=capture_reason_codes,
            source=source if source_in_result else None,
        )

    monkeypatch.setattr(
        step2_decision_builder,
        "capture_current_mmi_source",
        capture,
    )
    return raw, calls


def prepare_tmp_repo(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    permission: dict[str, Any] | None = None,
    permission_text: str | None = None,
) -> None:
    monkeypatch.setattr(step2_decision_builder, "repo_root", lambda: tmp_path)
    monkeypatch.setattr(step1_research, "repo_root", lambda: tmp_path)

    write_text(
        tmp_path / "prompts" / "strategy_a_decision_builder.txt",
        "RESEARCH\n{{ research_json }}\nPORTFOLIO\n{{ portfolio_snapshot }}\nSETTINGS\n{{ strategy_settings }}\n",
    )
    write_text(tmp_path / "inputs" / "current" / "strategy_settings.yaml", "as_of: '2026-06-22'\n")
    write_text(tmp_path / "inputs" / "current" / "portfolio_snapshot.txt", "QQQ | 1 | 100\n")
    write_json(
        step1_research.step1_research_output_path(),
        {
            "schema_version": "1.0",
            "as_of": "2026-06-22",
            "sentinel": BAD_RESEARCH_SENTINEL,
        },
    )
    if permission is not None:
        write_json(step1_research.step1_research_degraded_mode_decision_path(), permission)
    if permission_text is not None:
        write_text(step1_research.step1_research_degraded_mode_decision_path(), permission_text)


def read_blocked_artifact() -> dict[str, Any]:
    payload = json.loads(
        step2_decision_builder.step2_blocked_by_research_gate_path().read_text(encoding="utf-8")
    )
    assert isinstance(payload, dict)
    return payload


def assert_render_blocked() -> dict[str, Any]:
    with pytest.raises(ResearchDegradedModeGateError, match="Step 2 blocked"):
        step2_decision_builder.render_step2_prompt()
    assert not step2_decision_builder.step2_prompt_path().exists()
    assert not step2_decision_builder.step2_raw_output_path().exists()
    return read_blocked_artifact()


def test_strict_fresh_permission_allows_existing_step2_render_path(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    prepare_tmp_repo(tmp_path, monkeypatch, permission=strict_fresh_permission())

    result = step2_decision_builder.render_step2_prompt()

    assert result["prompt_path"] == str(step2_decision_builder.step2_prompt_path())
    assert step2_decision_builder.step2_prompt_path().exists()
    assert step2_decision_builder.step2_raw_output_path().read_text(encoding="utf-8") == ""
    prompt = step2_decision_builder.step2_prompt_path().read_text(encoding="utf-8")
    assert BAD_RESEARCH_SENTINEL in prompt
    assert not step2_decision_builder.step2_blocked_by_research_gate_path().exists()


@pytest.mark.parametrize(
    ("state", "manual_review_required"),
    [
        ("NO_OUTPUT", False),
        ("INVALID_CONTRACT", False),
        ("DEGRADED_WITH_LAST_GOOD", False),
        ("STRICT_STALE", False),
        # R2E.1: the compiled evidence-first state is non-actionable and must be
        # blocked by the Step 2 gate exactly like the degraded states.
        ("STRICT_FRESH_EVIDENCE_ONLY", False),
        # R2E.4: the grounded-memo state is likewise non-actionable and must block.
        ("STRICT_FRESH_GROUNDED_MEMO_NON_ACTIONABLE", False),
        # R2E.5b-5b: promoted handoff recognized, but gates remain closed.
        ("STRICT_FRESH_COMPILED_ACTIONABLE_PENDING_GATES", False),
        ("MANUAL_REVIEW_REQUIRED", True),
    ],
)
def test_non_strict_fresh_permissions_block_before_step2_prompt_render(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    state: str,
    manual_review_required: bool,
) -> None:
    prepare_tmp_repo(
        tmp_path,
        monkeypatch,
        permission=blocked_permission(
            state,
            manual_review_required=manual_review_required,
        ),
    )

    blocked = assert_render_blocked()

    assert blocked["blocked"] is True
    assert blocked["reason"] == "research_degraded_mode_gate"
    assert blocked["state"] == state
    assert blocked["allowed_actions"] == ["HOLD", "NO_TRADE"]
    assert "NEW_BUY" in blocked["blocked_actions"]
    assert "ORDER_COMPILATION" in blocked["blocked_actions"]
    assert blocked["manual_review_required"] is manual_review_required
    assert blocked["source_artifact"] == SOURCE_ARTIFACT
    assert blocked["recommended_result"] == "NO_TRADE"
    assert blocked["report_only"] is False
    assert blocked["blocker_reasons"]


def test_missing_permission_artifact_fails_closed_and_writes_blocked_artifact(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    prepare_tmp_repo(tmp_path, monkeypatch, permission=None)

    blocked = assert_render_blocked()

    assert blocked["state"] == "MISSING_RESEARCH_PERMISSION"
    assert blocked["allowed_actions"] == ["HOLD", "NO_TRADE"]
    assert blocked["blocked_actions"] == ["NEW_BUY", "ORDER_COMPILATION"]
    assert blocked["recommended_result"] == "NO_TRADE"
    assert blocked["source_artifact"] == SOURCE_ARTIFACT


def test_strict_fresh_without_order_compilation_permission_still_blocks(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    permission = strict_fresh_permission()
    permission["allowed_actions"] = ["HOLD", "NO_TRADE", "NEW_BUY"]

    prepare_tmp_repo(tmp_path, monkeypatch, permission=permission)

    blocked = assert_render_blocked()

    assert blocked["state"] == "STRICT_FRESH"
    assert "ORDER_COMPILATION" in blocked["blocked_actions"]
    assert any("ORDER_COMPILATION" in reason for reason in blocked["blocker_reasons"])


def test_malformed_permission_artifact_fails_closed_and_writes_blocked_artifact(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    prepare_tmp_repo(tmp_path, monkeypatch, permission_text="{not valid json")

    blocked = assert_render_blocked()

    assert blocked["state"] == "MALFORMED_RESEARCH_PERMISSION"
    assert blocked["allowed_actions"] == ["HOLD", "NO_TRADE"]
    assert blocked["blocked_actions"] == ["NEW_BUY", "ORDER_COMPILATION"]
    assert blocked["recommended_result"] == "NO_TRADE"
    assert blocked["blocker_reasons"]


def test_blocked_path_does_not_read_bad_research_into_step2_prompt(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    prepare_tmp_repo(tmp_path, monkeypatch, permission=blocked_permission("NO_OUTPUT"))

    blocked = assert_render_blocked()

    assert blocked["state"] == "NO_OUTPUT"
    assert not step2_decision_builder.step2_prompt_path().exists()


# --- H1 + LH2 invocation-local Step 2 render-only admission -----------------


def test_h1_lh2_render_uses_one_gate_snapshot_and_exact_admitted_payload(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    now_date = date(2026, 8, 14)
    prepare_tmp_repo(tmp_path, monkeypatch, permission=h1_permission())
    raw, capture_calls = _prepare_h1_lh2_inputs(
        tmp_path,
        monkeypatch,
        published_dates=(now_date, now_date - timedelta(days=180)),
    )
    monkeypatch.setattr(
        step2_decision_builder,
        "_system_now_date",
        lambda: now_date,
    )

    gate_reads: list[Path] = []
    real_gate_read_json = research_gate.read_json
    gate_path = step1_research.step1_research_degraded_mode_decision_path()

    def counted_gate_read_json(path: Path) -> Any:
        if Path(path) == gate_path:
            gate_reads.append(Path(path))
        return real_gate_read_json(path)

    monkeypatch.setattr(research_gate, "read_json", counted_gate_read_json)

    receipt_reads: list[Path] = []
    real_workflow_read_json = step2_decision_builder.read_json
    receipt_path = tmp_path / "inputs" / "current" / "lh2_manual_capture_receipt.json"

    def counted_workflow_read_json(path: Path) -> Any:
        if Path(path) == receipt_path:
            receipt_reads.append(Path(path))
        return real_workflow_read_json(path)

    monkeypatch.setattr(
        step2_decision_builder,
        "read_json",
        counted_workflow_read_json,
    )

    result = step2_decision_builder.render_step2_prompt()

    expected_sha256 = hashlib.sha256(raw).hexdigest()
    assert gate_reads == [gate_path]
    assert receipt_reads == [receipt_path]
    assert capture_calls == [
        (MmiSourceRole.LONG_HORIZON_RESEARCH, expected_sha256)
    ]
    assert result["mode"] == "h1_lh2_render_only"
    assert result["render_only"] == "True"
    assert result["step2_parse_allowed"] == "False"
    assert result["order_compilation_allowed"] == "False"
    assert result["new_buy_permission"] == "False"

    prompt = step2_decision_builder.step2_prompt_path().read_text(encoding="utf-8")
    for expected in (
        "QUALITATIVE, NON-ACTIONABLE, REPORT-ONLY",
        "repository deterministically parses this report but grants no actionable authority",
        "Publisher 0",
        "Publisher 1",
        now_date.isoformat(),
        (now_date - timedelta(days=180)).isoformat(),
        "operator/source-0.txt",
        "operator/source-1.txt",
        "ETF0",
        "ETF1",
        "Qualitative evidence excerpt 0.",
        "Qualitative evidence excerpt 1.",
    ):
        assert expected in prompt
    assert BAD_RESEARCH_SENTINEL not in prompt
    assert "source_entry_identity_sha256" not in prompt
    assert step2_decision_builder.step2_raw_output_path().read_bytes() == b""
    assert Path(result["raw_output_metadata_path"]).is_file()
    assert not step2_decision_builder.step2_blocked_by_research_gate_path().exists()
    assert not step2_decision_builder.step2_promoted_decision_only_path().exists()
    assert not step2_decision_builder.step2_template2_output_path().exists()
    assert not step2_decision_builder.step2_decision_packet_path().exists()


@pytest.mark.parametrize(
    "overrides",
    (
        {"allowed_actions": ["HOLD", "NO_TRADE", "SELL"]},
        {"manual_review_required": True},
        {"blocked_actions": ["HOLD", "NEW_BUY", "ORDER_COMPILATION"]},
        {"source": "raw_research_handoff"},
    ),
)
def test_noncanonical_h1_prerequisite_uses_existing_generic_denial(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    overrides: dict[str, object],
) -> None:
    prepare_tmp_repo(
        tmp_path,
        monkeypatch,
        permission=h1_permission(**overrides),
    )
    monkeypatch.setattr(
        step2_decision_builder,
        "capture_current_mmi_source",
        lambda *_args, **_kwargs: pytest.fail(
            "noncanonical H1 reached LH2 reconstruction"
        ),
    )

    blocked = assert_render_blocked()

    assert blocked["reason"] == "research_degraded_mode_gate"
    assert blocked["state"] == "H1_MAPPED_FRESH_NON_ACTIONABLE"


@pytest.mark.parametrize(
    ("raw_blocked_actions", "qualifies"),
    (
        (list(CANONICAL_H1_BLOCKED_ACTIONS), True),
        # Ordering is incidental at this seam: the canonical contract owns
        # membership, so a reordered canonical row must still qualify.
        (list(reversed(CANONICAL_H1_BLOCKED_ACTIONS)), True),
        # Real incomplete membership: the sleeve actions are simply absent.
        (["SELL", "NEW_BUY", "ORDER_COMPILATION"], False),
        # The gate evaluator normalizes an empty row into exactly NEW_BUY /
        # ORDER_COMPILATION, leaving a snapshot that is disjoint from
        # HOLD / NO_TRADE and canonical in every other field. Disjointness
        # alone would admit it; exact complement membership must not.
        ([], False),
        # A malformed (non string-array) row is unreachable as a malformed
        # evaluated value -- it reaches this seam as the same normalized pair.
        # The reachable normalization is what must fail closed.
        ("NEW_BUY", False),
        ([*CANONICAL_H1_BLOCKED_ACTIONS, "PROMOTED_RESEARCH_DECISION"], False),
        ([*CANONICAL_H1_BLOCKED_ACTIONS, "SELL"], False),
    ),
    ids=(
        "canonical",
        "canonical_reordered",
        "incomplete_membership",
        "normalized_from_empty",
        "malformed_shape_normalized",
        "extra_action",
        "duplicate_action",
    ),
)
def test_r1_render_admission_requires_exact_canonical_h1_blocked_actions(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    raw_blocked_actions: Any,
    qualifies: bool,
) -> None:
    """Only the exact canonical H1 blocked complement may reach LH2 access.

    Every other R1 prerequisite is canonical in every case, and the LH2 receipt
    and current source are present and admissible throughout. A noncanonical
    blocked row that is still denied therefore proves the denial came from the
    persisted H1 prerequisite itself, before any LH2 access -- not from the LH2
    chain failing afterwards.
    """
    now_date = date(2026, 8, 14)
    prepare_tmp_repo(
        tmp_path,
        monkeypatch,
        permission=h1_permission(blocked_actions=raw_blocked_actions),
    )
    _raw, capture_calls = _prepare_h1_lh2_inputs(
        tmp_path,
        monkeypatch,
        published_dates=(now_date,),
    )
    monkeypatch.setattr(
        step2_decision_builder,
        "_system_now_date",
        lambda: now_date,
    )

    receipt_path = tmp_path / "inputs" / "current" / "lh2_manual_capture_receipt.json"
    receipt_reads: list[Path] = []
    real_workflow_read_json = step2_decision_builder.read_json

    def counted_workflow_read_json(path: Path) -> Any:
        if Path(path) == receipt_path:
            receipt_reads.append(Path(path))
        return real_workflow_read_json(path)

    monkeypatch.setattr(
        step2_decision_builder,
        "read_json",
        counted_workflow_read_json,
    )

    if qualifies:
        result = step2_decision_builder.render_step2_prompt()

        assert result["mode"] == "h1_lh2_render_only"
        assert receipt_reads == [receipt_path]
        assert len(capture_calls) == 1
        assert not (
            step2_decision_builder.step2_blocked_by_research_gate_path().exists()
        )
        return

    blocked = assert_render_blocked()

    # The ordinary generic gate denial, NOT the H1/LH2 invocation-admission
    # block -- reaching the latter would itself prove LH2 access occurred.
    assert blocked["reason"] == "research_degraded_mode_gate"
    assert blocked["state"] == "H1_MAPPED_FRESH_NON_ACTIONABLE"
    assert blocked["mode"] == "blocked"
    assert blocked["order_compilation_allowed"] is False
    assert blocked["new_buy_permission"] is False
    # Fail closed BEFORE any LH2 access: neither the receipt nor the current
    # source was read, although both are present and would have been admitted.
    assert receipt_reads == []
    assert capture_calls == []


@pytest.mark.parametrize(
    ("receipt_case", "expected_code"),
    (
        ("missing", "H1_LH2_RECEIPT_MISSING"),
        ("malformed", "H1_LH2_RECEIPT_JSON_INVALID"),
        ("invalid", "LH2_MANUAL_CAPTURE_RECEIPT_V1_INVALID"),
    ),
)
def test_h1_receipt_operator_input_failures_use_single_invocation_block(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    receipt_case: str,
    expected_code: str,
) -> None:
    prepare_tmp_repo(tmp_path, monkeypatch, permission=h1_permission())
    receipt_path = tmp_path / "inputs" / "current" / "lh2_manual_capture_receipt.json"
    if receipt_case == "malformed":
        write_text(receipt_path, "{not-json")
    elif receipt_case == "invalid":
        write_json(
            receipt_path,
            {
                "schema_version": "lh2_manual_capture_receipt_v1",
                "source_role": "STRATEGY_SETTINGS",
                "observed_sha256": "a" * 64,
                "observed_size_bytes": 10,
            },
        )
    monkeypatch.setattr(
        step2_decision_builder,
        "capture_current_mmi_source",
        lambda *_args, **_kwargs: pytest.fail(
            "invalid receipt reached source capture"
        ),
    )

    blocked = assert_render_blocked()

    assert blocked["reason"] == "h1_lh2_invocation_admission_failed"
    assert blocked["state"] == "H1_MAPPED_FRESH_NON_ACTIONABLE"
    assert blocked["mode"] == "blocked"
    assert blocked["blocker_reasons"] == [expected_code]
    assert blocked["order_compilation_allowed"] is False
    assert blocked["new_buy_permission"] is False


def test_h1_receipt_native_io_failure_is_not_flattened_into_operator_input(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    prepare_tmp_repo(tmp_path, monkeypatch, permission=h1_permission())
    receipt_path = tmp_path / "inputs" / "current" / "lh2_manual_capture_receipt.json"
    real_read_json = step2_decision_builder.read_json

    def fail_receipt_io(path: Path) -> Any:
        if Path(path) == receipt_path:
            raise PermissionError("receipt denied by test")
        return real_read_json(path)

    monkeypatch.setattr(step2_decision_builder, "read_json", fail_receipt_io)

    with pytest.raises(PermissionError, match="receipt denied by test"):
        step2_decision_builder.render_step2_prompt()

    assert not step2_decision_builder.step2_blocked_by_research_gate_path().exists()
    assert not step2_decision_builder.step2_prompt_path().exists()


@pytest.mark.parametrize(
    ("case", "expected_code"),
    (
        ("sha", "MMI_SOURCE_EXPECTED_SHA256_MISMATCH"),
        ("size", "H1_LH2_RECEIPT_SIZE_MISMATCH"),
    ),
)
def test_h1_receipt_current_source_continuity_fails_closed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    case: str,
    expected_code: str,
) -> None:
    now_date = date(2026, 8, 14)
    prepare_tmp_repo(tmp_path, monkeypatch, permission=h1_permission())
    raw, capture_calls = _prepare_h1_lh2_inputs(
        tmp_path,
        monkeypatch,
        published_dates=(now_date,),
        receipt_sha256="0" * 64 if case == "sha" else None,
        receipt_size_bytes=0 if case == "size" else None,
        capture_status=(
            MmiProjectionResultCategory.PROJECTION_BLOCKED
            if case == "sha"
            else MmiProjectionResultCategory.PROJECTION_VALID_COMPLETE
        ),
        capture_reason_codes=(
            ("MMI_SOURCE_EXPECTED_SHA256_MISMATCH",)
            if case == "sha"
            else ()
        ),
        source_in_result=case != "sha",
    )
    monkeypatch.setattr(
        step2_decision_builder,
        "_system_now_date",
        lambda: now_date,
    )

    blocked = assert_render_blocked()

    expected_sha = "0" * 64 if case == "sha" else hashlib.sha256(raw).hexdigest()
    assert capture_calls == [
        (MmiSourceRole.LONG_HORIZON_RESEARCH, expected_sha)
    ]
    assert blocked["reason"] == "h1_lh2_invocation_admission_failed"
    assert blocked["blocker_reasons"] == [expected_code]


def test_h1_source_capture_requires_exact_complete_category(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    now_date = date(2026, 8, 14)
    prepare_tmp_repo(tmp_path, monkeypatch, permission=h1_permission())
    _prepare_h1_lh2_inputs(
        tmp_path,
        monkeypatch,
        published_dates=(now_date,),
        capture_status=MmiProjectionResultCategory.PROJECTION_VALID_WITH_GAPS,
    )

    blocked = assert_render_blocked()

    assert blocked["reason"] == "h1_lh2_invocation_admission_failed"
    assert blocked["blocker_reasons"] == ["H1_LH2_SOURCE_CAPTURE_INCOMPLETE"]


def test_h1_authenticated_source_v2_failure_uses_invocation_block(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    prepare_tmp_repo(tmp_path, monkeypatch, permission=h1_permission())
    _prepare_h1_lh2_inputs(
        tmp_path,
        monkeypatch,
        published_dates=(),
        raw_bytes=b'{"schema_version":"wrong","sources":[]}',
    )

    blocked = assert_render_blocked()

    assert blocked["reason"] == "h1_lh2_invocation_admission_failed"
    assert blocked["blocker_reasons"] == [
        "MMI_LONG_HORIZON_RESEARCH_PAYLOAD_V2_SCHEMA_VERSION_INVALID"
    ]


@pytest.mark.parametrize(
    ("ages", "expected_prefixes"),
    (
        ((181,), ("H1_LH2_SOURCE_STALE:",)),
        ((-1,), ("H1_LH2_SOURCE_FUTURE_DATED:",)),
        (
            (0, 181, -1),
            ("H1_LH2_SOURCE_STALE:", "H1_LH2_SOURCE_FUTURE_DATED:"),
        ),
    ),
)
def test_h1_lh2_temporal_denial_evaluates_whole_payload(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    ages: tuple[int, ...],
    expected_prefixes: tuple[str, ...],
) -> None:
    now_date = date(2026, 8, 14)
    prepare_tmp_repo(tmp_path, monkeypatch, permission=h1_permission())
    _prepare_h1_lh2_inputs(
        tmp_path,
        monkeypatch,
        published_dates=tuple(now_date - timedelta(days=age) for age in ages),
    )
    monkeypatch.setattr(
        step2_decision_builder,
        "_system_now_date",
        lambda: now_date,
    )

    blocked = assert_render_blocked()

    reasons = blocked["blocker_reasons"]
    assert blocked["reason"] == "h1_lh2_invocation_admission_failed"
    assert len(reasons) == len(expected_prefixes)
    for prefix in expected_prefixes:
        assert sum(reason.startswith(prefix) for reason in reasons) == 1


# --- R2E.5b-6c promoted Step 2 decision-only path -----------------------------


def promoted_decision_only_permission(**overrides: Any) -> dict[str, Any]:
    permission = {
        "state": "STRICT_FRESH_COMPILED_ACTIONABLE_STEP2_DECISION_ONLY",
        "research_availability": "strict_fresh_compiled_actionable_step2_decision_only",
        "fresh_research_available": False,
        "handoff_valid": False,
        "handoff_stale": False,
        "settings_hash_match": None,
        "universe_match": None,
        "allowed_actions": ["HOLD", "NO_TRADE", "PROMOTED_RESEARCH_DECISION"],
        "blocked_actions": [
            "SELL",
            "NEW_BUY",
            "ROTATION",
            "REBALANCE",
            "EXTENDED_ETF_ADMISSION",
            "ORDER_COMPILATION",
        ],
        "manual_review_required": False,
        "blocker_reasons": [
            "promoted_step2_decision_only_enabled",
            "new_buy_requires_future_gate_pr",
            "order_compilation_requires_future_gate_pr",
            "final_execution_requires_future_gate_pr",
        ],
        "non_blocker_reasons": [],
        "source": "promoted_compiled_actionable_handoff",
        "promoted_step2_decision_only": True,
        "order_compilation_allowed": False,
        "new_buy_permission": False,
        "permission_effect": "promoted_step2_decision_only",
        "not_authorization": True,
        "report_only": True,
    }
    permission.update(overrides)
    return permission


def test_promoted_decision_only_gate_allows_decision_only_mode() -> None:
    from investment_orchestrator.state.research_degraded_mode_gate import (
        evaluate_step2_research_gate,
    )

    gate = evaluate_step2_research_gate(promoted_decision_only_permission())

    assert gate.allowed is True
    assert gate.mode == "promoted_step2_decision_only"
    assert gate.order_compilation_allowed is False
    assert gate.new_buy_permission is False
    assert gate.step3_allowed is False
    assert gate.step4_allowed is False
    assert gate.recommended_terminal_result_after_step2 == "NO_TRADE_PENDING_FINAL_GATES"
    # The order actions stay explicitly blocked.
    assert "NEW_BUY" in gate.blocked_actions
    assert "ORDER_COMPILATION" in gate.blocked_actions


def test_promoted_decision_only_gate_blocks_without_promoted_action() -> None:
    from investment_orchestrator.state.research_degraded_mode_gate import (
        evaluate_step2_research_gate,
    )

    gate = evaluate_step2_research_gate(
        promoted_decision_only_permission(allowed_actions=["HOLD", "NO_TRADE"])
    )
    assert gate.allowed is False
    assert gate.mode == "blocked"
    assert any("PROMOTED_RESEARCH_DECISION" in reason for reason in gate.blocker_reasons)


def test_promoted_decision_only_gate_blocks_widened_order_actions() -> None:
    from investment_orchestrator.state.research_degraded_mode_gate import (
        evaluate_step2_research_gate,
    )

    for widened in (
        ["HOLD", "NO_TRADE", "PROMOTED_RESEARCH_DECISION", "NEW_BUY"],
        ["HOLD", "NO_TRADE", "PROMOTED_RESEARCH_DECISION", "ORDER_COMPILATION"],
    ):
        gate = evaluate_step2_research_gate(
            promoted_decision_only_permission(allowed_actions=widened)
        )
        assert gate.allowed is False, widened
        assert gate.mode == "blocked"
        assert any("must not allow NEW_BUY / ORDER_COMPILATION" in r for r in gate.blocker_reasons)


def test_promoted_decision_only_gate_blocks_on_marker_or_source_mismatch() -> None:
    from investment_orchestrator.state.research_degraded_mode_gate import (
        evaluate_step2_research_gate,
    )

    for overrides in (
        {"source": "raw_research_handoff"},
        {"promoted_step2_decision_only": False},
        {"manual_review_required": True},
    ):
        gate = evaluate_step2_research_gate(promoted_decision_only_permission(**overrides))
        assert gate.allowed is False, overrides
        assert gate.mode == "blocked"


def test_legacy_strict_fresh_gate_fields_report_full_permissions() -> None:
    from investment_orchestrator.state.research_degraded_mode_gate import (
        evaluate_step2_research_gate,
    )

    gate = evaluate_step2_research_gate(strict_fresh_permission())

    assert gate.allowed is True
    assert gate.mode == "strict_fresh_actionable"
    assert gate.order_compilation_allowed is True
    assert gate.new_buy_permission is True
    assert gate.step3_allowed is True
    assert gate.step4_allowed is True
    assert gate.recommended_terminal_result_after_step2 is None


def test_promoted_decision_only_render_fails_closed_without_pointer_artifacts(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The gate allows decision-only, but the promoted render re-verifies the
    # pointer / effective handoff / validation on disk; with none present the
    # render must fail closed and write the blocked artifact.
    prepare_tmp_repo(tmp_path, monkeypatch, permission=promoted_decision_only_permission())

    with pytest.raises(ResearchDegradedModeGateError, match="promoted decision-only verification"):
        step2_decision_builder.render_step2_prompt()

    assert not step2_decision_builder.step2_prompt_path().exists()
    blocked = read_blocked_artifact()
    assert blocked["reason"] == "promoted_step2_verification_failed"
    assert blocked["state"] == "STRICT_FRESH_COMPILED_ACTIONABLE_STEP2_DECISION_ONLY"
    assert blocked["order_compilation_allowed"] is False
    assert "pointer_missing" in blocked["blocker_reasons"]
    assert blocked["recommended_result"] == "NO_TRADE"


# --- Step 2 parse admission must precede extraction/persistence ----------------


def _seed_prior_step2_outputs() -> dict[Path, bytes]:
    """Seed existing packets and retain their exact bytes for denial assertions."""
    template_path = step2_decision_builder.step2_template2_output_path()
    packet_path = step2_decision_builder.step2_decision_packet_path()
    template_path.write_bytes(b"PRIOR TEMPLATE2 OUTPUT\n")
    packet_path.write_bytes(b'{"prior":"decision_packet"}\n')
    return {
        template_path: template_path.read_bytes(),
        packet_path: packet_path.read_bytes(),
    }


def _assert_prior_step2_outputs_unchanged(prior: dict[Path, bytes]) -> None:
    assert {path: path.read_bytes() for path in prior} == prior


def _valid_step2_raw_output() -> str:
    packet = {
        "effective_allowed_buy_universe": ["QQQ"],
        "MARKET_DATA_SNAPSHOT": {
            "schema_version": "1.0",
            "snapshot_type": "MARKET_DATA_SNAPSHOT",
            "run_timestamp_et": "2026-06-22 16:00 ET",
            "execution_date_et": "2026-06-22",
            "market_data_target_close_date_et": "2026-06-22",
            "close_time_zone": "America/New_York",
            "display_time_zone": "America/Los_Angeles",
            "primary_source": "fixture",
            "fallback_source_for_last_close_and_price_asof_only": "fixture",
            "holiday_aware_close_resolution": True,
            "tickers": [
                {
                    "ticker": "QQQ",
                    "last_close": 420.0,
                    "price_asof": "2026-06-22",
                    "atr_20_30d_pct": 2.0,
                    "ma50": 410.0,
                    "ma200": 390.0,
                    "avg_volume_3m": 50000000,
                    "last_close_source": "fixture",
                    "price_asof_source": "fixture",
                    "technicals_source": "fixture",
                    "retrieved_at_utc": None,
                    "same_day_close_required": False,
                    "freshness_ok": True,
                    "data_gap": False,
                    "data_gap_reason": None,
                    "notes": [],
                }
            ],
        },
        "active_shortlist": [],
        "buy_side_delta_table": [],
        "rotation_decision_layer_8_15": [],
        "sell_side_delta_table_8_2": [],
        "execution_plan_drafts_8_5": [
            {"ticker": "QQQ", "action_draft": "KEEP_EXISTING", "why": "fixture"}
        ],
        "sell_execution_plan_drafts_8_6": [],
        "assumptions_and_data_gaps": [],
        "decision_builder_ready_for_audit": True,
    }
    return (
        "TEMPLATE2_OUTPUT_START\n"
        "STRICT FRESH TEMPLATE2\n"
        "TEMPLATE2_OUTPUT_END\n"
        "DECISION_PACKET_START\n"
        + json.dumps(packet)
        + "\nDECISION_PACKET_END\n"
    )


@pytest.mark.parametrize(("state", "manual_review_required"), BLOCKED_PARSE_STATES)
def test_blocked_states_fail_before_step2_parse_and_preserve_prior_outputs(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    state: str,
    manual_review_required: bool,
) -> None:
    """The independent blocked-state matrix must never reach the extractor."""
    prepare_tmp_repo(
        tmp_path,
        monkeypatch,
        permission=(
            h1_permission()
            if state == "H1_MAPPED_FRESH_NON_ACTIONABLE"
            else blocked_permission(
                state,
                manual_review_required=manual_review_required,
            )
        ),
    )
    prior = _seed_prior_step2_outputs()
    calls: list[object] = []

    def forbidden_extractor(*_args: object, **_kwargs: object) -> None:
        calls.append("extractor")
        pytest.fail("blocked Step 2 parse invoked the extractor")

    monkeypatch.setattr(step2_decision_builder, "extract_template2_and_decision_packet", forbidden_extractor)

    with pytest.raises(ResearchDegradedModeGateError, match="Step 2 blocked"):
        step2_decision_builder.parse_step2_output()

    assert calls == []
    blocked = read_blocked_artifact()
    assert blocked["reason"] == "research_degraded_mode_gate"
    assert blocked["state"] == state
    _assert_prior_step2_outputs_unchanged(prior)


@pytest.mark.parametrize(
    ("case", "expected_state"),
    (
        ("missing", "MISSING_RESEARCH_PERMISSION"),
        ("malformed", "MALFORMED_RESEARCH_PERMISSION"),
        ("unknown", "UNKNOWN_RESEARCH_STATE"),
    ),
)
def test_invalid_or_unknown_permission_fails_before_step2_parse(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    case: str,
    expected_state: str,
) -> None:
    if case == "missing":
        prepare_tmp_repo(tmp_path, monkeypatch)
    elif case == "malformed":
        prepare_tmp_repo(tmp_path, monkeypatch, permission_text="{not valid json")
    else:
        prepare_tmp_repo(
            tmp_path,
            monkeypatch,
            permission=blocked_permission("UNKNOWN_RESEARCH_STATE"),
        )

    prior = _seed_prior_step2_outputs()
    monkeypatch.setattr(
        step2_decision_builder,
        "extract_template2_and_decision_packet",
        lambda **_kwargs: pytest.fail("invalid permission reached the extractor"),
    )

    with pytest.raises(ResearchDegradedModeGateError, match="Step 2 blocked"):
        step2_decision_builder.parse_step2_output()

    assert read_blocked_artifact()["state"] == expected_state
    _assert_prior_step2_outputs_unchanged(prior)


def test_strict_fresh_parse_admits_before_single_extraction_and_persists_outputs(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    prepare_tmp_repo(tmp_path, monkeypatch, permission=strict_fresh_permission())
    write_text(step2_decision_builder.step2_raw_output_path(), _valid_step2_raw_output())
    calls: list[str] = []

    def counted_extractor(**kwargs: Any) -> tuple[str, dict[str, Any]]:
        calls.append("extractor")
        return step2_parser.extract_template2_and_decision_packet(**kwargs)

    monkeypatch.setattr(step2_decision_builder, "extract_template2_and_decision_packet", counted_extractor)

    result = step2_decision_builder.parse_step2_output()

    assert calls == ["extractor"]
    assert result == {
        "template2_output_path": str(step2_decision_builder.step2_template2_output_path()),
        "decision_packet_path": str(step2_decision_builder.step2_decision_packet_path()),
        "template2_output_chars": str(len("STRICT FRESH TEMPLATE2")),
        "market_snapshot_type": "MARKET_DATA_SNAPSHOT",
    }
    assert step2_decision_builder.step2_template2_output_path().read_text(encoding="utf-8") == "STRICT FRESH TEMPLATE2\n"
    assert step2_decision_builder.step2_decision_packet_path().is_file()
    assert not step2_decision_builder.step2_blocked_by_research_gate_path().exists()


def test_promoted_decision_only_parse_verifies_before_single_extraction(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    prepare_tmp_repo(tmp_path, monkeypatch, permission=promoted_decision_only_permission())
    calls: list[str] = []
    promoted_context = {
        "promotion_status": "ACTIVE",
        "active_pointer_sha256": "pointer-sha",
        "effective_handoff_sha256": "handoff-sha",
        "promotion_expires_at": "2026-06-30T00:00:00Z",
        "actionable_this_run_tickers": ["QQQ"],
    }

    def fake_promoted_context(_gate: object) -> dict[str, Any]:
        calls.append("promoted_context")
        return promoted_context

    def fake_extractor(**kwargs: Any) -> tuple[str, dict[str, Any]]:
        assert calls == ["promoted_context"]
        calls.append("extractor")
        write_text(Path(kwargs["template2_output_path"]), "PROMOTED TEMPLATE2\n")
        packet = {"MARKET_DATA_SNAPSHOT": {"snapshot_type": "fixture"}}
        write_json(Path(kwargs["decision_packet_path"]), packet)
        return "PROMOTED TEMPLATE2\n", packet

    def fake_refresh() -> dict[str, str]:
        assert calls == ["promoted_context", "extractor"]
        calls.append("refresh")
        return {"promoted_step3_audit_only": "False"}

    monkeypatch.setattr(step2_decision_builder, "_load_promoted_step2_context_or_block", fake_promoted_context)
    monkeypatch.setattr(step2_decision_builder, "extract_template2_and_decision_packet", fake_extractor)
    monkeypatch.setattr(
        step2_decision_builder,
        "refresh_promoted_step3_audit_only_permission_after_step2",
        fake_refresh,
    )

    result = step2_decision_builder.parse_step2_output()

    assert calls == ["promoted_context", "extractor", "refresh"]
    assert result["mode"] == "promoted_step2_decision_only"
    assert result["order_compilation_allowed"] == "False"
    assert result["new_buy_permission"] == "False"
    assert result["promoted_step3_audit_only"] == "False"
    assert step2_decision_builder.step2_promoted_decision_only_path().is_file()


def test_promoted_invalid_provenance_blocks_before_step2_extraction_and_preserves_outputs(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    prepare_tmp_repo(tmp_path, monkeypatch, permission=promoted_decision_only_permission())
    prior = _seed_prior_step2_outputs()
    monkeypatch.setattr(
        step2_decision_builder,
        "extract_template2_and_decision_packet",
        lambda **_kwargs: pytest.fail("invalid promoted provenance reached the extractor"),
    )

    with pytest.raises(ResearchDegradedModeGateError, match="promoted decision-only verification"):
        step2_decision_builder.parse_step2_output()

    blocked = read_blocked_artifact()
    assert blocked["reason"] == "promoted_step2_verification_failed"
    _assert_prior_step2_outputs_unchanged(prior)


def test_denied_step2_parse_writes_block_used_by_existing_step3_guard(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    prepare_tmp_repo(tmp_path, monkeypatch, permission=blocked_permission("NO_OUTPUT"))
    monkeypatch.setattr(step3_audit_engine, "repo_root", lambda: tmp_path)
    _seed_prior_step2_outputs()
    monkeypatch.setattr(
        step2_decision_builder,
        "extract_template2_and_decision_packet",
        lambda **_kwargs: pytest.fail("blocked parse reached the extractor"),
    )

    with pytest.raises(ResearchDegradedModeGateError, match="Step 2 blocked"):
        step2_decision_builder.parse_step2_output()

    with pytest.raises(UpstreamArtifactGuardError):
        step3_audit_engine.render_step3_prompt()

    assert step3_audit_engine.step3_blocked_by_upstream_gate_path().is_file()
