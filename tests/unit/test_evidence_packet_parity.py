"""S1A-10 evidence_packet shadow-parity helper tests.

Pure, deterministic, fail-closed comparison of a PRODUCTION evidence packet vs a
Step 1A-sourced evidence packet before the future S1A-11 guarded disk-writer
switch. Only ``generated_at`` may be normalized; every runtime-relevant field is
compared exactly, and an unknown ISO-datetime field inside the runtime subtree
fails closed. These helpers read no disk, call no LLM, write no files, and carry
no authority.
"""

from __future__ import annotations

import copy
from typing import Any

from investment_orchestrator.research.evidence_packet import (
    compare_evidence_packet_runtime_parity,
    normalize_evidence_packet_for_parity,
)


_PROD_GENERATED_AT = "2026-07-07T03:15:58.676783+00:00"


def _registry(*, generated_at: Any = _PROD_GENERATED_AT, **overrides: Any) -> dict[str, Any]:
    reg: dict[str, Any] = {
        "schema_version": "active_research_anchor_registry_v1",
        "is_llm_generated": False,
        "generated_at": generated_at,
        "as_of_date": "2026-06-28",
        "registry_valid": True,
        "registry_blockers": [],
        "duplicate_blockers": [],
        "active_anchors": [
            {"anchor_id": "AI_CAPEX", "content_sha256": "a" * 64},
            {"anchor_id": "SEMI_UP", "content_sha256": "b" * 64},
        ],
        "inactive_anchors": [],
        "counts": {"active": 2, "expired": 0, "invalid": 0},
        "source_manifest": [
            {"source_id": "operator_research_anchors_yaml", "present": True, "sha256": "d" * 64}
        ],
        "revocations_applied": [],
        "revocations_pending": [],
        "revocation_problems": [],
    }
    reg.update(overrides)
    return reg


def _packet(*, generated_at: Any = _PROD_GENERATED_AT, registry: Any = None, **overrides: Any) -> dict[str, Any]:
    packet: dict[str, Any] = {
        "schema_version": "evidence_packet_v1",
        "source": "deterministic_step1",
        "report_only": True,
        "is_llm_generated": False,
        "generated_at": generated_at,
        "strategy_settings_hash": "settings-hash-123",
        "universe": {"allowed_buy_tickers": ["QQQ", "VOO", "SMH"], "core_universe": ["QQQ", "VOO"]},
        "budget_settings": {"hard_cap_open_orders_budget": 38211.29},
        "data_gaps": [],
        "active_anchor_registry": registry
        if registry is not None
        else _registry(generated_at=generated_at),
        "source_artifacts": {"strategy_settings": "/repo/inputs/current/strategy_settings.yaml"},
        "last_good_research_summary": {"available": False},
        "portfolio_snapshot_summary": {"available": True, "positions": 4},
    }
    packet.update(overrides)
    return packet


def _prod_and_step1a() -> tuple[dict[str, Any], dict[str, Any]]:
    """A production packet (wall-clock generated_at) and a Step1A packet (None)."""
    return _packet(generated_at=_PROD_GENERATED_AT), _packet(generated_at=None)


# --- normalizer ---------------------------------------------------------------


def test_normalizer_replaces_only_generated_at_and_records_paths() -> None:
    packet = _packet(generated_at=_PROD_GENERATED_AT)
    normalized, normalized_paths, diagnostics = normalize_evidence_packet_for_parity(packet)

    assert normalized_paths == ["active_anchor_registry.generated_at", "generated_at"]
    assert normalized["generated_at"] == "<normalized_generated_at>"
    assert normalized["active_anchor_registry"]["generated_at"] == "<normalized_generated_at>"
    assert diagnostics["normalization_allowlist"] == ["generated_at"]
    assert diagnostics["unknown_runtime_timestamp_fields"] == []
    # Content fields untouched.
    assert normalized["active_anchor_registry"]["as_of_date"] == "2026-06-28"
    assert normalized["active_anchor_registry"]["source_manifest"][0]["sha256"] == "d" * 64
    assert normalized["strategy_settings_hash"] == "settings-hash-123"


def test_normalizer_does_not_mutate_input() -> None:
    packet = _packet(generated_at=_PROD_GENERATED_AT)
    snapshot = copy.deepcopy(packet)
    normalize_evidence_packet_for_parity(packet)
    assert packet == snapshot  # input (support_signals' object) untouched


