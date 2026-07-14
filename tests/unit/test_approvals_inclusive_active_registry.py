"""R2G-5b: approvals-inclusive active registry tests (groups A-G, J).

Every test proves the approvals-inclusive registry is a SEPARATE report-only
observer: it never mutates the baseline registry, binds activation ONLY to
operator_completed_anchor_sha256 (candidate data is audit-only), fails closed on
duplicates / manifest failures, carries the report-only markers, and never
contains order-shaped fields.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
import hashlib
from enum import Enum
from pathlib import Path
import textwrap
from typing import Any
from unittest.mock import Mock

import pytest
import yaml

from investment_orchestrator.research.active_research_anchor_registry import (
    build_active_research_anchor_registry,
)
from investment_orchestrator.research.actionable_promotion_eligibility import (
    evaluate_actionable_handoff_promotion_eligibility,
)
from investment_orchestrator.research.approval_registry_dual_read_diff import (
    build_approval_registry_dual_read_diff,
)
from investment_orchestrator.research.approval_registry_switch_readiness import (
    SWITCH_TARGET_FAIL_CLOSED,
    evaluate_approval_registry_switch_readiness,
)
from investment_orchestrator.research.grounding_status_observatory import (
    build_grounding_status_observatory,
)
from investment_orchestrator.research.research_anchors import (
    RESEARCH_ANCHOR_TRUSTED_DATE_INVALID,
    RESEARCH_ANCHOR_TRUSTED_DATE_MISSING,
    validate_research_anchors,
)
from investment_orchestrator.research.research_anchor_approval_manifest import (
    YAML_ALIAS_NOT_ALLOWED,
    YAML_ANCHOR_NOT_ALLOWED,
    YAML_MERGE_NOT_ALLOWED,
    build_research_anchor_approvals_validation,
    compute_operator_completed_anchor_sha256 as sha,
)
from investment_orchestrator.research.research_anchor_revocation_manifest import (
    build_research_anchor_revocations_validation,
)
from investment_orchestrator.research.approvals_inclusive_active_registry import (
    APPROVALS_SOURCE_ID,
    APPROVAL_TYPE_APPROVED_CANDIDATE,
    APPROVAL_TYPE_AUTHORED,
    ApprovalSourceState,
    CAPTURE_INVALID,
    CapturedResearchAnchorApprovalSource,
    BLOCKER_APPROVALS_MANIFEST_INVALID,
    BLOCKER_DUPLICATE_ACROSS_SOURCES,
    BLOCKER_DUPLICATE_WITHIN_APPROVALS,
    BLOCKER_DUPLICATE_TARGET_REVOCATION,
    BLOCKER_REVOCATIONS_INVALID,
    STATUS_REVOKED,
    SCHEMA_VERSION,
    _build_from_sanitized_source,
    _sanitize_captured_source,
    build_active_research_anchor_registry_with_approvals,
    build_research_anchor_approval_source_validations,
    capture_research_anchor_approval_source,
    capture_research_anchor_approval_source_text,
    compile_active_research_anchor_registry_with_approvals,
    write_active_research_anchor_registry_with_approvals,
)
from investment_orchestrator.research.support_signals import (
    REASON_MISSING_VALID_ANCHOR_SOURCE,
    build_compiled_support_signals,
)

UNIVERSE = ["QQQ", "VOO", "SMH"]
AS_OF = "2026-07-04"

_ORDER_SHAPED_KEYS = frozenset(
    {
        "account", "quantity", "shares", "order_type", "tif", "time_in_force",
        "limit_price", "stop_price", "venue", "routing", "broker", "new_buy",
        "order_compilation", "budget", "allocation",
    }
)


class _SourceBackedValidation(dict[str, Any]):
    """Test-only report mapping paired with the raw source that produced it."""

    def __init__(
        self,
        payload: dict[str, Any],
        *,
        source_text: str | None,
        today: Any,
        candidate_index: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(payload)
        self.source_text = source_text
        self.today = today
        self.candidate_index = candidate_index


def _anchor(anchor_id: str = "AI_CAPEX_2026H2", **overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "anchor_id": anchor_id,
        "anchor_type": "structural_theme",
        "applicable_tickers": ["QQQ"],
        "anchor_date_et": "2026-06-15",
        "valid_from": "2026-06-01",
        "valid_until": "2026-07-31",
        "source_type": "operator",
        "confidence_floor": "medium",
        "summary": "Operator-dated thesis grounding.",
    }
    base.update(overrides)
    return base


def _baseline(anchors: list[dict[str, Any]] | None = None, *, today: str = AS_OF) -> dict[str, Any]:
    payload = {
        "schema_version": "research_anchors_v1",
        "as_of_date": today,
        "is_llm_generated": False,
        "anchors": anchors or [],
    }
    result = validate_research_anchors(payload, allowed_universe=UNIVERSE, today=today)
    return build_active_research_anchor_registry(
        anchors_result=result,
        source_present=bool(anchors),
        source_sha256="baselinesha" if anchors else None,
        source_path="inputs/current/research_anchors.yaml",
        as_of_date=today,
    )


def _approval(
    *,
    approval_id: str = "APR-1",
    decision: str = "approve",
    completed: dict[str, Any] | None = None,
    hash_override: str | None = None,
    include_hash: bool = True,
    candidate_id: str | None = None,
    candidate_sha256: str | None = None,
) -> dict[str, Any]:
    anchor = _anchor() if completed is None else completed
    entry: dict[str, Any] = {"approval_id": approval_id, "decision": decision}
    if candidate_id is not None:
        entry["candidate_id"] = candidate_id
    if candidate_sha256 is not None:
        entry["candidate_sha256"] = candidate_sha256
    if anchor is not None:
        entry["operator_completed_anchor"] = anchor
    if include_hash:
        entry["operator_completed_anchor_sha256"] = (
            hash_override if hash_override is not None else sha(anchor)
        )
    return entry


def _validation(
    approvals: list[dict[str, Any]] | Any,
    *,
    present: bool = True,
    today: Any = AS_OF,
    candidate_index: dict[str, Any] | None = None,
    **manifest_overrides: Any,
) -> _SourceBackedValidation:
    manifest = {
        "schema_version": "research_anchor_approvals_v1",
        "is_llm_generated": False,
        "as_of_date": today,
        "approvals": approvals,
    }
    manifest.update(manifest_overrides)
    source_text = yaml.safe_dump(manifest, sort_keys=False) if present else None
    source_sha256 = (
        hashlib.sha256(source_text.encode("utf-8")).hexdigest()
        if source_text is not None
        else None
    )
    payload = build_research_anchor_approvals_validation(
        manifest=manifest if present else None,
        source_present=present,
        source_sha256=source_sha256,
        source_path="inputs/current/research_anchor_approvals.yaml",
        allowed_universe=UNIVERSE,
        today=today,
        candidate_index=candidate_index,
    )
    return _SourceBackedValidation(
        payload,
        source_text=source_text,
        today=today,
        candidate_index=candidate_index,
    )


def _revocation(**overrides: Any) -> dict[str, Any]:
    anchor = _anchor()
    base: dict[str, Any] = {
        "revocation_id": "REV-2026-07-04-001",
        "target_type": "approval_anchor",
        "approval_id": "APR-1",
        "anchor_id": "AI_CAPEX_2026H2",
        "operator_completed_anchor_sha256": sha(anchor),
        "effective_as_of": AS_OF,
        "reason": "Thesis invalidated.",
        "revoked_by": "operator",
    }
    base.update(overrides)
    return base


def _revocations_validation(
    revocations: Any,
    *,
    approvals: list[dict[str, Any]] | None = None,
    present: bool = True,
    today: str = AS_OF,
    **manifest_overrides: Any,
) -> _SourceBackedValidation:
    approvals = approvals if approvals is not None else [_approval()]
    manifest = {
        "schema_version": "research_anchor_approvals_v1",
        "is_llm_generated": False,
        "as_of_date": today,
        "approvals": approvals,
        "revocations": revocations,
    }
    manifest.update(manifest_overrides)
    source_text = yaml.safe_dump(manifest, sort_keys=False) if present else None
    source_sha256 = (
        hashlib.sha256(source_text.encode("utf-8")).hexdigest()
        if source_text is not None
        else None
    )
    approvals_validation = build_research_anchor_approvals_validation(
        manifest=manifest if present else None,
        source_present=present,
        source_sha256=source_sha256,
        source_path="inputs/current/research_anchor_approvals.yaml",
        allowed_universe=UNIVERSE,
        today=today,
    )
    payload = build_research_anchor_revocations_validation(
        manifest=manifest if present else None,
        approvals_validation=approvals_validation,
        source_present=present,
        source_sha256=source_sha256,
        source_path="inputs/current/research_anchor_approvals.yaml",
        today=today,
        as_of_date=today,
    )
    return _SourceBackedValidation(payload, source_text=source_text, today=today)


def _merge(
    baseline: dict[str, Any],
    validation: _SourceBackedValidation,
    *,
    revocations_validation: _SourceBackedValidation | None = None,
) -> dict[str, Any]:
    activation_source = revocations_validation or validation
    return build_active_research_anchor_registry_with_approvals(
        baseline=baseline,
        approval_source_text=activation_source.source_text,
        approval_source_path="inputs/current/research_anchor_approvals.yaml",
        allowed_universe=UNIVERSE,
        today=activation_source.today,
        candidate_index=validation.candidate_index,
    )


def _activate_raw(source_text: Any, *, today: Any = AS_OF) -> dict[str, Any]:
    return build_active_research_anchor_registry_with_approvals(
        baseline=_missing_baseline(),
        approval_source_text=source_text,
        approval_source_path="inputs/current/research_anchor_approvals.yaml",
        allowed_universe=UNIVERSE,
        today=today,
    )


def _forge_captured_source(**overrides: Any) -> CapturedResearchAnchorApprovalSource:
    """Bypass frozen/slots constructor checks to probe consumer-side defenses."""
    source_text = _validation([_approval()]).source_text
    source_bytes = source_text.encode("utf-8")
    fields: dict[str, Any] = {
        "source_state": ApprovalSourceState.PRESENT,
        "source_path": "inputs/current/research_anchor_approvals.yaml",
        "source_bytes": source_bytes,
        "source_text": source_text,
        "source_sha256": hashlib.sha256(source_bytes).hexdigest(),
        "read_error": None,
    }
    fields.update(overrides)
    forged = object.__new__(CapturedResearchAnchorApprovalSource)
    for field, value in fields.items():
        object.__setattr__(forged, field, value)
    return forged


class _ForeignApprovalSourceState(Enum):
    PRESENT = "present"


class _StatefulCapturedSource(CapturedResearchAnchorApprovalSource):
    """A subtype that would change authority fields if a consumer read them."""

    __slots__ = ("reads", "first_text", "second_text")

    def __getattribute__(self, name: str) -> Any:
        if name in {"source_text", "source_sha256"}:
            reads = object.__getattribute__(self, "reads") + 1
            object.__setattr__(self, "reads", reads)
            text = (
                object.__getattribute__(self, "first_text")
                if reads <= 2
                else object.__getattribute__(self, "second_text")
            )
            if name == "source_text":
                return text
            return hashlib.sha256(text.encode("utf-8")).hexdigest()
        return object.__getattribute__(self, name)


class _StaticCapturedSource(CapturedResearchAnchorApprovalSource):
    __slots__ = ()


@dataclass(frozen=True)
class _ForeignCapturedSource:
    source_state: Any
    source_path: Any
    source_bytes: Any
    source_text: Any
    source_sha256: Any
    read_error: Any


def _stateful_captured_source() -> _StatefulCapturedSource:
    first_text = _validation([]).source_text
    second_text = _validation([_approval()]).source_text
    source = object.__new__(_StatefulCapturedSource)
    object.__setattr__(source, "reads", 0)
    object.__setattr__(source, "first_text", first_text)
    object.__setattr__(source, "second_text", second_text)
    return source


def _static_captured_subclass() -> _StaticCapturedSource:
    valid = _forge_captured_source()
    source = object.__new__(_StaticCapturedSource)
    for field in (
        "source_state",
        "source_path",
        "source_bytes",
        "source_text",
        "source_sha256",
        "read_error",
    ):
        object.__setattr__(source, field, getattr(valid, field))
    return source


def _invalid_captured_source(case: str) -> CapturedResearchAnchorApprovalSource:
    valid = _forge_captured_source()
    changes: dict[str, dict[str, Any]] = {
        "present-read-error": {"read_error": "approval_source_read_error"},
        "present-empty": {
            "source_bytes": b"",
            "source_text": "",
            "source_sha256": hashlib.sha256(b"").hexdigest(),
        },
        "present-whitespace": {
            "source_bytes": b" \t\n",
            "source_text": " \t\n",
            "source_sha256": hashlib.sha256(b" \t\n").hexdigest(),
        },
        "present-text-bytes-mismatch": {"source_text": valid.source_text + "\n"},
        "present-hash-mismatch": {"source_sha256": "0" * 64},
        "absent-bytes": {
            "source_state": ApprovalSourceState.ABSENT,
            "source_text": "",
            "source_sha256": None,
        },
        "absent-text": {
            "source_state": ApprovalSourceState.ABSENT,
            "source_bytes": b"",
            "source_sha256": None,
        },
        "absent-hash": {
            "source_state": ApprovalSourceState.ABSENT,
            "source_bytes": b"",
            "source_text": "",
        },
        "absent-read-error": {
            "source_state": ApprovalSourceState.ABSENT,
            "source_bytes": b"",
            "source_text": "",
            "source_sha256": None,
            "read_error": "approval_source_read_error",
        },
        "read-error-valid-source": {
            "source_state": ApprovalSourceState.READ_ERROR,
            "read_error": "approval_source_read_error",
        },
        "read-error-hash": {
            "source_state": ApprovalSourceState.READ_ERROR,
            "source_bytes": b"",
            "source_text": "",
            "read_error": "approval_source_read_error",
        },
        "read-error-missing-reason": {
            "source_state": ApprovalSourceState.READ_ERROR,
            "source_bytes": b"",
            "source_text": "",
            "source_sha256": None,
            "read_error": None,
        },
        "read-error-blank-reason": {
            "source_state": ApprovalSourceState.READ_ERROR,
            "source_bytes": b"",
            "source_text": "",
            "source_sha256": None,
            "read_error": "",
        },
        "read-error-unknown-reason": {
            "source_state": ApprovalSourceState.READ_ERROR,
            "source_bytes": b"",
            "source_text": "",
            "source_sha256": None,
            "read_error": "caller_supplied_read_error",
        },
        "foreign-enum": {"source_state": _ForeignApprovalSourceState.PRESENT},
        "string-state": {"source_state": "present"},
        "none-state": {"source_state": None},
        "integer-state": {"source_state": 1},
    }
    return _forge_captured_source(**changes[case])


def _yaml_policy_attack_sources() -> list[tuple[str, str, str]]:
    base = yaml.safe_load(_validation([_approval()]).source_text)
    approval = base["approvals"][0]
    anchor = approval["operator_completed_anchor"]
    revocation = _revocation(
        operator_completed_anchor_sha256=approval[
            "operator_completed_anchor_sha256"
        ]
    )
    valid_text = yaml.safe_dump(base, sort_keys=False)
    with_revocation = {**base, "revocations": [revocation]}
    revocation_text = yaml.safe_dump(with_revocation, sort_keys=False)
    top_without_approvals = {
        key: value for key, value in base.items() if key != "approvals"
    }
    top_text = yaml.safe_dump(top_without_approvals, sort_keys=False)
    approval_text = textwrap.indent(
        yaml.safe_dump(approval, sort_keys=False).rstrip(), "  "
    )
    anchor_hash = approval["operator_completed_anchor_sha256"]

    return [
        ("top-mapping-anchor", "&root\n" + valid_text, YAML_ANCHOR_NOT_ALLOWED),
        (
            "top-mapping-alias",
            "&root\n" + valid_text + "copy: *root\n",
            YAML_ALIAS_NOT_ALLOWED,
        ),
        (
            "approval-entry-anchor",
            top_text + "approvals:\n- &approval\n" + approval_text + "\n",
            YAML_ANCHOR_NOT_ALLOWED,
        ),
        (
            "approval-entry-alias",
            top_text
            + "approvals:\n- &approval\n"
            + approval_text
            + "\n- *approval\n",
            YAML_ALIAS_NOT_ALLOWED,
        ),
        (
            "nested-anchor",
            valid_text.replace(
                "  operator_completed_anchor:\n",
                "  operator_completed_anchor: &anchor\n",
                1,
            ),
            YAML_ANCHOR_NOT_ALLOWED,
        ),
        (
            "nested-alias",
            valid_text.replace(
                "  operator_completed_anchor:\n",
                "  operator_completed_anchor: &anchor\n",
                1,
            ).replace(
                "  operator_completed_anchor_sha256:",
                "  source_note: *anchor\n  operator_completed_anchor_sha256:",
                1,
            ),
            YAML_ALIAS_NOT_ALLOWED,
        ),
        (
            "revocation-entry-anchor",
            revocation_text.replace(
                "revocations:\n- revocation_id:",
                "revocations:\n- &revocation\n  revocation_id:",
                1,
            ),
            YAML_ANCHOR_NOT_ALLOWED,
        ),
        (
            "revocation-entry-alias",
            revocation_text.replace(
                "revocations:\n- revocation_id:",
                "revocations:\n- &revocation\n  revocation_id:",
                1,
            )
            + "- *revocation\n",
            YAML_ALIAS_NOT_ALLOWED,
        ),
        (
            "top-merge",
            "defaults: &defaults "
            + json.dumps(base)
            + "\n<<: *defaults\n",
            YAML_MERGE_NOT_ALLOWED,
        ),
        (
            "approval-entry-merge",
            top_text
            + "approval_defaults: &approval_defaults "
            + json.dumps(approval)
            + "\napprovals:\n- <<: *approval_defaults\n",
            YAML_MERGE_NOT_ALLOWED,
        ),
        (
            "nested-merge",
            top_text
            + "anchor_defaults: &anchor_defaults "
            + json.dumps(anchor)
            + "\napprovals:\n- approval_id: APR-1\n"
            + "  decision: approve\n  operator_completed_anchor:\n"
            + "    <<: *anchor_defaults\n"
            + f"  operator_completed_anchor_sha256: {anchor_hash}\n",
            YAML_MERGE_NOT_ALLOWED,
        ),
        (
            "revocation-merge",
            valid_text
            + "revocation_defaults: &revocation_defaults "
            + json.dumps(revocation)
            + "\nrevocations:\n- <<: *revocation_defaults\n",
            YAML_MERGE_NOT_ALLOWED,
        ),
        (
            "scalar-alias",
            valid_text.replace(
                "  decision: approve\n",
                "  decision: &decision approve\n  operator_note: *decision\n",
                1,
            ),
            YAML_ALIAS_NOT_ALLOWED,
        ),
        (
            "sequence-alias",
            valid_text.replace("approvals:\n", "approvals: &entries\n", 1)
            + "revocations: *entries\n",
            YAML_ALIAS_NOT_ALLOWED,
        ),
        (
            "unused-anchor",
            "unused: &unused value\n" + valid_text,
            YAML_ANCHOR_NOT_ALLOWED,
        ),
        (
            "unknown-through-merge",
            "defaults: &defaults "
            + json.dumps({**base, "unknown_top_level": "x"})
            + "\n<<: *defaults\n",
            YAML_MERGE_NOT_ALLOWED,
        ),
        (
            "duplicate-plus-merge",
            "defaults: &defaults "
            + json.dumps(base)
            + "\n<<: *defaults\nschema_version: research_anchor_approvals_v1\n",
            YAML_MERGE_NOT_ALLOWED,
        ),
        (
            "non-string-key-plus-alias",
            "true: &value x\nfalse: *value\n" + valid_text,
            YAML_ALIAS_NOT_ALLOWED,
        ),
    ]


def _missing_baseline() -> dict[str, Any]:
    return build_active_research_anchor_registry(
        anchors_result=None,
        source_present=False,
        source_sha256=None,
        source_path="inputs/current/research_anchors.yaml",
        as_of_date=None,
    )


def _validation_at_trusted_boundary(
    today: Any,
    *,
    completed: dict[str, Any] | None = None,
) -> _SourceBackedValidation:
    approval = _approval(completed=completed)
    manifest = {
            "schema_version": "research_anchor_approvals_v1",
            "is_llm_generated": False,
            # This source date is intentionally valid but never substitutes for
            # the separate trusted activation boundary supplied in ``today``.
            "as_of_date": AS_OF,
            "approvals": [approval],
        }
    source_text = yaml.safe_dump(manifest, sort_keys=False)
    payload = build_research_anchor_approvals_validation(
        manifest=manifest,
        source_present=True,
        source_sha256=hashlib.sha256(source_text.encode("utf-8")).hexdigest(),
        source_path="inputs/current/research_anchor_approvals.yaml",
        allowed_universe=UNIVERSE,
        today=today,
    )
    return _SourceBackedValidation(payload, source_text=source_text, today=today)


def _active_ids(reg: dict[str, Any]) -> list[str]:
    return sorted(a["anchor_id"] for a in reg["active_anchors"] if a.get("anchor_id"))


def _keys(value: Any):
    if isinstance(value, dict):
        for k, v in value.items():
            yield k
            yield from _keys(v)
    elif isinstance(value, list):
        for item in value:
            yield from _keys(item)


def _assert_revocation_fail_closed(reg: dict[str, Any]) -> None:
    assert reg["registry_valid"] is False
    assert BLOCKER_REVOCATIONS_INVALID in reg["registry_blockers"]
    assert "AI_CAPEX_2026H2" not in _active_ids(reg)
    assert reg["counts"]["approved_active"] == 0
    assert reg["revocation_problems"]


# --- markers ------------------------------------------------------------------


def test_report_only_markers_present() -> None:
    reg = _merge(_baseline(), _validation([_approval()]))
    assert reg["schema_version"] == SCHEMA_VERSION
    assert reg["is_llm_generated"] is False
    assert reg["report_only"] is True
    assert reg["permission_effect"] == "none"
    assert reg["not_authorization"] is True
    assert reg["not_execution_authorization"] is True
    assert reg["cannot_affect_allowed_actions"] is True
    assert reg["is_embedded_registry"] is False
    assert reg["embedded_in_evidence_packet"] is False
    assert reg["standalone_artifact_not_consumed_by_support_signals"] is True
    assert reg["embedded_registry_selection_owned_by_evidence_packet"] is True
    for key in (
        "consumed_by_support_signals", "consumed_by_active_registry",
        "consumed_by_availability", "consumed_by_gates",
        "consumed_by_step2", "consumed_by_step4",
    ):
        assert reg[key] is False


def test_json_serializable() -> None:
    reg = _merge(_baseline(), _validation([_approval()]))
    assert json.loads(json.dumps(reg))["schema_version"] == SCHEMA_VERSION


def test_no_order_shaped_fields_anywhere() -> None:
    reg = _merge(_baseline([_anchor("VOO_T", applicable_tickers=["VOO"])]), _validation([_approval()]))
    present = {k for k in _keys(reg) if k.lower() in _ORDER_SHAPED_KEYS}
    assert present == set(), f"order-shaped keys leaked: {present}"


# --- A. happy path ------------------------------------------------------------


def test_A_happy_path_appears_with_provenance() -> None:
    baseline = _baseline([_anchor("VOO_T", applicable_tickers=["VOO"])])
    reg = _merge(baseline, _validation([_approval(candidate_id="CAND-QQQ-1", candidate_sha256="abc")]))
    assert reg["registry_valid"] is True
    row = [a for a in reg["active_anchors"] if a["anchor_id"] == "AI_CAPEX_2026H2"][0]
    assert row["source_id"] == APPROVALS_SOURCE_ID
    assert row["source_category"] == "C_operator"
    assert row["source_type"] == "operator"
    assert row["approval_type"] == APPROVAL_TYPE_APPROVED_CANDIDATE
    assert row["approval_id"] == "APR-1"
    assert row["candidate_id"] == "CAND-QQQ-1"
    assert row["candidate_sha256"] == "abc"
    assert row["operator_completed_anchor_sha256"] == sha(_anchor())
    assert len(row["content_sha256"]) == 64
    assert row["status"] == "active"
    assert row["validation"]["hash_match"] is True
    # baseline anchor still present + a source_manifest entry for the approvals YAML
    assert "VOO_T" in _active_ids(reg)
    manifest_ids = [m["source_id"] for m in reg["source_manifest"]]
    assert "operator_research_anchors_yaml" in manifest_ids
    assert APPROVALS_SOURCE_ID in manifest_ids


def test_A_baseline_registry_object_not_mutated() -> None:
    baseline = _baseline([_anchor("VOO_T", applicable_tickers=["VOO"])])
    before = json.dumps(baseline, sort_keys=True)
    _merge(baseline, _validation([_approval()]))
    assert json.dumps(baseline, sort_keys=True) == before  # merge never mutates baseline
    assert _active_ids(baseline) == ["VOO_T"]


def test_A_source_manifest_approvals_entry_fields() -> None:
    validation = _validation([_approval()])
    reg = _merge(_baseline(), validation)
    entry = [m for m in reg["source_manifest"] if m["source_id"] == APPROVALS_SOURCE_ID][0]
    assert entry["source_category"] == "C_operator"
    assert entry["source_type"] == "operator"
    assert entry["path"] == "inputs/current/research_anchor_approvals.yaml"
    assert entry["sha256"] == hashlib.sha256(
        validation.source_text.encode("utf-8")
    ).hexdigest()
    assert entry["present"] is True
    assert entry["valid"] is True
    assert isinstance(entry["problems"], list)


# --- B. approval without candidate -------------------------------------------


def test_B_no_candidate_operator_authored() -> None:
    reg = _merge(_baseline(), _validation([_approval()]))
    row = [a for a in reg["active_anchors"] if a["anchor_id"] == "AI_CAPEX_2026H2"][0]
    assert row["approval_type"] == APPROVAL_TYPE_AUTHORED
    assert row["candidate_id"] is None
    assert row["candidate_sha256"] is None
    assert row["status"] == "active"


# --- C. approval with candidate reference (audit only) -----------------------


def test_C_candidate_reference_audit_only_mismatch_does_not_block() -> None:
    # candidate index says the candidate hash is "right"; approval declares "wrong".
    val = _validation(
        [_approval(candidate_id="C1", candidate_sha256="wrong")],
        candidate_index={"C1": "right"},
    )
    reg = _merge(_baseline(), val)
    row = [a for a in reg["active_anchors"] if a["anchor_id"] == "AI_CAPEX_2026H2"][0]
    # Still active: candidate hash has ZERO activation authority.
    assert row["status"] == "active"
    assert row["approval_type"] == APPROVAL_TYPE_APPROVED_CANDIDATE
    assert row["candidate_link_status"] == "candidate_hash_mismatch"
    assert row["validation"]["hash_match"] is True  # operator anchor hash still matches


# --- R2G-5d-1 revocation overlay (standalone artifact only) -------------------


def test_R_valid_active_revocation_moves_approval_anchor_to_revoked_inactive() -> None:
    approval = _approval(candidate_id="CAND-1", candidate_sha256="candidate-audit-only")
    reg = _merge(
        _baseline([_anchor("VOO_T", applicable_tickers=["VOO"])]),
        _validation([approval]),
        revocations_validation=_revocations_validation([_revocation()], approvals=[approval]),
    )

    assert reg["registry_valid"] is True
    assert "AI_CAPEX_2026H2" not in _active_ids(reg)
    assert _active_ids(reg) == ["VOO_T"]
    assert reg["counts"]["revoked"] == 1
    assert reg["counts"]["approved_active"] == 0

    revoked = [r for r in reg["inactive_anchors"] if r.get("anchor_id") == "AI_CAPEX_2026H2"][0]
    assert revoked["status"] == STATUS_REVOKED
    assert revoked["revocation_id"] == "REV-2026-07-04-001"
    assert revoked["approval_id"] == "APR-1"
    assert revoked["effective_as_of"] == AS_OF
    assert revoked["reason"] == "Thesis invalidated."
    assert revoked["candidate_sha256"] == "candidate-audit-only"
    assert revoked["operator_completed_anchor_sha256"] == sha(_anchor())
    assert revoked["revocation"]["reason"] == "Thesis invalidated."

    assert reg["revocations_applied"] == [
        {
            "revocation_id": "REV-2026-07-04-001",
            "approval_id": "APR-1",
            "anchor_id": "AI_CAPEX_2026H2",
            "operator_completed_anchor_sha256": sha(_anchor()),
            "effective_as_of": AS_OF,
            "reason": "Thesis invalidated.",
            "target_type": "approval_anchor",
        }
    ]
    assert any(e["event"] == "anchor_revoked" for e in reg["audit_trail"])
    assert reg["permission_effect"] == "none"
    assert reg["not_authorization"] is True


def test_R_future_revocation_is_pending_and_anchor_remains_active() -> None:
    approval = _approval()
    reg = _merge(
        _baseline(),
        _validation([approval]),
        revocations_validation=_revocations_validation(
            [_revocation(effective_as_of="2026-12-31")], approvals=[approval]
        ),
    )

    assert reg["registry_valid"] is True
    assert "AI_CAPEX_2026H2" in _active_ids(reg)
    assert reg["counts"]["revoked"] == 0
    assert reg["revocations_applied"] == []
    assert reg["revocations_pending"][0]["revocation_id"] == "REV-2026-07-04-001"
    assert any(e["event"] == "revocation_pending_future" for e in reg["audit_trail"])


def test_R_revocation_validator_failure_modes_fail_overlay_closed() -> None:
    invalid_cases = [
        [_revocation(approval_id="APR-DOES-NOT-EXIST")],
        [_revocation(operator_completed_anchor_sha256="0" * 64)],
        [_revocation(anchor_id="WRONG_ANCHOR")],
        [_revocation(revocation_id="REV-DUP"), _revocation(revocation_id="REV-DUP")],
        [_revocation(target_type="baseline_anchor")],
        [{**_revocation(), "order_intent": "buy"}],
        [{**_revocation(), "candidate_sha256": "candidate-cannot-bind"}],
        [_revocation(reason="NEW_BUY")],
    ]

    for revocations in invalid_cases:
        approval = _approval()
        reg = _merge(
            _baseline(),
            _validation([approval]),
            revocations_validation=_revocations_validation(revocations, approvals=[approval]),
        )
        _assert_revocation_fail_closed(reg)
        assert reg["revocations_applied"] == []


def test_R_is_llm_generated_true_fails_overlay_closed() -> None:
    approval = _approval()
    reg = _merge(
        _baseline(),
        _validation([approval]),
        revocations_validation=_revocations_validation(
            [_revocation()], approvals=[approval], is_llm_generated=True
        ),
    )
    _assert_revocation_fail_closed(reg)


def test_R_duplicate_target_revocations_fail_overlay_closed_explicitly() -> None:
    approval = _approval()
    reg = _merge(
        _baseline(),
        _validation([approval]),
        revocations_validation=_revocations_validation(
            [_revocation(revocation_id="REV-1"), _revocation(revocation_id="REV-2")],
            approvals=[approval],
        ),
    )

    _assert_revocation_fail_closed(reg)
    assert BLOCKER_DUPLICATE_TARGET_REVOCATION in reg["registry_blockers"]
    assert any(p["reason"] == BLOCKER_DUPLICATE_TARGET_REVOCATION for p in reg["revocation_problems"])


def test_R_expired_target_revocation_does_not_resurrect_or_mark_revoked() -> None:
    stale_anchor = _anchor(valid_from="2026-01-01", valid_until="2026-02-01")
    approval = _approval(completed=stale_anchor)
    reg = _merge(
        _baseline(),
        _validation([approval]),
        revocations_validation=_revocations_validation(
            [_revocation(operator_completed_anchor_sha256=sha(stale_anchor))],
            approvals=[approval],
        ),
    )

    assert "AI_CAPEX_2026H2" not in _active_ids(reg)
    inactive = [r for r in reg["inactive_anchors"] if r.get("anchor_id") == "AI_CAPEX_2026H2"][0]
    assert inactive["status"] == "expired"
    assert reg["counts"]["revoked"] == 0
    assert reg["revocations_applied"] == []
    assert any(e["event"] == "revocation_target_not_active" for e in reg["audit_trail"])


def test_R_revocation_overlay_is_mandatory_for_runtime_builder_call() -> None:
    approval = _approval()
    baseline = _baseline()
    source = _revocations_validation([_revocation()], approvals=[approval])
    reg = build_active_research_anchor_registry_with_approvals(
        baseline=baseline,
        approval_source_text=source.source_text,
        approval_source_path="inputs/current/research_anchor_approvals.yaml",
        allowed_universe=UNIVERSE,
        today=AS_OF,
    )
    assert "AI_CAPEX_2026H2" not in _active_ids(reg)
    assert reg["counts"]["approved_active"] == 0
    assert reg["counts"]["revoked"] == 1
    assert [row["revocation_id"] for row in reg["revocations_applied"]] == [
        "REV-2026-07-04-001"
    ]


def test_R_private_raw_core_mandatorily_enforces_revocation() -> None:
    import inspect

    import investment_orchestrator.research.approvals_inclusive_active_registry as module

    core = module._build_from_captured_source
    parameters = inspect.signature(core).parameters
    assert "approvals_validation" not in parameters
    assert "revocations_validation" not in parameters
    assert "trusted_date_valid" not in parameters
    assert "apply_revocations" not in parameters

    approval = _approval()
    source = _revocations_validation([_revocation()], approvals=[approval])
    reg = core(
        baseline=_missing_baseline(),
        approval_source=capture_research_anchor_approval_source_text(
            source.source_text,
            source_path="inputs/current/research_anchor_approvals.yaml",
        ),
        allowed_universe=UNIVERSE,
        today=AS_OF,
        generated_at=None,
        candidate_index=None,
    )

    assert reg["counts"]["approved_active"] == 0
    assert reg["counts"]["revoked"] == 1


def test_R_writer_mandatorily_enforces_revocation(tmp_path: Path) -> None:
    approval = _approval()
    source = _revocations_validation([_revocation()], approvals=[approval])
    approvals_path = tmp_path / "research_anchor_approvals.yaml"
    approvals_path.write_text(source.source_text, encoding="utf-8")
    output_path = tmp_path / "with_approvals.json"

    summary = write_active_research_anchor_registry_with_approvals(
        output_path=output_path,
        anchors_path=tmp_path / "missing-research-anchors.yaml",
        approvals_path=approvals_path,
        allowed_universe=UNIVERSE,
        today=AS_OF,
    )
    payload = json.loads(output_path.read_text(encoding="utf-8"))

    assert summary["approved_active_count"] == "0"
    assert payload["counts"]["approved_active"] == 0
    assert payload["counts"]["revoked"] == 1
    assert payload["revocations_applied"][0]["revocation_id"] == "REV-2026-07-04-001"


def test_R_malformed_present_revocation_source_fails_overlay_closed() -> None:
    source = _validation([_approval()])
    manifest = yaml.safe_load(source.source_text)
    manifest["revocations"] = {"not": "a-list"}

    reg = _activate_raw(yaml.safe_dump(manifest, sort_keys=False))

    _assert_revocation_fail_closed(reg)
    assert BLOCKER_REVOCATIONS_INVALID in reg["registry_blockers"]


def test_R_approval_and_revocation_use_one_captured_source_identity() -> None:
    import inspect

    approval = _approval()
    source = _revocations_validation([_revocation()], approvals=[approval])
    reg = _activate_raw(source.source_text)
    signature = inspect.signature(build_active_research_anchor_registry_with_approvals)
    assert "revocation_source_text" not in signature.parameters
    assert "revocation_source_path" not in signature.parameters

    approvals_entry = next(
        row for row in reg["source_manifest"] if row["source_id"] == APPROVALS_SOURCE_ID
    )
    revocations_entry = next(
        row
        for row in reg["source_manifest"]
        if row["source_id"] == "operator_research_anchor_revocations_yaml"
    )
    assert approvals_entry["sha256"] == revocations_entry["sha256"]
    assert approvals_entry["path"] == revocations_entry["path"]

    with pytest.raises(TypeError):
        build_active_research_anchor_registry_with_approvals(
            baseline=_missing_baseline(),
            approval_source_text=source.source_text,
            approval_source_path="inputs/current/research_anchor_approvals.yaml",
            revocation_source_text=source.source_text,
            allowed_universe=UNIVERSE,
            today=AS_OF,
        )


def test_R_revocation_validation_artifact_cannot_cross_into_raw_source() -> None:
    artifact = dict(
        _revocations_validation([_revocation()], approvals=[_approval()])
    )

    reg = _activate_raw(yaml.safe_dump(artifact, sort_keys=False))

    assert reg["registry_valid"] is False
    assert reg["counts"]["approved_active"] == 0
    assert reg["active_anchors"] == []


def test_R_support_signals_still_does_not_read_revocation_artifacts() -> None:
    import inspect

    import investment_orchestrator.research.support_signals as ss

    assert "research_anchor_revocation_manifest" not in inspect.getsource(ss)
    assert "validate_research_anchor_revocations" not in inspect.getsource(ss)
    assert "research_anchor_revocations_validation" not in inspect.getsource(ss)


# --- D. hash failures ---------------------------------------------------------


def test_D_missing_hash_inactive() -> None:
    reg = _merge(_baseline(), _validation([_approval(include_hash=False)]))
    assert "AI_CAPEX_2026H2" not in _active_ids(reg)
    inactive = [a for a in reg["inactive_anchors"] if a.get("anchor_id") == "AI_CAPEX_2026H2"]
    assert inactive and inactive[0]["status"] == "invalid"


def test_D_mismatched_hash_inactive() -> None:
    reg = _merge(_baseline(), _validation([_approval(hash_override="0" * 64)]))
    assert "AI_CAPEX_2026H2" not in _active_ids(reg)


def test_D_mutated_anchor_after_hash_inactive() -> None:
    # Declared hash is for the original; the delivered anchor is mutated -> mismatch.
    good = _anchor()
    entry = {
        "approval_id": "APR-1",
        "decision": "approve",
        "operator_completed_anchor": _anchor(summary="MUTATED"),
        "operator_completed_anchor_sha256": sha(good),
    }
    reg = _merge(_baseline(), _validation([entry]))
    assert "AI_CAPEX_2026H2" not in _active_ids(reg)


# --- E. validation failures ---------------------------------------------------


def test_E_dateless_skeleton_inactive() -> None:
    skeleton = {
        "anchor_id": "QQQ_SKELETON", "anchor_type": "structural_theme",
        "applicable_tickers": ["QQQ"], "source_type": "operator",
        "confidence_floor": "medium", "summary": "x",
    }
    reg = _merge(_baseline(), _validation([_approval(completed=skeleton)]))
    assert "QQQ_SKELETON" not in _active_ids(reg)


def test_E_forbidden_keys_inactive() -> None:
    for bad in ({"hard_cap_budget": 1}, {"order_intent": "buy"}, {"allocation_pct": 5}):
        reg = _merge(_baseline(), _validation([_approval(completed=_anchor(**bad))]))
        assert "AI_CAPEX_2026H2" not in _active_ids(reg), bad


def test_E_new_buy_and_order_compilation_tokens_inactive() -> None:
    for token in ("NEW_BUY", "ORDER_COMPILATION"):
        reg = _merge(_baseline(), _validation([_approval(completed=_anchor(summary=token))]))
        assert "AI_CAPEX_2026H2" not in _active_ids(reg), token


def test_E_out_of_universe_inactive() -> None:
    reg = _merge(_baseline(), _validation([_approval(completed=_anchor(applicable_tickers=["TSLA"]))]))
    assert "AI_CAPEX_2026H2" not in _active_ids(reg)


def test_E_invalid_dates_inactive() -> None:
    reg = _merge(_baseline(), _validation([_approval(completed=_anchor(anchor_date_et="June 15"))]))
    assert "AI_CAPEX_2026H2" not in _active_ids(reg)


def test_E_valid_from_after_valid_until_inactive() -> None:
    reg = _merge(_baseline(), _validation([_approval(completed=_anchor(valid_from="2026-09-01", valid_until="2026-08-01"))]))
    assert "AI_CAPEX_2026H2" not in _active_ids(reg)


def test_E_future_valid_from_is_invalid_and_inactive() -> None:
    reg = _merge(
        _baseline(),
        _validation([_approval(completed=_anchor(valid_from="2026-07-05"))]),
    )
    assert "AI_CAPEX_2026H2" not in _active_ids(reg)
    row = [a for a in reg["inactive_anchors"] if a.get("anchor_id") == "AI_CAPEX_2026H2"][0]
    assert row["status"] == "invalid"


def test_E_stale_goes_expired_not_active() -> None:
    stale = _anchor(valid_from="2026-01-01", valid_until="2026-02-01")
    reg = _merge(_baseline(), _validation([_approval(completed=stale)]))
    assert "AI_CAPEX_2026H2" not in _active_ids(reg)
    row = [a for a in reg["inactive_anchors"] if a.get("anchor_id") == "AI_CAPEX_2026H2"][0]
    assert row["status"] == "expired"


def test_E_non_operator_source_type_inactive() -> None:
    bad = _anchor(source_type="analyst_memo")
    entry = {
        "approval_id": "APR-1", "decision": "approve",
        "operator_completed_anchor": bad, "operator_completed_anchor_sha256": sha(bad),
    }
    reg = _merge(_baseline(), _validation([entry]))
    assert "AI_CAPEX_2026H2" not in _active_ids(reg)


# --- F. manifest failures -----------------------------------------------------


def test_F_missing_manifest_valid_no_approval_registry() -> None:
    baseline = _baseline([_anchor("VOO_T", applicable_tickers=["VOO"])])
    reg = _merge(baseline, _validation([], present=False))
    assert reg["registry_valid"] is True
    assert _active_ids(reg) == ["VOO_T"]
    assert reg["counts"]["approved_active"] == 0
    entry = [m for m in reg["source_manifest"] if m["source_id"] == APPROVALS_SOURCE_ID][0]
    assert entry["present"] is False


def test_F_wrong_schema_fails_closed() -> None:
    baseline = _baseline([_anchor("VOO_T", applicable_tickers=["VOO"])])
    reg = _merge(baseline, _validation([_approval()], schema_version="nope"))
    assert reg["registry_valid"] is False
    assert BLOCKER_APPROVALS_MANIFEST_INVALID in reg["registry_blockers"]
    assert "AI_CAPEX_2026H2" not in _active_ids(reg)
    assert _active_ids(reg) == ["VOO_T"]  # baseline still active


def test_F_is_llm_generated_true_fails_closed() -> None:
    reg = _merge(_baseline(), _validation([_approval()], is_llm_generated=True))
    assert reg["registry_valid"] is False
    assert BLOCKER_APPROVALS_MANIFEST_INVALID in reg["registry_blockers"]
    assert "AI_CAPEX_2026H2" not in _active_ids(reg)


def test_F_duplicate_approval_id_fails_closed() -> None:
    a2 = _approval(approval_id="APR-DUP")
    b2 = _approval(approval_id="APR-DUP", completed=_anchor("OTHER", applicable_tickers=["VOO"]))
    reg = _merge(_baseline(), _validation([a2, b2]))
    assert reg["registry_valid"] is False
    assert reg["counts"]["approved_active"] == 0


def test_F_unsupported_decision_inactive() -> None:
    reg = _merge(_baseline(), _validation([_approval(decision="reject")]))
    assert "AI_CAPEX_2026H2" not in _active_ids(reg)


def test_F_revocations_not_implemented_ignored() -> None:
    # A 'revoke' decision is unsupported in R2G-5b -> treated as non-activatable, not a revocation.
    reg = _merge(_baseline([_anchor("VOO_T", applicable_tickers=["VOO"])]),
                 _validation([_approval(decision="revoke", completed=_anchor("VOO_T", applicable_tickers=["VOO"]))]))
    # baseline VOO_T stays active (no revocation path); the revoke approval does not deactivate it.
    # (VOO_T is a cross-source duplicate here -> excluded; assert no active QQQ anchor added and no revoked count)
    assert reg["counts"]["revoked"] == 0


def test_F_malformed_yaml_fails_closed() -> None:
    payload = build_research_anchor_approvals_validation(
        manifest=None, source_present=True, source_sha256="x", source_path="p",
        allowed_universe=UNIVERSE, today=AS_OF, parse_error="bad yaml",
    )
    val = _SourceBackedValidation(payload, source_text="bad: [", today=AS_OF)
    reg = _merge(_baseline([_anchor("VOO_T", applicable_tickers=["VOO"])]), val)
    assert reg["registry_valid"] is False
    assert BLOCKER_APPROVALS_MANIFEST_INVALID in reg["registry_blockers"]
    assert _active_ids(reg) == ["VOO_T"]


# --- G. duplicate policy ------------------------------------------------------


def test_G_duplicate_anchor_id_within_approvals_fails_closed() -> None:
    a1 = _approval(approval_id="APR-1", completed=_anchor("DUP"))
    a2 = _approval(approval_id="APR-2", completed=_anchor("DUP", applicable_tickers=["VOO"]))
    reg = _merge(_baseline(), _validation([a1, a2]))
    assert reg["registry_valid"] is False
    # within-approvals duplicate anchor_id also invalidates the approvals source
    assert reg["counts"]["approved_active"] == 0
    assert "DUP" not in _active_ids(reg)


def test_G_cross_source_duplicate_fails_closed_no_precedence() -> None:
    baseline = _baseline([_anchor("SHARED", applicable_tickers=["QQQ"])])
    assert _active_ids(baseline) == ["SHARED"]
    reg = _merge(baseline, _validation([_approval(completed=_anchor("SHARED"))]))
    assert reg["registry_valid"] is False
    assert BLOCKER_DUPLICATE_ACROSS_SOURCES in reg["registry_blockers"]
    dup = [d for d in reg["duplicate_blockers"] if d["reason"] == BLOCKER_DUPLICATE_ACROSS_SOURCES][0]
    assert dup["anchor_ids"] == ["SHARED"]
    # No silent precedence: SHARED is active from NEITHER source.
    assert "SHARED" not in _active_ids(reg)
    inactive_ids = [a.get("anchor_id") for a in reg["inactive_anchors"]]
    assert inactive_ids.count("SHARED") >= 1


def test_G_cross_source_duplicate_keeps_other_anchors() -> None:
    baseline = _baseline([
        _anchor("SHARED", applicable_tickers=["QQQ"]),
        _anchor("VOO_OK", applicable_tickers=["VOO"]),
    ])
    reg = _merge(baseline, _validation([_approval(completed=_anchor("SHARED"))]))
    # Conflicting SHARED excluded; the non-conflicting baseline VOO_OK stays active.
    assert "VOO_OK" in _active_ids(reg)
    assert "SHARED" not in _active_ids(reg)


# --- H. trusted approval-activation boundary ---------------------------------


def test_H_missing_baseline_valid_approval_valid_today_activates_by_existing_policy() -> None:
    baseline = _missing_baseline()
    validation = _validation_at_trusted_boundary(AS_OF)
    reg = _merge(baseline, validation)

    assert baseline["registry_valid"] is True
    assert baseline["active_anchors"] == []
    assert not any(key.startswith("activation_trusted_date") for key in validation)
    assert reg["registry_valid"] is True
    assert reg["counts"]["approved_active"] == 1
    assert _active_ids(reg) == ["AI_CAPEX_2026H2"]


@pytest.mark.parametrize(
    ("trusted_today", "expected_active", "expected_reason"),
    [
        (AS_OF, 1, None),
        (None, 0, RESEARCH_ANCHOR_TRUSTED_DATE_MISSING),
        ("not-a-date", 0, RESEARCH_ANCHOR_TRUSTED_DATE_INVALID),
        ("2026-07-04T00:00:00+00:00", 0, RESEARCH_ANCHOR_TRUSTED_DATE_INVALID),
        (object(), 0, RESEARCH_ANCHOR_TRUSTED_DATE_INVALID),
    ],
    ids=["valid", "missing", "malformed", "offset-timestamp", "unsupported-object"],
)
def test_H_path_compiler_missing_baseline_enforces_trusted_activation_boundary(
    tmp_path: Any,
    trusted_today: Any,
    expected_active: int,
    expected_reason: str | None,
) -> None:
    approval = _approval()
    approvals_path = tmp_path / "research_anchor_approvals.yaml"
    approvals_path.write_text(
        yaml.safe_dump(
            {
                "schema_version": "research_anchor_approvals_v1",
                "is_llm_generated": False,
                "as_of_date": AS_OF,
                "approvals": [approval],
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )

    reg = compile_active_research_anchor_registry_with_approvals(
        anchors_path=tmp_path / "missing-research-anchors.yaml",
        approvals_path=approvals_path,
        allowed_universe=UNIVERSE,
        today=trusted_today,
    )

    assert reg["source_manifest"][0]["present"] is False
    assert reg["source_manifest"][0]["problems"] == ["research_anchors_missing"]
    assert reg["registry_valid"] is (expected_reason is None)
    assert reg["counts"]["active"] == expected_active
    assert reg["counts"]["approved_active"] == expected_active
    assert reg["registry_blockers"] == (
        [] if expected_reason is None else [expected_reason]
    )


@pytest.mark.parametrize(
    ("trusted_today", "expected_reason"),
    [
        (None, RESEARCH_ANCHOR_TRUSTED_DATE_MISSING),
        ("not-a-date", RESEARCH_ANCHOR_TRUSTED_DATE_INVALID),
        ("2026-07-04T00:00:00+00:00", RESEARCH_ANCHOR_TRUSTED_DATE_INVALID),
        (object(), RESEARCH_ANCHOR_TRUSTED_DATE_INVALID),
    ],
    ids=["missing", "malformed", "offset-timestamp", "unsupported-object"],
)
def test_H_missing_baseline_invalid_trusted_boundary_blocks_approval_activation(
    trusted_today: Any,
    expected_reason: str,
) -> None:
    baseline = _missing_baseline()
    validation = _validation_at_trusted_boundary(trusted_today)
    approval_result = validation["approval_results"][0]
    reg = _merge(baseline, validation)

    assert baseline["registry_valid"] is True
    assert baseline["active_anchors"] == []
    # Entry validation remains date-light and non-activating. The overlay owns
    # and enforces the trusted activation boundary independently.
    assert approval_result["validation_valid"] is True
    assert not any(key.startswith("activation_trusted_date") for key in validation)
    assert reg["registry_valid"] is False
    assert reg["active_anchors"] == []
    assert reg["counts"]["active"] == 0
    assert reg["counts"]["approved_active"] == 0
    assert reg["registry_blockers"].count(expected_reason) == 1
    assert reg["inactive_anchors"][0]["reason"] == expected_reason


def test_H_persisted_validation_mapping_is_not_an_activation_input() -> None:
    import inspect

    import investment_orchestrator.research.approvals_inclusive_active_registry as module

    validation = _validation_at_trusted_boundary(AS_OF)
    signature = inspect.signature(build_active_research_anchor_registry_with_approvals)
    assert "approvals_validation" not in signature.parameters
    assert "revocations_validation" not in signature.parameters
    assert "apply_revocations" not in signature.parameters
    assert "trusted_date_valid" not in signature.parameters
    assert not hasattr(module, "_build")
    with pytest.raises(TypeError):
        build_active_research_anchor_registry_with_approvals(
            baseline=_missing_baseline(),
            approval_source_text=validation.source_text,
            approval_source_path="inputs/current/research_anchor_approvals.yaml",
            allowed_universe=UNIVERSE,
            today=AS_OF,
            approvals_validation=validation,
        )
    with pytest.raises(TypeError):
        build_active_research_anchor_registry_with_approvals(
            baseline=_missing_baseline(),
            approval_source_text=validation.source_text,
            approval_source_path="inputs/current/research_anchor_approvals.yaml",
            allowed_universe=UNIVERSE,
            today=AS_OF,
            revocations_validation=dict(validation),
        )
    with pytest.raises(TypeError):
        build_active_research_anchor_registry_with_approvals(
            baseline=_missing_baseline(),
            approval_source_text=validation.source_text,
            approval_source_path="inputs/current/research_anchor_approvals.yaml",
            allowed_universe=UNIVERSE,
            today=AS_OF,
            apply_revocations=False,
        )
    with pytest.raises(TypeError):
        build_active_research_anchor_registry_with_approvals(
            baseline=_missing_baseline(),
            approval_source_text=validation.source_text,
            approval_source_path="inputs/current/research_anchor_approvals.yaml",
            allowed_universe=UNIVERSE,
            today=AS_OF,
            eligible_approval_rows=validation["approval_results"],
        )
    with pytest.raises(TypeError):
        compile_active_research_anchor_registry_with_approvals(
            anchors_path="inputs/current/research_anchors.yaml",
            approvals_path="inputs/current/research_anchor_approvals.yaml",
            allowed_universe=UNIVERSE,
            today=AS_OF,
            apply_revocations=False,
        )


def test_H_fabricated_attestation_mapping_cannot_activate() -> None:
    validation = dict(_validation_at_trusted_boundary(None))
    validation.update(
        {
            "activation_trusted_date": AS_OF,
            "activation_trusted_date_valid": True,
            "activation_trusted_date_reason": None,
        }
    )
    reg = build_active_research_anchor_registry_with_approvals(
        baseline=_missing_baseline(),
        approval_source_text=validation,
        approval_source_path="fabricated-validation.json",
        allowed_universe=UNIVERSE,
        today=AS_OF,
    )

    assert reg["registry_valid"] is False
    assert reg["active_anchors"] == []
    assert reg["counts"]["approved_active"] == 0


def test_H_activation_date_is_independent_of_prior_validation_date() -> None:
    activates_on_b = _anchor(valid_from="2026-07-05")
    prior_at_a = _validation(
        [_approval(completed=activates_on_b)],
        today="2026-07-04",
    )
    prior_at_b = _validation(
        [_approval(completed=activates_on_b)],
        today="2026-07-05",
    )

    assert prior_at_a["approval_results"][0]["validation_valid"] is False
    assert prior_at_b["approval_results"][0]["validation_valid"] is True
    assert _activate_raw(prior_at_a.source_text, today="2026-07-05")["counts"][
        "approved_active"
    ] == 1
    assert _activate_raw(prior_at_b.source_text, today="2026-07-04")["counts"][
        "approved_active"
    ] == 0


def test_H_manifest_and_approval_dates_never_substitute_for_missing_today() -> None:
    approval = _approval()
    approval["approved_at"] = f"{AS_OF}T12:00:00Z"
    source = _validation([approval], today=AS_OF)

    reg = _activate_raw(source.source_text, today=None)

    assert reg["registry_valid"] is False
    assert reg["counts"]["approved_active"] == 0
    assert reg["registry_blockers"] == [RESEARCH_ANCHOR_TRUSTED_DATE_MISSING]


def test_H_manifest_A_validation_cannot_authorize_manifest_B() -> None:
    source_a = _validation([_approval(completed=_anchor("ANCHOR-A"))])
    source_b = _validation([_approval(completed=_anchor("ANCHOR-B"))])
    source_b["approval_results"] = source_a["approval_results"]
    source_b["source_sha256"] = source_a["source_sha256"]

    reg = _activate_raw(source_b.source_text)
    assert _active_ids(reg) == ["ANCHOR-B"]
    assert "ANCHOR-A" not in _active_ids(reg)


def test_H_post_validation_approval_content_mutation_is_revalidated() -> None:
    prior = _validation([_approval()])
    mutated = yaml.safe_load(prior.source_text)
    mutated["approvals"][0]["operator_completed_anchor"]["summary"] = (
        "changed after prior validation"
    )

    reg = _activate_raw(yaml.safe_dump(mutated, sort_keys=False))
    assert reg["counts"]["approved_active"] == 0
    assert reg["active_anchors"] == []


def test_H_source_identity_is_recomputed_from_exact_activation_bytes() -> None:
    prior = _validation([_approval()])
    prior["source_sha256"] = "0" * 64

    reg = _activate_raw(prior.source_text)
    approvals_source = next(
        entry
        for entry in reg["source_manifest"]
        if entry["source_id"] == APPROVALS_SOURCE_ID
    )
    expected_sha = hashlib.sha256(prior.source_text.encode("utf-8")).hexdigest()
    assert approvals_source["sha256"] == expected_sha
    assert approvals_source["sha256"] != prior["source_sha256"]
    assert reg["counts"]["approved_active"] == 1


def test_H_path_wrapper_reads_existing_combined_source_exactly_once(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    approvals_path = tmp_path / "research_anchor_approvals.yaml"
    approvals_path.write_text(_validation([_approval()]).source_text, encoding="utf-8")
    original_read_text = Path.read_text
    reads = 0

    def counted_read_text(path: Path, *args: Any, **kwargs: Any) -> str:
        nonlocal reads
        if path == approvals_path:
            reads += 1
        return original_read_text(path, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", counted_read_text)
    reg = compile_active_research_anchor_registry_with_approvals(
        anchors_path=tmp_path / "missing-research-anchors.yaml",
        approvals_path=approvals_path,
        allowed_universe=UNIVERSE,
        today=AS_OF,
    )

    assert reads == 1
    assert reg["counts"]["approved_active"] == 1


def test_H_normalized_text_hash_and_newline_compatibility(
    tmp_path: Path,
) -> None:
    source_text = _validation([_approval()]).source_text
    assert source_text.endswith("\n")
    lf_path = tmp_path / "lf.yaml"
    crlf_path = tmp_path / "crlf.yaml"
    cr_path = tmp_path / "cr.yaml"
    no_terminal_path = tmp_path / "no-terminal.yaml"
    non_ascii_path = tmp_path / "non-ascii.yaml"
    bom_path = tmp_path / "bom.yaml"

    lf_path.write_bytes(source_text.encode("utf-8"))
    crlf_path.write_bytes(source_text.replace("\n", "\r\n").encode("utf-8"))
    cr_path.write_bytes(source_text.replace("\n", "\r").encode("utf-8"))
    no_terminal_path.write_bytes(source_text.rstrip("\n").encode("utf-8"))

    non_ascii_manifest = yaml.safe_load(source_text)
    non_ascii_manifest["approvals"][0]["operator_note"] = "café"
    non_ascii_text = yaml.safe_dump(
        non_ascii_manifest, sort_keys=False, allow_unicode=True
    )
    non_ascii_path.write_bytes(non_ascii_text.encode("utf-8"))
    bom_path.write_bytes(b"\xef\xbb\xbf" + source_text.encode("utf-8"))

    lf = capture_research_anchor_approval_source(lf_path)
    crlf = capture_research_anchor_approval_source(crlf_path)
    cr = capture_research_anchor_approval_source(cr_path)
    no_terminal = capture_research_anchor_approval_source(no_terminal_path)
    non_ascii = capture_research_anchor_approval_source(non_ascii_path)
    bom = capture_research_anchor_approval_source(bom_path)

    expected_lf_bytes = source_text.encode("utf-8")
    expected_lf_sha = hashlib.sha256(expected_lf_bytes).hexdigest()
    assert lf.source_bytes == expected_lf_bytes
    assert crlf.source_bytes == expected_lf_bytes
    assert cr.source_bytes == expected_lf_bytes
    assert lf.source_sha256 == expected_lf_sha
    assert crlf.source_sha256 == expected_lf_sha
    assert cr.source_sha256 == expected_lf_sha
    assert no_terminal.source_sha256 == hashlib.sha256(
        source_text.rstrip("\n").encode("utf-8")
    ).hexdigest()
    assert no_terminal.source_sha256 != expected_lf_sha
    assert non_ascii.source_bytes == non_ascii_text.encode("utf-8")
    assert "café" in (non_ascii.source_text or "")
    assert bom.source_bytes == b"\xef\xbb\xbf" + expected_lf_bytes
    for captured in (lf, crlf, cr, no_terminal, non_ascii, bom):
        registry = build_active_research_anchor_registry_with_approvals(
            baseline=_missing_baseline(),
            approval_source_text=captured,
            approval_source_path=captured.source_path,
            allowed_universe=UNIVERSE,
            today=AS_OF,
        )
        assert registry["registry_valid"] is True
        assert registry["counts"]["approved_active"] == 1


def test_H_one_normalized_source_identity_reaches_every_activation_derivation(
    tmp_path: Path,
) -> None:
    from investment_orchestrator.research.approval_registry_switch_readiness import (
        build_approval_registry_switch_readiness_from_captured_source,
    )
    from investment_orchestrator.research.evidence_packet import (
        build_embedded_active_anchor_registry_selection_from_source,
    )

    source_path = tmp_path / "research_anchor_approvals.yaml"
    source_text = _validation([_approval()]).source_text
    source_path.write_bytes(source_text.replace("\n", "\r\n").encode("utf-8"))
    captured = capture_research_anchor_approval_source(source_path)
    expected_sha = hashlib.sha256(source_text.encode("utf-8")).hexdigest()
    baseline = _missing_baseline()

    approvals_validation, revocations_validation = (
        build_research_anchor_approval_source_validations(
            approval_source=captured,
            allowed_universe=UNIVERSE,
            today=AS_OF,
        )
    )
    registry = build_active_research_anchor_registry_with_approvals(
        baseline=baseline,
        approval_source_text=captured,
        approval_source_path=str(source_path),
        allowed_universe=UNIVERSE,
        today=AS_OF,
    )
    selection = build_embedded_active_anchor_registry_selection_from_source(
        baseline=baseline,
        approval_source_text=captured,
        approval_source_path=str(source_path),
        allowed_universe=UNIVERSE,
        today=AS_OF,
    )
    readiness = build_approval_registry_switch_readiness_from_captured_source(
        anchors_path=tmp_path / "missing-research-anchors.yaml",
        approval_source=captured,
        allowed_universe=UNIVERSE,
        today=AS_OF,
    )

    assert captured.source_sha256 == expected_sha
    assert approvals_validation["source_sha256"] == expected_sha
    assert revocations_validation["source_sha256"] == expected_sha
    registry_sources = {
        row["source_id"]: row["sha256"] for row in registry["source_manifest"]
    }
    assert registry_sources[APPROVALS_SOURCE_ID] == expected_sha
    assert registry_sources["operator_research_anchor_revocations_yaml"] == expected_sha
    embedded_sources = {
        row["source_id"]: row["sha256"]
        for row in selection["approvals_registry"]["source_manifest"]
    }
    assert embedded_sources[APPROVALS_SOURCE_ID] == expected_sha
    assert readiness["source_hashes"]["research_anchor_approvals_yaml"][
        "approvals_source_manifest"
    ] == expected_sha


def test_H_source_path_is_provenance_only_after_capture() -> None:
    source_text = _validation([_approval()]).source_text

    first = build_active_research_anchor_registry_with_approvals(
        baseline=_missing_baseline(),
        approval_source_text=source_text,
        approval_source_path="provenance/a.yaml",
        allowed_universe=UNIVERSE,
        today=AS_OF,
    )
    second = build_active_research_anchor_registry_with_approvals(
        baseline=_missing_baseline(),
        approval_source_text=source_text,
        approval_source_path="provenance/b.yaml",
        allowed_universe=UNIVERSE,
        today=AS_OF,
    )

    assert first["active_anchors"] == second["active_anchors"]
    assert first["counts"] == second["counts"]
    first_source = next(
        row for row in first["source_manifest"] if row["source_id"] == APPROVALS_SOURCE_ID
    )
    second_source = next(
        row for row in second["source_manifest"] if row["source_id"] == APPROVALS_SOURCE_ID
    )
    assert first_source["sha256"] == second_source["sha256"]
    assert first_source["path"] != second_source["path"]


def test_H_source_replacement_after_capture_cannot_change_activation_bytes(
    tmp_path: Path,
) -> None:
    source_path = tmp_path / "research_anchor_approvals.yaml"
    source_path.write_text(_validation([_approval()]).source_text, encoding="utf-8")
    captured = capture_research_anchor_approval_source(source_path)
    source_path.write_text("not: [valid", encoding="utf-8")

    reg = build_active_research_anchor_registry_with_approvals(
        baseline=_missing_baseline(),
        approval_source_text=captured,
        approval_source_path=str(source_path),
        allowed_universe=UNIVERSE,
        today=AS_OF,
    )

    assert reg["counts"]["approved_active"] == 1
    approvals_source = next(
        row for row in reg["source_manifest"] if row["source_id"] == APPROVALS_SOURCE_ID
    )
    assert approvals_source["sha256"] == hashlib.sha256(
        captured.source_bytes or b""
    ).hexdigest()


def test_H_source_read_error_fails_closed_instead_of_becoming_absent(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    approvals_path = tmp_path / "research_anchor_approvals.yaml"
    approvals_path.write_text(_validation([_approval()]).source_text, encoding="utf-8")
    original_read_text = Path.read_text

    def fail_approval_read(path: Path, *args: Any, **kwargs: Any) -> str:
        if path == approvals_path:
            raise PermissionError("injected read failure")
        return original_read_text(path, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", fail_approval_read)
    reg = compile_active_research_anchor_registry_with_approvals(
        anchors_path=tmp_path / "missing-research-anchors.yaml",
        approvals_path=approvals_path,
        allowed_universe=UNIVERSE,
        today=AS_OF,
    )

    assert reg["registry_valid"] is False
    assert reg["active_anchors"] == []
    assert reg["counts"].get("approved_active", 0) == 0


@pytest.mark.parametrize("content", ["", "   ", "\n"])
def test_H_present_blank_source_is_invalid_not_absent(
    tmp_path: Path,
    content: str,
) -> None:
    approvals_path = tmp_path / "research_anchor_approvals.yaml"
    approvals_path.write_text(content, encoding="utf-8")

    captured = capture_research_anchor_approval_source(approvals_path)
    reg = compile_active_research_anchor_registry_with_approvals(
        anchors_path=tmp_path / "missing-research-anchors.yaml",
        approvals_path=approvals_path,
        allowed_universe=UNIVERSE,
        today=AS_OF,
    )

    assert captured.source_state is ApprovalSourceState.PRESENT
    assert reg["registry_valid"] is False
    assert reg["counts"]["approved_active"] == 0
    source = next(
        row for row in reg["source_manifest"] if row["source_id"] == APPROVALS_SOURCE_ID
    )
    assert source["present"] is True
    assert source["valid"] is False


def test_H_genuinely_absent_source_keeps_valid_empty_policy(tmp_path: Path) -> None:
    approvals_path = tmp_path / "missing-approvals.yaml"
    captured = capture_research_anchor_approval_source(approvals_path)
    reg = compile_active_research_anchor_registry_with_approvals(
        anchors_path=tmp_path / "missing-research-anchors.yaml",
        approvals_path=approvals_path,
        allowed_universe=UNIVERSE,
        today=AS_OF,
    )

    assert captured.source_state is ApprovalSourceState.ABSENT
    assert reg["registry_valid"] is True
    assert reg["counts"]["approved_active"] == 0


def test_H_invalid_utf8_source_is_read_error_and_fails_closed(tmp_path: Path) -> None:
    approvals_path = tmp_path / "research_anchor_approvals.yaml"
    approvals_path.write_bytes(b"\xff\xfe")

    captured = capture_research_anchor_approval_source(approvals_path)
    reg = compile_active_research_anchor_registry_with_approvals(
        anchors_path=tmp_path / "missing-research-anchors.yaml",
        approvals_path=approvals_path,
        allowed_universe=UNIVERSE,
        today=AS_OF,
    )

    assert captured.source_state is ApprovalSourceState.READ_ERROR
    assert captured.read_error == "approval_source_utf8_decode_error"
    assert reg["registry_valid"] is False
    assert reg["counts"]["approved_active"] == 0


_INVALID_CAPTURE_CASES = (
    "present-read-error",
    "present-empty",
    "present-whitespace",
    "present-text-bytes-mismatch",
    "present-hash-mismatch",
    "absent-bytes",
    "absent-text",
    "absent-hash",
    "absent-read-error",
    "read-error-valid-source",
    "read-error-hash",
    "read-error-missing-reason",
    "read-error-blank-reason",
    "read-error-unknown-reason",
    "foreign-enum",
    "string-state",
    "none-state",
    "integer-state",
)


def test_H_captured_source_constructor_rejects_contradictory_authority() -> None:
    valid = _forge_captured_source()
    with pytest.raises(ValueError, match=CAPTURE_INVALID):
        CapturedResearchAnchorApprovalSource(
            source_state=ApprovalSourceState.PRESENT,
            source_path=valid.source_path,
            source_bytes=valid.source_bytes,
            source_text=valid.source_text,
            source_sha256=valid.source_sha256,
            read_error="approval_source_read_error",
        )
    with pytest.raises(ValueError, match=CAPTURE_INVALID):
        CapturedResearchAnchorApprovalSource(
            source_state=_ForeignApprovalSourceState.PRESENT,  # type: ignore[arg-type]
            source_path=valid.source_path,
            source_bytes=valid.source_bytes,
            source_text=valid.source_text,
            source_sha256=valid.source_sha256,
            read_error=None,
        )
    with pytest.raises(ValueError, match=CAPTURE_INVALID):
        CapturedResearchAnchorApprovalSource(
            source_state=ApprovalSourceState.ABSENT,
            source_path=valid.source_path,
            source_bytes=valid.source_bytes,
            source_text="",
            source_sha256=None,
            read_error=None,
        )
    with pytest.raises(ValueError, match=CAPTURE_INVALID):
        CapturedResearchAnchorApprovalSource(
            source_state=ApprovalSourceState.READ_ERROR,
            source_path=valid.source_path,
            source_bytes=valid.source_bytes,
            source_text=valid.source_text,
            source_sha256=None,
            read_error="approval_source_read_error",
        )
    with pytest.raises(ValueError, match=CAPTURE_INVALID):
        CapturedResearchAnchorApprovalSource(
            source_state=ApprovalSourceState.READ_ERROR,
            source_path=valid.source_path,
            source_bytes=b"",
            source_text="",
            source_sha256=None,
            read_error="caller_supplied_read_error",
        )


@pytest.mark.parametrize("case", _INVALID_CAPTURE_CASES)
@pytest.mark.parametrize(
    "boundary",
    [
        "validation",
        "public-registry",
        "private-core",
        "embedded-selection",
        "switch-readiness",
    ],
)
def test_H_every_captured_source_boundary_revalidates_the_full_invariant(
    case: str,
    boundary: str,
) -> None:
    captured = _invalid_captured_source(case)
    if boundary == "validation":
        approvals, revocations = build_research_anchor_approval_source_validations(
            approval_source=captured,
            allowed_universe=UNIVERSE,
            today=AS_OF,
        )
        assert approvals["source_valid"] is False
        assert CAPTURE_INVALID in json.dumps(approvals["manifest_errors"])
        assert revocations["source_valid"] is False
        assert CAPTURE_INVALID in json.dumps(revocations["blockers"])
        return

    if boundary == "public-registry":
        registry = build_active_research_anchor_registry_with_approvals(
            baseline=_missing_baseline(),
            approval_source_text=captured,
            approval_source_path=captured.source_path,
            allowed_universe=UNIVERSE,
            today=AS_OF,
        )
    elif boundary == "private-core":
        import investment_orchestrator.research.approvals_inclusive_active_registry as module

        registry = module._build_from_captured_source(
            baseline=_missing_baseline(),
            approval_source=captured,
            allowed_universe=UNIVERSE,
            today=AS_OF,
            generated_at=None,
            candidate_index=None,
        )
    elif boundary == "embedded-selection":
        from investment_orchestrator.research.evidence_packet import (
            build_embedded_active_anchor_registry_selection_from_source,
        )

        selection = build_embedded_active_anchor_registry_selection_from_source(
            baseline=_missing_baseline(),
            approval_source_text=captured,
            approval_source_path=captured.source_path,
            allowed_universe=UNIVERSE,
            today=AS_OF,
        )
        registry = selection["approvals_registry"]
        assert selection["selected_registry"]["active_anchors"] == []
    else:
        from investment_orchestrator.research.approval_registry_switch_readiness import (
            build_approval_registry_switch_readiness_from_captured_source,
        )

        readiness = build_approval_registry_switch_readiness_from_captured_source(
            anchors_path="inputs/current/missing-research-anchors.yaml",
            approval_source=captured,
            allowed_universe=UNIVERSE,
            today=AS_OF,
        )
        assert readiness["ready"] is False
        assert readiness["fail_closed_empty_required"] is True
        return

    assert registry["registry_valid"] is False
    assert registry["counts"]["approved_active"] == 0
    assert not any(
        row.get("source_category") == "operator_approval"
        for row in registry["active_anchors"]
    )


def _assert_nonexact_capture_rejected_at_every_boundary(source: Any) -> None:
    from investment_orchestrator.research.approval_registry_switch_readiness import (
        build_approval_registry_switch_readiness_from_captured_source,
    )
    from investment_orchestrator.research.evidence_packet import (
        build_embedded_active_anchor_registry_selection_from_source,
    )
    import investment_orchestrator.research.approvals_inclusive_active_registry as module

    approvals, revocations = build_research_anchor_approval_source_validations(
        approval_source=source,
        allowed_universe=UNIVERSE,
        today=AS_OF,
    )
    assert approvals["source_valid"] is False
    assert CAPTURE_INVALID in json.dumps(approvals["manifest_errors"])
    assert revocations["source_valid"] is False

    registries = [
        build_active_research_anchor_registry_with_approvals(
            baseline=_missing_baseline(),
            approval_source_text=source,
            approval_source_path="inputs/current/research_anchor_approvals.yaml",
            allowed_universe=UNIVERSE,
            today=AS_OF,
        ),
        module._build_from_captured_source(
            baseline=_missing_baseline(),
            approval_source=source,
            allowed_universe=UNIVERSE,
            today=AS_OF,
            generated_at=None,
            candidate_index=None,
        ),
    ]
    selection = build_embedded_active_anchor_registry_selection_from_source(
        baseline=_missing_baseline(),
        approval_source_text=source,
        approval_source_path="inputs/current/research_anchor_approvals.yaml",
        allowed_universe=UNIVERSE,
        today=AS_OF,
    )
    registries.append(selection["approvals_registry"])
    assert selection["selected_registry"]["active_anchors"] == []

    for registry in registries:
        assert registry["registry_valid"] is False
        assert registry["counts"].get("approved_active", 0) == 0
        assert not any(
            row.get("source_category") == "operator_approval"
            for row in registry["active_anchors"]
        )

    readiness = build_approval_registry_switch_readiness_from_captured_source(
        anchors_path="inputs/current/missing-research-anchors.yaml",
        approval_source=source,
        allowed_universe=UNIVERSE,
        today=AS_OF,
    )
    assert readiness["ready"] is False
    assert readiness["fail_closed_empty_required"] is True


def test_H_subclass_with_changing_authority_fields_is_never_read_or_accepted() -> None:
    source = _stateful_captured_source()

    _assert_nonexact_capture_rejected_at_every_boundary(source)

    assert source.reads == 0


@pytest.mark.parametrize(
    "source_factory",
    [
        _static_captured_subclass,
        lambda: _ForeignCapturedSource(
            source_state=ApprovalSourceState.PRESENT,
            source_path="inputs/current/research_anchor_approvals.yaml",
            source_bytes=_validation([_approval()]).source_text.encode("utf-8"),
            source_text=_validation([_approval()]).source_text,
            source_sha256=hashlib.sha256(
                _validation([_approval()]).source_text.encode("utf-8")
            ).hexdigest(),
            read_error=None,
        ),
        lambda: Mock(name="captured_source_proxy"),
    ],
    ids=["static-subclass", "foreign-frozen-dataclass", "mock-proxy"],
)
def test_H_subclasses_proxies_and_matching_foreign_objects_fail_closed(
    source_factory: Any,
) -> None:
    _assert_nonexact_capture_rejected_at_every_boundary(source_factory())


def test_H_sanitized_copy_is_independent_of_later_exact_object_mutation() -> None:
    first_text = _validation([_approval(completed=_anchor("SOURCE_A"))]).source_text
    second_text = _validation([_approval(completed=_anchor("SOURCE_B"))]).source_text
    original = capture_research_anchor_approval_source_text(
        first_text,
        source_path="inputs/current/research_anchor_approvals.yaml",
    )
    sanitized = _sanitize_captured_source(original)
    second_bytes = second_text.encode("utf-8")
    object.__setattr__(original, "source_bytes", second_bytes)
    object.__setattr__(original, "source_text", second_text)
    object.__setattr__(
        original,
        "source_sha256",
        hashlib.sha256(second_bytes).hexdigest(),
    )

    registry = _build_from_sanitized_source(
        baseline=_missing_baseline(),
        approval_source=sanitized,
        allowed_universe=UNIVERSE,
        today=AS_OF,
        generated_at=None,
        candidate_index=None,
    )

    assert registry["counts"]["approved_active"] == 1
    assert [row["anchor_id"] for row in registry["active_anchors"]] == ["SOURCE_A"]
    approval_source = next(
        row
        for row in registry["source_manifest"]
        if row["source_id"] == APPROVALS_SOURCE_ID
    )
    assert approval_source["sha256"] == hashlib.sha256(
        first_text.encode("utf-8")
    ).hexdigest()


@pytest.mark.parametrize(
    ("case", "source_text", "expected_reason"),
    _yaml_policy_attack_sources(),
    ids=[case for case, _, _ in _yaml_policy_attack_sources()],
)
def test_H_yaml_graph_and_merge_features_fail_before_activation(
    case: str,
    source_text: str,
    expected_reason: str,
) -> None:
    registry = _activate_raw(source_text)

    assert case
    assert registry["registry_valid"] is False
    assert registry["counts"]["approved_active"] == 0
    assert registry["active_anchors"] == []
    approval_source = next(
        row
        for row in registry["source_manifest"]
        if row["source_id"] == APPROVALS_SOURCE_ID
    )
    assert approval_source["valid"] is False
    assert any(expected_reason in problem for problem in approval_source["problems"])
    assert expected_reason in json.dumps(registry["registry_blockers"])


@pytest.mark.parametrize(
    ("mutator", "expected_reason"),
    [
        (
            lambda manifest: manifest.__setitem__("unknown_top_level", "x"),
            "research_anchor_approval_manifest_unknown_field",
        ),
        (
            lambda manifest: manifest["approvals"][0].__setitem__(
                "unknown_approval_field", "x"
            ),
            "research_anchor_approval_entry_unknown_field",
        ),
    ],
)
def test_H_unknown_combined_source_fields_cannot_activate(
    mutator: Any,
    expected_reason: str,
) -> None:
    manifest = yaml.safe_load(_validation([_approval()]).source_text)
    mutator(manifest)

    reg = _activate_raw(yaml.safe_dump(manifest, sort_keys=False))

    assert reg["registry_valid"] is False
    assert reg["counts"]["approved_active"] == 0
    approval_source = next(
        row for row in reg["source_manifest"] if row["source_id"] == APPROVALS_SOURCE_ID
    )
    assert expected_reason in approval_source["problems"]


def test_H_mutated_operator_completed_anchor_hash_fails_fresh_validation() -> None:
    prior = _validation([_approval()])
    mutated = yaml.safe_load(prior.source_text)
    mutated["approvals"][0]["operator_completed_anchor_sha256"] = "0" * 64

    reg = _activate_raw(yaml.safe_dump(mutated, sort_keys=False))
    assert reg["counts"]["approved_active"] == 0
    assert reg["active_anchors"] == []


@pytest.mark.parametrize(
    "mutation",
    [
        {},
        {"schema_version": "unknown_validation_schema"},
        {"unknown_activation_field": "copied"},
        {
            "activation_trusted_date": AS_OF,
            "activation_trusted_date_valid": True,
            "activation_trusted_date_reason": None,
        },
    ],
    ids=[
        "prior-v1-validation-artifact",
        "unknown-schema",
        "extra-field",
        "removed-attestation-fields",
    ],
)
def test_H_validation_artifacts_cannot_be_replayed_as_approval_sources(
    mutation: dict[str, Any],
) -> None:
    artifact = dict(_validation([_approval()]))
    artifact.update(mutation)

    reg = _activate_raw(yaml.safe_dump(artifact, sort_keys=False))
    assert reg["registry_valid"] is False
    assert reg["counts"]["approved_active"] == 0
    assert reg["active_anchors"] == []


@pytest.mark.parametrize(
    "anchor_type",
    [
        "scheduled_macro_event",
        "scheduled_earnings_event",
        "scheduled_rebalance_event",
    ],
)
@pytest.mark.parametrize("blocks_if_stale", [True, False])
def test_H_scheduled_approval_activation_uses_fresh_source_and_independent_today(
    anchor_type: str,
    blocks_if_stale: bool,
) -> None:
    def case(valid_from: str, valid_until: str, today: Any) -> dict[str, Any]:
        scheduled = _anchor(
            anchor_type=anchor_type,
            anchor_date_et="2026-07-20",
            valid_from=valid_from,
            valid_until=valid_until,
            blocks_if_stale=blocks_if_stale,
        )
        return _activate_raw(
            _validation_at_trusted_boundary(today, completed=scheduled).source_text,
            today=today,
        )

    inside = case("2026-06-01", "2026-07-31", AS_OF)
    starts_today = case(AS_OF, "2026-07-31", AS_OF)
    ends_today = case("2026-06-01", AS_OF, AS_OF)
    future = case("2026-07-05", "2026-07-31", AS_OF)
    expired = case("2026-06-01", "2026-07-03", AS_OF)
    missing = case("2026-06-01", "2026-07-31", None)
    malformed = case("2026-06-01", "2026-07-31", "not-a-date")
    offset = case(
        "2026-06-01",
        "2026-07-31",
        "2026-07-04T00:00:00+00:00",
    )

    scheduled = _anchor(
        anchor_type=anchor_type,
        anchor_date_et="2026-07-20",
        valid_from="2026-06-01",
        valid_until="2026-07-31",
        blocks_if_stale=blocks_if_stale,
    )
    approval = _approval(completed=scheduled)
    revoked_source = _revocations_validation(
        [
            _revocation(
                operator_completed_anchor_sha256=sha(scheduled),
            )
        ],
        approvals=[approval],
    )
    revoked = _activate_raw(revoked_source.source_text)

    assert inside["counts"]["approved_active"] == 1
    assert starts_today["counts"]["approved_active"] == 1
    assert ends_today["counts"]["approved_active"] == 1
    assert future["counts"]["approved_active"] == 0
    assert "research_anchor_not_yet_valid" in json.dumps(future["inactive_anchors"])
    assert expired["counts"]["approved_active"] == 0
    assert missing["registry_valid"] is False
    assert missing["counts"]["approved_active"] == 0
    assert missing["registry_blockers"] == [RESEARCH_ANCHOR_TRUSTED_DATE_MISSING]
    assert malformed["registry_valid"] is False
    assert malformed["counts"]["approved_active"] == 0
    assert malformed["registry_blockers"] == [RESEARCH_ANCHOR_TRUSTED_DATE_INVALID]
    assert offset["registry_valid"] is False
    assert offset["counts"]["approved_active"] == 0
    assert offset["registry_blockers"] == [RESEARCH_ANCHOR_TRUSTED_DATE_INVALID]
    assert revoked["counts"]["approved_active"] == 0
    assert revoked["counts"]["revoked"] == 1


def test_H_source_present_invalid_complete_registry_still_blocks_approval() -> None:
    anchors_result = validate_research_anchors(
        {
            "schema_version": "research_anchors_v1",
            "is_llm_generated": False,
            "anchors": [_anchor("BASELINE")],
        },
        allowed_universe=UNIVERSE,
        today=AS_OF,
    )
    baseline = build_active_research_anchor_registry(
        anchors_result=anchors_result,
        source_present=True,
        source_sha256="baselinesha",
        source_path="inputs/current/research_anchors.yaml",
        as_of_date=None,
    )
    reg = _merge(baseline, _validation_at_trusted_boundary(AS_OF))

    assert baseline["registry_valid"] is False
    assert baseline["active_anchors"] == []
    assert reg["registry_valid"] is False
    assert reg["active_anchors"] == []
    assert reg["counts"]["approved_active"] == 0


def test_H_missing_boundary_is_safe_before_readiness_and_through_downstream_observers() -> None:
    baseline = _missing_baseline()
    validation = _validation_at_trusted_boundary(None)
    reg = _merge(baseline, validation)
    diff = build_approval_registry_dual_read_diff(
        baseline_registry=baseline,
        approvals_registry=reg,
    )
    readiness = evaluate_approval_registry_switch_readiness(
        baseline_registry=baseline,
        approvals_registry=reg,
        dual_read_diff=diff,
        current_research_anchors_sha256=None,
        current_research_anchor_approvals_sha256=hashlib.sha256(
            validation.source_text.encode("utf-8")
        ).hexdigest(),
        approvals_source_present=True,
    )
    observatory = build_grounding_status_observatory(
        baseline_registry=baseline,
        approvals_registry=reg,
        approvals_validation=validation,
        readiness=readiness,
    )
    packet = {
        "schema_version": "evidence_packet_v1",
        "is_llm_generated": False,
        "universe": {
            "allowed_buy_tickers": list(UNIVERSE),
            "approved_extended_etf": [],
        },
        "research_anchors": {
            "available": False,
            "valid": False,
            "valid_anchor_count": 0,
            "anchors": [],
        },
        "active_anchor_registry": reg,
    }
    memo = {
        "schema_version": "analyst_memo_v1",
        "is_llm_generated": True,
        "confidence": "high",
        "ticker_relative_view": [
            {
                "ticker": "QQQ",
                "stance": "prefer",
                "rationale_12m_plus": "Probe thesis.",
                "anchor_id_refs": ["AI_CAPEX_2026H2"],
            }
        ],
        "avoid_or_deprioritize": [],
        "data_gaps": [],
        "source_notes": [{"claim": "probe", "source": "operator"}],
    }
    signals = build_compiled_support_signals(
        evidence_packet=packet,
        analyst_memo=memo,
        compilation_mode="evidence_plus_memo",
    )
    promotion = evaluate_actionable_handoff_promotion_eligibility(
        evidence_packet=packet,
        compiled_support_signals=signals,
        actionable_preview=None,
        actionable_candidate=None,
        actionable_candidate_validation=None,
        actionable_candidate_metadata=None,
        today=AS_OF,
    )

    assert reg["counts"]["approved_active"] == 0
    assert diff["added_by_approvals"] == []
    assert readiness["switch_target"] == SWITCH_TARGET_FAIL_CLOSED
    assert readiness["fail_closed_empty_required"] is True
    assert observatory["approvals_summary"]["active_approval_anchor_count"] == 0
    assert signals["accepted_support_signals"] == []
    assert REASON_MISSING_VALID_ANCHOR_SOURCE in signals["global_blockers"]
    assert promotion["eligible_for_promotion"] is False


# --- J. safety ----------------------------------------------------------------


def test_J_no_new_buy_or_order_compilation_grants() -> None:
    reg = _merge(_baseline(), _validation([_approval()]))
    blob = json.dumps(reg)
    assert '"NEW_BUY"' not in blob
    assert '"ORDER_COMPILATION"' not in blob


def test_J_never_raises_on_garbage() -> None:
    reg = build_active_research_anchor_registry_with_approvals(
        baseline="nonsense",
        approval_source_text=12345,
        approval_source_path=None,
        allowed_universe="nonsense",
        today=None,
    )
    assert reg["schema_version"] == SCHEMA_VERSION
    assert reg["registry_valid"] is False
