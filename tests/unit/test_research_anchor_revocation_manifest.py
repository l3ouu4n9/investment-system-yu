"""R2G-5d-0: report-only operator-revocation manifest validator tests.

Every test proves the validator is inert and precision-first: it validates but
NEVER applies revocations, binds only via approval_id + anchor_id +
operator_completed_anchor_sha256 (candidate_sha256 can never bind), fails closed on
any unresolved/inconsistent/unknown target (mandatory amendment), and carries the
report-only / not_authorization markers with no order-shaped fields.
"""

from __future__ import annotations

import json
from typing import Any

from investment_orchestrator.research.research_anchor_approval_manifest import (
    YAML_MERGE_NOT_ALLOWED,
    build_research_anchor_approvals_validation,
    compute_operator_completed_anchor_sha256 as sha,
)
from investment_orchestrator.research.research_anchor_revocation_manifest import (
    BIND_HASH_MISMATCH,
    BIND_INCONSISTENT,
    BIND_RESOLVED,
    BIND_TARGET_NOT_FOUND,
    STATUS_REJECTED,
    STATUS_VALID_ACTIVE,
    STATUS_VALID_PENDING_FUTURE,
    VALIDATION_SCHEMA_VERSION,
    build_research_anchor_revocations_validation,
    validate_research_anchor_revocations,
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


def _anchor(anchor_id: str = "AI_CAPEX_2026H2", **overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "anchor_id": anchor_id, "anchor_type": "structural_theme",
        "applicable_tickers": ["QQQ"], "anchor_date_et": "2026-06-15",
        "valid_from": "2026-06-01", "valid_until": "2026-07-31",
        "source_type": "operator", "confidence_floor": "medium", "summary": "x",
    }
    base.update(overrides)
    return base


def _approval(anchor: dict[str, Any], *, approval_id: str = "APR-1") -> dict[str, Any]:
    return {"approval_id": approval_id, "decision": "approve", "operator_completed_anchor": anchor,
            "operator_completed_anchor_sha256": sha(anchor)}


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


def _approvals_validation(approvals: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    approvals = approvals if approvals is not None else [_approval(_anchor())]
    return build_research_anchor_approvals_validation(
        manifest={"schema_version": "research_anchor_approvals_v1", "is_llm_generated": False,
                  "approvals": approvals},
        source_present=True, source_sha256="s", source_path="p",
        allowed_universe=UNIVERSE, today=AS_OF,
    )


def _run(revocations: Any, *, approvals: list[dict[str, Any]] | None = None,
         present: bool = True, as_of: str = AS_OF, **manifest_overrides: Any) -> dict[str, Any]:
    approvals = approvals if approvals is not None else [_approval(_anchor())]
    manifest = {"schema_version": "research_anchor_approvals_v1", "is_llm_generated": False,
                "as_of_date": as_of, "approvals": approvals, "revocations": revocations}
    manifest.update(manifest_overrides)
    return build_research_anchor_revocations_validation(
        manifest=manifest if present else None,
        approvals_validation=_approvals_validation(approvals),
        source_present=present, source_sha256="s" if present else None, source_path="p",
        today=as_of, as_of_date=as_of,
    )


def _first(r: dict[str, Any]) -> dict[str, Any]:
    return r["revocation_results"][0]


def _keys(value: Any):
    if isinstance(value, dict):
        for k, v in value.items():
            yield k
            yield from _keys(v)
    elif isinstance(value, list):
        for item in value:
            yield from _keys(item)


# --- markers ------------------------------------------------------------------


def test_markers_and_non_consumption() -> None:
    r = _run([_revocation()])
    assert r["schema_version"] == VALIDATION_SCHEMA_VERSION
    assert r["is_llm_generated"] is False
    assert r["report_only"] is True
    assert r["permission_effect"] == "none"
    assert r["not_authorization"] is True
    assert r["not_execution_authorization"] is True
    assert r["applies_revocations"] is False
    assert r["not_applied_report_only"] is True
    assert r["does_not_change_runtime_grounding"] is True
    assert r["cannot_affect_allowed_actions"] is True
    assert r["candidate_sha256_used_for_binding"] is False
    for k in ("consumed_by_support_signals", "consumed_by_active_registry",
              "consumed_by_approvals_inclusive_registry", "consumed_by_readiness",
              "consumed_by_availability", "consumed_by_gates", "consumed_by_step2", "consumed_by_step4"):
        assert r[k] is False


def test_json_serializable() -> None:
    r = _run([_revocation()])
    assert json.loads(json.dumps(r))["schema_version"] == VALIDATION_SCHEMA_VERSION


# --- valid --------------------------------------------------------------------


def test_valid_revocation_validates_but_applies_nothing() -> None:
    r = _run([_revocation()])
    assert r["source_valid"] is True
    assert r["revocations_valid"] is True
    rr = _first(r)
    assert rr["status"] == STATUS_VALID_ACTIVE
    assert rr["target_binding_status"] == BIND_RESOLVED
    assert rr["applied"] is False  # R2G-5d-0 applies nothing
    assert rr["errors"] == []
    assert rr["binding_hash_field"] == "operator_completed_anchor_sha256"
    assert rr["reason"] == "Thesis invalidated."
    assert rr["revoked_by"] == "operator"
    assert r["counts"] == {"checked": 1, "valid": 1, "valid_active": 1, "pending_future": 0, "invalid": 0}


# --- binding failures ---------------------------------------------------------


def test_missing_binding_field_fails() -> None:
    r = _run([_revocation(approval_id=None)])
    rr = _first(r)
    assert rr["status"] == STATUS_REJECTED
    assert r["revocations_valid"] is False
    assert any("missing required field" in e for e in rr["errors"])


def test_missing_hash_fails() -> None:
    r = _run([_revocation(operator_completed_anchor_sha256=None)])
    assert _first(r)["status"] == STATUS_REJECTED
    assert r["revocations_valid"] is False


def test_hash_mismatch_fails() -> None:
    r = _run([_revocation(operator_completed_anchor_sha256="0" * 64)])
    rr = _first(r)
    assert rr["status"] == STATUS_REJECTED
    assert rr["target_binding_status"] == BIND_HASH_MISMATCH
    assert any("does not match" in e for e in rr["errors"])


def test_inconsistent_triple_fails() -> None:
    r = _run([_revocation(anchor_id="WRONG_ANCHOR")])
    rr = _first(r)
    assert rr["status"] == STATUS_REJECTED
    assert rr["target_binding_status"] == BIND_INCONSISTENT


def test_unknown_target_fails_closed() -> None:
    # MANDATORY AMENDMENT: unknown target must fail closed, never warn/no-op.
    r = _run([_revocation(approval_id="APR-DOES-NOT-EXIST")])
    rr = _first(r)
    assert rr["status"] == STATUS_REJECTED
    assert rr["target_binding_status"] == BIND_TARGET_NOT_FOUND
    assert rr["would_fail_overlay_closed"] is True
    assert r["revocations_valid"] is False
    assert any("revocation_target_not_found" in e for e in rr["errors"])


# --- manifest-level structural failures ---------------------------------------


def test_duplicate_revocation_id_fails_closed() -> None:
    a = _revocation(revocation_id="REV-DUP")
    b = _revocation(revocation_id="REV-DUP")
    r = _run([a, b])
    assert r["source_valid"] is False
    assert r["revocations_valid"] is False
    assert any("duplicate_revocation_id" in b for b in r["blockers"])


def test_unsupported_target_type_fails() -> None:
    r = _run([_revocation(target_type="baseline_anchor")])
    rr = _first(r)
    assert rr["status"] == STATUS_REJECTED
    assert any("unsupported target_type" in e for e in rr["errors"])


def test_revocations_not_a_list_fails_closed() -> None:
    r = _run("not-a-list")
    assert r["source_valid"] is False
    assert any("revocations_not_a_list" in b for b in r["blockers"])
    assert r["revocation_results"] == []


def test_is_llm_generated_true_fails_closed() -> None:
    r = _run([_revocation()], is_llm_generated=True)
    assert r["source_valid"] is False
    assert any("is_llm_generated" in b for b in r["blockers"])


def test_missing_revocations_section_benign() -> None:
    r = build_research_anchor_revocations_validation(
        manifest={"schema_version": "research_anchor_approvals_v1", "is_llm_generated": False,
                  "approvals": [_approval(_anchor())]},
        approvals_validation=_approvals_validation(), source_present=True,
        source_sha256="s", source_path="p", today=AS_OF, as_of_date=AS_OF,
    )
    assert r["source_valid"] is True
    assert r["revocations_valid"] is True
    assert r["counts"]["checked"] == 0


def test_missing_manifest_benign() -> None:
    r = build_research_anchor_revocations_validation(
        manifest=None, approvals_validation=None, source_present=False,
        source_sha256=None, source_path="p", today=AS_OF, as_of_date=AS_OF,
    )
    assert r["source_present"] is False
    assert r["source_valid"] is True
    assert r["revocations_valid"] is True
    assert r["warnings"]


# --- forbidden / anchor-defining / candidate fields ---------------------------


def test_forbidden_budget_key_fails() -> None:
    r = _run([{**_revocation(), "hard_cap_budget": 1000}])
    assert _first(r)["status"] == STATUS_REJECTED
    assert any("forbidden" in e for e in _first(r)["errors"])


def test_forbidden_order_key_fails() -> None:
    r = _run([{**_revocation(), "order_intent": "buy"}])
    assert _first(r)["status"] == STATUS_REJECTED


def test_new_buy_token_value_fails() -> None:
    r = _run([_revocation(reason="NEW_BUY")])
    rr = _first(r)
    assert rr["status"] == STATUS_REJECTED
    assert any("action token" in e.lower() for e in rr["errors"])


def test_order_compilation_token_value_fails() -> None:
    r = _run([_revocation(revoked_by="ORDER_COMPILATION")])
    assert _first(r)["status"] == STATUS_REJECTED


def test_anchor_defining_field_fails() -> None:
    for field in ("operator_completed_anchor", "anchor_type", "valid_from", "applicable_tickers"):
        r = _run([{**_revocation(), field: "whatever"}])
        assert _first(r)["status"] == STATUS_REJECTED, field
        assert any("anchor-defining" in e or "forbidden" in e or "disallowed" in e for e in _first(r)["errors"])


def test_candidate_sha256_cannot_bind_and_is_disallowed_field() -> None:
    # candidate_sha256 is not an allowed revocation key -> rejected; never used for binding.
    r = _run([{**_revocation(), "candidate_sha256": "abc123"}])
    rr = _first(r)
    assert rr["status"] == STATUS_REJECTED
    assert rr["candidate_sha256_used_for_binding"] is False
    assert any("anchor-defining/candidate" in e or "disallowed" in e for e in rr["errors"])


def test_disallowed_arbitrary_field_fails() -> None:
    r = _run([{**_revocation(), "surprise_field": 1}])
    assert _first(r)["status"] == STATUS_REJECTED
    assert any("disallowed field" in e for e in _first(r)["errors"])


# --- reason non-authoritative -------------------------------------------------


def test_reason_required() -> None:
    r = _run([_revocation(reason="")])
    assert _first(r)["status"] == STATUS_REJECTED
    assert any("reason" in e for e in _first(r)["errors"])


def test_reason_content_never_parsed_for_logic() -> None:
    # An arbitrary (non-token) reason string does not change the outcome.
    r1 = _run([_revocation(reason="Thesis invalidated.")])
    r2 = _run([_revocation(reason="completely different free text 123")])
    assert _first(r1)["status"] == _first(r2)["status"] == STATUS_VALID_ACTIVE


# --- effective date -----------------------------------------------------------


def test_future_effective_reported_not_applied() -> None:
    r = _run([_revocation(effective_as_of="2026-12-31")])
    rr = _first(r)
    assert rr["status"] == STATUS_VALID_PENDING_FUTURE
    assert rr["effective_classification"] == "pending_future"
    assert rr["applied"] is False  # never applies anything
    assert r["counts"]["pending_future"] == 1


def test_non_iso_effective_date_fails() -> None:
    r = _run([_revocation(effective_as_of="December 31 2026")])
    assert _first(r)["status"] == STATUS_REJECTED


# --- approvals source invalid -> cannot bind ---------------------------------


def test_approvals_source_invalid_fails_closed() -> None:
    # duplicate approval_id makes the approvals source invalid -> revocations cannot bind.
    approvals = [_approval(_anchor(), approval_id="APR-DUP"),
                 _approval(_anchor("OTHER", applicable_tickers=["VOO"]), approval_id="APR-DUP")]
    r = _run([_revocation()], approvals=approvals)
    assert r["source_valid"] is False
    assert any("approvals_source_invalid" in b for b in r["blockers"])


# --- malformed YAML (disk loader) --------------------------------------------


def test_malformed_yaml_fails_closed(tmp_path: Any) -> None:
    path = tmp_path / "research_anchor_approvals.yaml"
    path.write_text("revocations: [unterminated\n : :\n")
    r = validate_research_anchor_revocations(manifest_path=path, allowed_universe=UNIVERSE, today=AS_OF)
    assert r["source_present"] is True
    assert r["source_valid"] is False
    assert any("malformed_yaml" in b for b in r["blockers"])


def test_combined_source_merge_policy_is_shared_by_revocation_disk_validator(
    tmp_path: Any,
) -> None:
    path = tmp_path / "research_anchor_approvals.yaml"
    path.write_text(
        "defaults: &defaults {schema_version: research_anchor_approvals_v1, "
        "is_llm_generated: false, approvals: [], revocations: []}\n"
        "<<: *defaults\n",
        encoding="utf-8",
    )

    result = validate_research_anchor_revocations(
        manifest_path=path,
        allowed_universe=UNIVERSE,
        today=AS_OF,
    )

    assert result["source_valid"] is False
    assert result["revocations_valid"] is False
    assert YAML_MERGE_NOT_ALLOWED in json.dumps(result["blockers"])


def test_disk_loader_missing_file_benign(tmp_path: Any) -> None:
    r = validate_research_anchor_revocations(
        manifest_path=tmp_path / "nope.yaml", allowed_universe=UNIVERSE, today=AS_OF
    )
    assert r["source_present"] is False
    assert r["source_valid"] is True
    assert r["revocations_valid"] is True


def test_disk_loader_happy_path(tmp_path: Any) -> None:
    anchor = _anchor()
    text = (
        "schema_version: research_anchor_approvals_v1\nis_llm_generated: false\n"
        'as_of_date: "2026-07-04"\napprovals:\n  - approval_id: APR-1\n    decision: approve\n'
        "    operator_completed_anchor:\n      anchor_id: AI_CAPEX_2026H2\n"
        "      anchor_type: structural_theme\n      applicable_tickers: [QQQ]\n"
        '      anchor_date_et: "2026-06-15"\n      valid_from: "2026-06-01"\n'
        '      valid_until: "2026-07-31"\n      source_type: operator\n'
        "      confidence_floor: medium\n      summary: \"x\"\n"
        f'    operator_completed_anchor_sha256: "{sha(anchor)}"\n'
        "revocations:\n  - revocation_id: REV-1\n    target_type: approval_anchor\n"
        "    approval_id: APR-1\n    anchor_id: AI_CAPEX_2026H2\n"
        f'    operator_completed_anchor_sha256: "{sha(anchor)}"\n'
        '    effective_as_of: "2026-07-04"\n    reason: "Thesis invalidated."\n    revoked_by: "operator"\n'
    )
    path = tmp_path / "research_anchor_approvals.yaml"
    path.write_text(text)
    r = validate_research_anchor_revocations(manifest_path=path, allowed_universe=UNIVERSE, today=AS_OF)
    assert r["source_valid"] is True
    assert r["revocations_valid"] is True
    assert _first(r)["target_binding_status"] == BIND_RESOLVED


# --- safety -------------------------------------------------------------------


def test_no_order_shaped_fields() -> None:
    r = _run([_revocation()])
    present = {k for k in _keys(r) if k.lower() in _ORDER_SHAPED_KEYS}
    assert present == set(), f"order-shaped keys leaked: {present}"


def test_no_new_buy_or_order_compilation_grants() -> None:
    r = _run([_revocation()])
    blob = json.dumps(r)
    assert '"NEW_BUY"' not in blob
    assert '"ORDER_COMPILATION"' not in blob


def test_never_raises_on_garbage() -> None:
    r = build_research_anchor_revocations_validation(
        manifest=12345, approvals_validation="nonsense", source_present=True,
        source_sha256=None, source_path=None,
    )
    assert r["schema_version"] == VALIDATION_SCHEMA_VERSION
    assert r["revocations_valid"] is False