def test_normalizer_does_not_touch_date_only_content_fields() -> None:
    # as_of_date / valid_until / anchor_date_et are date-only, NOT ISO datetimes,
    # and must never be normalized or flagged as unknown timestamps.
    reg = _registry(
        active_anchors=[
            {
                "anchor_id": "AI",
                "content_sha256": "c" * 64,
                "anchor_date_et": "2026-06-15",
                "valid_until": "2026-07-31",
            }
        ]
    )
    normalized, normalized_paths, diagnostics = normalize_evidence_packet_for_parity(
        _packet(registry=reg)
    )
    anchor = normalized["active_anchor_registry"]["active_anchors"][0]
    assert anchor["anchor_date_et"] == "2026-06-15"
    assert anchor["valid_until"] == "2026-07-31"
    assert diagnostics["unknown_runtime_timestamp_fields"] == []
    assert "active_anchor_registry.as_of_date" not in normalized_paths


# --- parity pass --------------------------------------------------------------


def test_generated_at_only_difference_passes_with_diagnostics() -> None:
    prod, step1a = _prod_and_step1a()
    result = compare_evidence_packet_runtime_parity(prod, step1a)
    assert result["subtree_match"] is True
    assert result["differences"] == []
    assert result["unknown_runtime_timestamp_fields"] == []
    assert result["normalized_paths"] == ["active_anchor_registry.generated_at", "generated_at"]
    assert result["normalization_allowlist"] == ["generated_at"]
    # report-only markers on the diagnostics object.
    assert result["report_only"] is True
    assert result["not_authorization"] is True
    assert result["consumed_by_gates"] is False


def test_identical_packets_pass() -> None:
    prod = _packet(generated_at=_PROD_GENERATED_AT)
    result = compare_evidence_packet_runtime_parity(prod, copy.deepcopy(prod))
    assert result["subtree_match"] is True
    assert result["differences"] == []


# --- runtime-relevant mismatch failures ---------------------------------------


def test_active_anchor_set_mismatch_fails() -> None:
    prod, step1a = _prod_and_step1a()
    step1a["active_anchor_registry"]["active_anchors"] = [
        {"anchor_id": "AI_CAPEX", "content_sha256": "a" * 64}
    ]  # dropped one anchor
    result = compare_evidence_packet_runtime_parity(prod, step1a)
    assert result["subtree_match"] is False
    assert any("active_anchor_registry" in d["path"] for d in result["differences"])


def test_per_anchor_content_sha256_mismatch_fails() -> None:
    prod, step1a = _prod_and_step1a()
    step1a["active_anchor_registry"]["active_anchors"][0]["content_sha256"] = "f" * 64
    result = compare_evidence_packet_runtime_parity(prod, step1a)
    assert result["subtree_match"] is False
    assert any("content_sha256" in d["path"] for d in result["differences"])


def test_source_manifest_sha_mismatch_fails() -> None:
    prod, step1a = _prod_and_step1a()
    step1a["active_anchor_registry"]["source_manifest"][0]["sha256"] = "0" * 64
    result = compare_evidence_packet_runtime_parity(prod, step1a)
    assert result["subtree_match"] is False
    assert any("source_manifest" in d["path"] for d in result["differences"])


def test_registry_valid_mismatch_fails() -> None:
    prod, step1a = _prod_and_step1a()
    step1a["active_anchor_registry"]["registry_valid"] = False
    result = compare_evidence_packet_runtime_parity(prod, step1a)
    assert result["subtree_match"] is False
    assert any(d["path"] == "active_anchor_registry.registry_valid" for d in result["differences"])


def test_fail_closed_marker_mismatch_fails() -> None:
    prod, step1a = _prod_and_step1a()
    step1a["active_anchor_registry"]["registry_blockers"] = ["fail_closed_reason"]
    result = compare_evidence_packet_runtime_parity(prod, step1a)
    assert result["subtree_match"] is False
    assert any("registry_blockers" in d["path"] for d in result["differences"])


def test_revocations_applied_mismatch_fails() -> None:
    prod, step1a = _prod_and_step1a()
    step1a["active_anchor_registry"]["revocations_applied"] = [{"anchor_id": "AI_CAPEX"}]
    result = compare_evidence_packet_runtime_parity(prod, step1a)
    assert result["subtree_match"] is False
    assert any("revocations_applied" in d["path"] for d in result["differences"])


def test_data_gaps_mismatch_fails() -> None:
    prod, step1a = _prod_and_step1a()
    step1a["data_gaps"] = [{"field": "universe", "reason": "DATA_GAP"}]
    result = compare_evidence_packet_runtime_parity(prod, step1a)
    assert result["subtree_match"] is False
    assert any(d["path"].startswith("data_gaps") for d in result["differences"])


def test_universe_mismatch_fails() -> None:
    prod, step1a = _prod_and_step1a()
    step1a["universe"]["allowed_buy_tickers"] = ["QQQ"]
    result = compare_evidence_packet_runtime_parity(prod, step1a)
    assert result["subtree_match"] is False
    assert any(d["path"].startswith("universe") for d in result["differences"])


