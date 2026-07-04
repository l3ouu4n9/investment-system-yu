"""R2G-5a: report-only operator-approval manifest validator tests.

Every test proves the validation artifact is diagnostic + inert: it carries the
report-only / not_authorization markers, is consumed by nothing, activates no
anchor, binds activation eligibility ONLY to operator_completed_anchor_sha256
(candidate_sha256 is audit-only), and never contains order-shaped fields.
"""

from __future__ import annotations

import json
from typing import Any

from investment_orchestrator.research.research_anchor_approval_manifest import (
    ACTIVATION_BINDING_HASH_FIELD,
    CANDIDATE_HASH_ROLE,
    CANDIDATE_LINK_HASH_MISMATCH,
    CANDIDATE_LINK_NONE,
    CANDIDATE_LINK_NOT_FOUND,
    CANDIDATE_LINK_REFERENCED,
    CANDIDATE_LINK_VERIFIED,
    MANIFEST_SCHEMA_VERSION,
    STATUS_EXPIRED,
    STATUS_REJECTED,
    STATUS_VALID_REPORT_ONLY,
    VALIDATION_SCHEMA_VERSION,
    build_research_anchor_approvals_validation,
    compute_operator_completed_anchor_sha256,
    validate_research_anchor_approvals,
)

UNIVERSE = ["QQQ", "VOO", "SMH"]
AS_OF = "2026-07-04"

_ORDER_SHAPED_KEYS = frozenset(
    {
        "account",
        "quantity",
        "shares",
        "order_type",
        "tif",
        "time_in_force",
        "limit_price",
        "stop_price",
        "venue",
        "routing",
        "broker",
        "new_buy",
        "order_compilation",
        "budget",
        "allocation",
    }
)