def test_budget_settings_mismatch_fails() -> None:
    prod, step1a = _prod_and_step1a()
    step1a["budget_settings"]["hard_cap_open_orders_budget"] = 1.0
    result = compare_evidence_packet_runtime_parity(prod, step1a)
    assert result["subtree_match"] is False
    assert any(d["path"].startswith("budget_settings") for d in result["differences"])


def test_strategy_settings_hash_mismatch_fails() -> None:
    prod, step1a = _prod_and_step1a()
    step1a["strategy_settings_hash"] = "different-hash"
    result = compare_evidence_packet_runtime_parity(prod, step1a)
    assert result["subtree_match"] is False
    assert any(d["path"] == "strategy_settings_hash" for d in result["differences"])


def test_schema_version_mismatch_fails() -> None:
    prod, step1a = _prod_and_step1a()
    step1a["schema_version"] = "evidence_packet_v2"
    result = compare_evidence_packet_runtime_parity(prod, step1a)
    assert result["subtree_match"] is False
    assert any(d["path"] == "schema_version" for d in result["differences"])


def test_as_of_date_mismatch_fails_not_normalized() -> None:
    # as_of_date is content — a difference must fail, proving it is not normalized.
    prod, step1a = _prod_and_step1a()
    step1a["active_anchor_registry"]["as_of_date"] = "2026-06-29"
    result = compare_evidence_packet_runtime_parity(prod, step1a)
    assert result["subtree_match"] is False
    assert any(d["path"] == "active_anchor_registry.as_of_date" for d in result["differences"])


# --- normalization boundaries -------------------------------------------------


def test_unknown_iso_datetime_in_runtime_subtree_fails_closed() -> None:
    prod, step1a = _prod_and_step1a()
    # An unexpected wall-clock timestamp appears in the registry (both sides, so
    # it is NOT a plain value difference) under a non-generated_at key.
    stamp = "2026-07-07T09:00:00+00:00"
    prod["active_anchor_registry"]["compiled_at"] = stamp
    step1a["active_anchor_registry"]["compiled_at"] = stamp
    result = compare_evidence_packet_runtime_parity(prod, step1a)
    assert result["subtree_match"] is False
    assert "active_anchor_registry.compiled_at" in result["unknown_runtime_timestamp_fields"]


def test_null_vs_absent_runtime_field_fails() -> None:
    prod, step1a = _prod_and_step1a()
    prod["active_anchor_registry"]["as_of_date"] = None
    del step1a["active_anchor_registry"]["as_of_date"]
    result = compare_evidence_packet_runtime_parity(prod, step1a)
    assert result["subtree_match"] is False
    assert any("as_of_date" in d["path"] for d in result["differences"])


def test_list_ordering_difference_in_runtime_subtree_fails() -> None:
    prod, step1a = _prod_and_step1a()
    anchors = step1a["active_anchor_registry"]["active_anchors"]
    step1a["active_anchor_registry"]["active_anchors"] = list(reversed(anchors))
    result = compare_evidence_packet_runtime_parity(prod, step1a)
    assert result["subtree_match"] is False


# --- report-only differences (non-blocking) -----------------------------------


def test_source_artifacts_path_difference_is_report_only() -> None:
    prod, step1a = _prod_and_step1a()
    step1a["source_artifacts"] = {"strategy_settings": "/other/path/strategy_settings.yaml"}
    result = compare_evidence_packet_runtime_parity(prod, step1a)
    assert result["subtree_match"] is True  # not blocking
    assert result["differences"] == []
    assert any(d["path"].startswith("source_artifacts") for d in result["report_only_differences"])


def test_last_good_summary_difference_is_report_only() -> None:
    prod, step1a = _prod_and_step1a()
    step1a["last_good_research_summary"] = {"available": True, "age_days": 3}
    result = compare_evidence_packet_runtime_parity(prod, step1a)
    assert result["subtree_match"] is True
    assert any(
        d["path"].startswith("last_good_research_summary") for d in result["report_only_differences"]
    )


def test_portfolio_snapshot_summary_difference_is_report_only() -> None:
    prod, step1a = _prod_and_step1a()
    step1a["portfolio_snapshot_summary"] = {"available": True, "positions": 5}
    result = compare_evidence_packet_runtime_parity(prod, step1a)
    assert result["subtree_match"] is True
    assert any(
        d["path"].startswith("portfolio_snapshot_summary")
        for d in result["report_only_differences"]
    )


def test_top_level_generated_at_difference_alone_is_not_a_mismatch() -> None:
    # Even without normalization symmetry, the top-level generated_at is
    # normalized in both, so it never appears in differences.
    prod = _packet(generated_at="2026-07-07T03:15:58+00:00")
    step1a = _packet(generated_at="2026-07-07T09:99:99+00:00", registry=_registry(generated_at=None))
    result = compare_evidence_packet_runtime_parity(prod, step1a)
    assert result["subtree_match"] is True
    assert not any(d["path"] == "generated_at" for d in result["differences"])