def _completed_anchor(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "anchor_id": "AI_CAPEX_2026H2",
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


def _approval(
    *,
    approval_id: str = "APR-2026-07-04-001",
    decision: str = "approve",
    completed: dict[str, Any] | None = None,
    hash_override: str | None = None,
    include_hash: bool = True,
    candidate_id: str | None = None,
    candidate_sha256: str | None = None,
) -> dict[str, Any]:
    anchor = _completed_anchor() if completed is None else completed
    entry: dict[str, Any] = {"approval_id": approval_id, "decision": decision}
    if candidate_id is not None:
        entry["candidate_id"] = candidate_id
    if candidate_sha256 is not None:
        entry["candidate_sha256"] = candidate_sha256
    if anchor is not None:
        entry["operator_completed_anchor"] = anchor
    if include_hash:
        entry[ACTIVATION_BINDING_HASH_FIELD] = (
            hash_override
            if hash_override is not None
            else compute_operator_completed_anchor_sha256(anchor)
        )
    return entry


def _manifest(approvals: list[dict[str, Any]] | Any, **overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "schema_version": MANIFEST_SCHEMA_VERSION,
        "is_llm_generated": False,
        "as_of_date": AS_OF,
        "approvals": approvals,
    }
    base.update(overrides)
    return base


def _build(manifest: Any, *, today: str = AS_OF, **kw: Any) -> dict[str, Any]:
    return build_research_anchor_approvals_validation(
        manifest=manifest,
        source_present=True,
        source_sha256="deadbeef",
        source_path="inputs/current/research_anchor_approvals.yaml",
        allowed_universe=UNIVERSE,
        today=today,
        as_of_date=today,
        **kw,
    )


def _keys(value: Any):
    if isinstance(value, dict):
        for k, v in value.items():
            yield k
            yield from _keys(v)
    elif isinstance(value, list):
        for item in value:
            yield from _keys(item)


# --- artifact markers ---------------------------------------------------------


def test_artifact_markers_report_only_and_inert() -> None:
    r = _build(_manifest([_approval()]))
    assert r["schema_version"] == VALIDATION_SCHEMA_VERSION
    assert r["is_llm_generated"] is False
    assert r["report_only"] is True
    assert r["permission_effect"] == "none"
    assert r["not_authorization"] is True
    assert r["not_execution_authorization"] is True
    assert r["cannot_affect_allowed_actions"] is True
    for key in (
        "consumed_by_support_signals",
        "consumed_by_active_registry",
        "consumed_by_compiler",
        "consumed_by_promotion_eligibility",
        "consumed_by_availability",
        "consumed_by_gates",
        "consumed_by_step2",
        "consumed_by_step4",
    ):
        assert r[key] is False


def test_required_top_level_fields_present() -> None:
    r = _build(_manifest([_approval()]))
    for key in (
        "generated_at",
        "as_of_date",
        "source_path",
        "source_sha256",
        "source_present",
        "source_valid",
        "manifest_errors",
        "manifest_warnings",
        "approval_results",
        "counts",
        "notes",
    ):
        assert key in r


def test_json_serializable() -> None:
    r = _build(_manifest([_approval()]))
    assert json.loads(json.dumps(r))["schema_version"] == VALIDATION_SCHEMA_VERSION


# --- 1. happy path ------------------------------------------------------------


def test_happy_path_would_activate_diagnostic() -> None:
    r = _build(_manifest([_approval()]))
    assert r["source_valid"] is True
    ar = r["approval_results"][0]
    assert ar["decision"] == "approve"
    assert ar["hash_match"] is True
    assert ar["validation_valid"] is True
    assert ar["validation_usable"] is True
    assert ar["validation_stale"] is False
    assert ar["would_activate"] is True
    assert ar["status"] == STATUS_VALID_REPORT_ONLY
    assert ar["activation_binding_hash_field"] == ACTIVATION_BINDING_HASH_FIELD
    assert ar["candidate_hash_role"] == CANDIDATE_HASH_ROLE
    assert ar["approval_errors"] == []
    assert ar["normalized_anchor_preview"]["source_type"] == "operator"
    assert r["counts"]["would_activate"] == 1
    assert r["counts"]["valid_report_only"] == 1


# --- 2. approval without candidate --------------------------------------------


def test_approval_without_candidate_allowed() -> None:
    r = _build(_manifest([_approval()]))
    ar = r["approval_results"][0]
    assert ar["candidate_id"] is None
    assert ar["candidate_sha256"] is None
    assert ar["candidate_link_status"] == CANDIDATE_LINK_NONE
    assert ar["would_activate"] is True


# --- 3. approval with candidate reference (audit only) ------------------------


def test_candidate_reference_recorded_but_audit_only() -> None:
    r = _build(
        _manifest(
            [_approval(candidate_id="CAND-QQQ-56128cc1ceb4", candidate_sha256="a" * 64)]
        )
    )
    ar = r["approval_results"][0]
    assert ar["candidate_id"] == "CAND-QQQ-56128cc1ceb4"
    assert ar["candidate_sha256"] == "a" * 64
    # No candidate index supplied -> reference recorded, not verified.
    assert ar["candidate_link_status"] == CANDIDATE_LINK_REFERENCED
    # Activation is bound to the operator-completed anchor hash, NOT the candidate.
    assert ar["candidate_hash_role"] == CANDIDATE_HASH_ROLE
    assert ar["would_activate"] is True


def test_candidate_verified_against_index_audit_only() -> None:
    r = _build(
        _manifest([_approval(candidate_id="CAND-1", candidate_sha256="cafe")]),
        candidate_index={"CAND-1": "cafe"},
    )
    ar = r["approval_results"][0]
    assert ar["candidate_link_status"] == CANDIDATE_LINK_VERIFIED
    assert ar["would_activate"] is True


def test_candidate_not_found_is_warning_only() -> None:
    r = _build(
        _manifest([_approval(candidate_id="CAND-MISSING", candidate_sha256="cafe")]),
        candidate_index={"CAND-1": "cafe"},
    )
    ar = r["approval_results"][0]
    assert ar["candidate_link_status"] == CANDIDATE_LINK_NOT_FOUND
    assert ar["approval_errors"] == []
    assert ar["approval_warnings"]
    # Warning does NOT block activation — binding is the operator anchor hash.
    assert ar["would_activate"] is True


# --- 4. candidate mismatch (warning only; no activation authority) ------------


def test_candidate_hash_mismatch_is_warning_only_not_authority() -> None:
    r = _build(
        _manifest([_approval(candidate_id="CAND-1", candidate_sha256="wrong")]),
        candidate_index={"CAND-1": "cafe"},
    )
    ar = r["approval_results"][0]
    assert ar["candidate_link_status"] == CANDIDATE_LINK_HASH_MISMATCH
    assert ar["approval_errors"] == []  # mismatch is NOT an error
    assert ar["approval_warnings"]
    # Candidate hash has ZERO activation authority: the operator anchor hash
    # still matches, so this remains would_activate:true.
    assert ar["hash_match"] is True
    assert ar["would_activate"] is True


# --- 5. completed anchor hash mismatch ----------------------------------------


def test_completed_anchor_hash_mismatch_rejected() -> None:
    r = _build(_manifest([_approval(hash_override="0" * 64)]))
    ar = r["approval_results"][0]
    assert ar["hash_match"] is False
    assert ar["would_activate"] is False
    assert ar["status"] == STATUS_REJECTED
    assert any("mismatch" in e for e in ar["approval_errors"])


# --- 6. missing completed anchor hash -----------------------------------------


def test_missing_completed_anchor_hash_rejected() -> None:
    r = _build(_manifest([_approval(include_hash=False)]))
    ar = r["approval_results"][0]
    assert ar["operator_completed_anchor_sha256"] is None
    assert ar["would_activate"] is False
    assert ar["status"] == STATUS_REJECTED
    assert any(ACTIVATION_BINDING_HASH_FIELD in e for e in ar["approval_errors"])


def test_missing_completed_anchor_rejected() -> None:
    entry = {"approval_id": "APR-X", "decision": "approve"}
    r = _build(_manifest([entry]))
    ar = r["approval_results"][0]
    assert ar["would_activate"] is False
    assert ar["status"] == STATUS_REJECTED
    assert any("operator_completed_anchor is required" in e for e in ar["approval_errors"])


# --- 7. dateless skeleton as completed anchor ---------------------------------


def test_dateless_skeleton_rejected() -> None:
    skeleton = {
        "anchor_id": "QQQ_CANDIDATE_ANCHOR",
        "anchor_type": "structural_theme",
        "applicable_tickers": ["QQQ"],
        "source_type": "operator",
        "confidence_floor": "medium",
        "summary": "AI capex",
    }
    r = _build(_manifest([_approval(completed=skeleton)]))
    ar = r["approval_results"][0]
    # Hash still matches (declared over the skeleton), but validation fails.
    assert ar["hash_match"] is True
    assert ar["validation_valid"] is False
    assert ar["would_activate"] is False
    assert ar["status"] == STATUS_REJECTED
    assert any("anchor_date_et" in e or "valid_from" in e for e in ar["approval_errors"])


# --- 8. malformed manifest ----------------------------------------------------


def test_malformed_manifest_fails_closed() -> None:
    r = build_research_anchor_approvals_validation(
        manifest=None,
        source_present=True,
        source_sha256="x",
        source_path="p",
        allowed_universe=UNIVERSE,
        today=AS_OF,
        parse_error="mapping values are not allowed here",
    )
    assert r["source_valid"] is False
    assert r["approval_results"] == []
    assert any("malformed_yaml" in e for e in r["manifest_errors"])


def test_top_level_not_mapping_fails_closed() -> None:
    r = _build([1, 2, 3])
    assert r["source_valid"] is False
    assert r["approval_results"] == []
    assert r["manifest_errors"]


def test_never_raises_on_garbage() -> None:
    r = build_research_anchor_approvals_validation(
        manifest={"schema_version": MANIFEST_SCHEMA_VERSION, "is_llm_generated": False,
                  "approvals": [12345, "nonsense", {"decision": "approve"}]},
        source_present=True,
        source_sha256=None,
        source_path=None,
        allowed_universe="not-a-list",
        today=AS_OF,
    )
    assert r["schema_version"] == VALIDATION_SCHEMA_VERSION
    assert len(r["approval_results"]) == 3


def test_missing_manifest_valid_empty_report() -> None:
    r = build_research_anchor_approvals_validation(
        manifest=None,
        source_present=False,
        source_sha256=None,
        source_path="inputs/current/research_anchor_approvals.yaml",
        allowed_universe=UNIVERSE,
        today=AS_OF,
    )
    assert r["source_present"] is False
    assert r["approval_results"] == []
    assert r["manifest_errors"] == []
    assert r["manifest_warnings"]
    assert r["counts"]["approvals"] == 0


# --- 9. is_llm_generated true -------------------------------------------------


def test_is_llm_generated_true_rejected() -> None:
    r = _build(_manifest([_approval()], is_llm_generated=True))
    assert r["source_valid"] is False
    assert any("is_llm_generated" in e for e in r["manifest_errors"])
    # Even a hash-matching, valid anchor cannot activate when the manifest is invalid.
    assert r["approval_results"][0]["would_activate"] is False


def test_wrong_schema_version_rejected() -> None:
    r = _build(_manifest([_approval()], schema_version="something_else"))
    assert r["source_valid"] is False
    assert any("schema_version" in e for e in r["manifest_errors"])
    assert r["approval_results"][0]["would_activate"] is False


# --- 10. forbidden keys / tokens ----------------------------------------------


def test_forbidden_budget_key_rejected() -> None:
    r = _build(_manifest([_approval(completed=_completed_anchor(hard_cap_budget=1000))]))
    ar = r["approval_results"][0]
    assert ar["validation_valid"] is False
    assert ar["would_activate"] is False
    assert any("forbidden" in e for e in ar["approval_errors"])


def test_forbidden_order_key_rejected() -> None:
    r = _build(_manifest([_approval(completed=_completed_anchor(order_intent="buy"))]))
    ar = r["approval_results"][0]
    assert ar["validation_valid"] is False
    assert ar["would_activate"] is False


def test_new_buy_token_value_rejected() -> None:
    r = _build(_manifest([_approval(completed=_completed_anchor(summary="NEW_BUY"))]))
    ar = r["approval_results"][0]
    assert ar["validation_valid"] is False
    assert ar["would_activate"] is False
    assert any("action token" in e.lower() for e in ar["approval_errors"])


def test_order_compilation_token_value_rejected() -> None:
    r = _build(_manifest([_approval(completed=_completed_anchor(summary="ORDER_COMPILATION"))]))
    ar = r["approval_results"][0]
    assert ar["validation_valid"] is False
    assert ar["would_activate"] is False


# --- 11. out-of-universe ticker -----------------------------------------------


def test_out_of_universe_ticker_rejected() -> None:
    r = _build(_manifest([_approval(completed=_completed_anchor(applicable_tickers=["TSLA"]))]))
    ar = r["approval_results"][0]
    assert ar["validation_valid"] is False
    assert ar["would_activate"] is False
    assert any("universe" in e for e in ar["approval_errors"])


# --- 12. invalid dates / valid_from > valid_until -----------------------------


def test_valid_from_after_valid_until_rejected() -> None:
    r = _build(
        _manifest(
            [_approval(completed=_completed_anchor(valid_from="2026-08-01", valid_until="2026-07-01"))]
        )
    )
    ar = r["approval_results"][0]
    assert ar["validation_valid"] is False
    assert ar["would_activate"] is False


def test_non_iso_date_rejected() -> None:
    r = _build(_manifest([_approval(completed=_completed_anchor(anchor_date_et="June 15 2026"))]))
    ar = r["approval_results"][0]
    assert ar["validation_valid"] is False
    assert ar["would_activate"] is False


# --- 13. stale / expired ------------------------------------------------------


def test_stale_anchor_expired_not_active() -> None:
    stale = _completed_anchor(valid_from="2026-01-01", valid_until="2026-02-01")
    r = _build(_manifest([_approval(completed=stale)]), today="2026-07-04")
    ar = r["approval_results"][0]
    assert ar["hash_match"] is True
    assert ar["validation_valid"] is True
    assert ar["validation_stale"] is True
    assert ar["would_activate"] is False
    assert ar["status"] == STATUS_EXPIRED


# --- 14. duplicate approval_id ------------------------------------------------


def test_duplicate_approval_id_fails_closed() -> None:
    a1 = _approval(approval_id="APR-DUP")
    a2 = _approval(
        approval_id="APR-DUP", completed=_completed_anchor(anchor_id="OTHER", applicable_tickers=["VOO"])
    )
    r = _build(_manifest([a1, a2]))
    assert r["source_valid"] is False
    assert any("duplicate approval_id" in e for e in r["manifest_errors"])
    for ar in r["approval_results"]:
        assert ar["would_activate"] is False


# --- 15. duplicate anchor_id within manifest ----------------------------------


def test_duplicate_anchor_id_fails_closed() -> None:
    a1 = _approval(approval_id="APR-1")
    a2 = _approval(approval_id="APR-2")  # same anchor_id AI_CAPEX_2026H2
    r = _build(_manifest([a1, a2]))
    assert r["source_valid"] is False
    assert any("anchor_id" in e for e in r["manifest_errors"])
    for ar in r["approval_results"]:
        assert ar["would_activate"] is False


# --- 16. non-consumption / safety ---------------------------------------------


def test_no_order_shaped_fields_anywhere() -> None:
    r = _build(_manifest([_approval()]))
    present = {k for k in _keys(r) if k.lower() in _ORDER_SHAPED_KEYS}
    assert present == set(), f"order-shaped keys leaked: {present}"


def test_no_new_buy_or_order_compilation_grants_in_output() -> None:
    r = _build(_manifest([_approval()]))
    blob = json.dumps(r)
    assert '"NEW_BUY"' not in blob
    assert '"ORDER_COMPILATION"' not in blob


def test_unsupported_decision_rejected() -> None:
    # Revocations are deferred to R2G-5d; only 'approve' is supported here.
    r = _build(_manifest([_approval(decision="reject")]))
    ar = r["approval_results"][0]
    assert ar["would_activate"] is False
    assert ar["status"] == STATUS_REJECTED
    assert any("decision must be" in e for e in ar["approval_errors"])


def test_disk_loader_missing_file_valid_empty(tmp_path: Any) -> None:
    r = validate_research_anchor_approvals(
        manifest_path=tmp_path / "does_not_exist.yaml",
        allowed_universe=UNIVERSE,
        today=AS_OF,
    )
    assert r["source_present"] is False
    assert r["source_valid"] is False
    assert r["approval_results"] == []
    assert r["manifest_warnings"]


def test_disk_loader_happy_path(tmp_path: Any) -> None:
    anchor = _completed_anchor()
    manifest_text = (
        "schema_version: research_anchor_approvals_v1\n"
        "is_llm_generated: false\n"
        'as_of_date: "2026-07-04"\n'
        "approvals:\n"
        "  - approval_id: APR-2026-07-04-001\n"
        "    decision: approve\n"
        "    operator_completed_anchor:\n"
        "      anchor_id: AI_CAPEX_2026H2\n"
        "      anchor_type: structural_theme\n"
        "      applicable_tickers: [QQQ]\n"
        '      anchor_date_et: "2026-06-15"\n'
        '      valid_from: "2026-06-01"\n'
        '      valid_until: "2026-07-31"\n'
        "      source_type: operator\n"
        "      confidence_floor: medium\n"
        '      summary: "Operator-dated thesis grounding."\n'
        f'    operator_completed_anchor_sha256: "{compute_operator_completed_anchor_sha256(anchor)}"\n'
    )
    path = tmp_path / "research_anchor_approvals.yaml"
    path.write_text(manifest_text)
    r = validate_research_anchor_approvals(
        manifest_path=path, allowed_universe=UNIVERSE, today=AS_OF
    )
    assert r["source_present"] is True
    assert r["source_valid"] is True
    assert r["source_sha256"] is not None
    ar = r["approval_results"][0]
    assert ar["hash_match"] is True
    assert ar["would_activate"] is True


def test_disk_loader_malformed_yaml_fails_closed(tmp_path: Any) -> None:
    path = tmp_path / "bad.yaml"
    path.write_text("approvals: [unterminated\n  : :\n")
    r = validate_research_anchor_approvals(
        manifest_path=path, allowed_universe=UNIVERSE, today=AS_OF
    )
    assert r["source_present"] is True
    assert r["source_valid"] is False


# --- hash-binding model -------------------------------------------------------


def test_operator_anchor_sha_is_activation_binding() -> None:
    anchor = _completed_anchor()
    expected = compute_operator_completed_anchor_sha256(anchor)
    assert isinstance(expected, str) and len(expected) == 64
    # Changing ANY field changes the binding hash -> mismatch -> no activation.
    mutated = _completed_anchor(summary="different")
    assert compute_operator_completed_anchor_sha256(mutated) != expected
