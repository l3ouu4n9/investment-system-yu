"""B1a repository-owned Step 4 fixture-evidence invariants only."""

from __future__ import annotations

import json
import re
import stat
from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
FIXTURE_ROOT = REPOSITORY_ROOT / "tests/fixtures/step4_artifact_compatibility_v1"
EXPECTED_BUNDLES = frozenset(
    {
        "zero_action_keep_only",
        "cancel_only",
        "actionable_buy",
        "sell_only_structural",
        "blocked_data_gap",
    }
)
EXPECTED_FILENAMES = frozenset(
    {
        "template4_orders.txt",
        "order_state_export.txt",
        "exec_summary.txt",
        "fixture_metadata.json",
    }
)
EXPECTED_EVIDENCE_KINDS = {
    "zero_action_keep_only": "OBSERVED_CURRENT_FORMAT_EXAMPLE",
    "cancel_only": "OBSERVED_CURRENT_FORMAT_EXAMPLE",
    "actionable_buy": "OBSERVED_CURRENT_FORMAT_EXAMPLE",
    "sell_only_structural": "OBSERVED_STRUCTURAL_SELL_EXAMPLE",
    "blocked_data_gap": "OBSERVED_BLOCKED_VARIANT_EXAMPLE",
}
ALLOWED_EVIDENCE_CATEGORIES = frozenset(
    {
        "STRATEGY_C_PROMPT",
        "CURRENT_FORMAT_ARCHIVE_OBSERVATION",
        "RUNBOOK_OR_DESIGN_EVIDENCE",
        "VALIDATOR_TEST_EVIDENCE",
    }
)
AUTHORITY_KEYS = frozenset(
    {
        "runtime_valid",
        "canonical_publication_authorized",
        "manual_order_ready",
        "broker_ready",
        "sell_authorized",
    }
)
PROVENANCE_BANNED_PATTERNS = (
    r"(?i)artifacts/(?:archive|current|daily)",
    r"\b(?:19|20)\d{6}_\d{6}\b",
    r"\b[0-9a-fA-F]{7,40}\b",
    r"\b[0-9a-fA-F]{64}\b",
    r"(?i)\b(?:sha[-_]?256|commit|hash|lineage|operator)\b",
    r"(?i)(?:/home/|/tmp/|[A-Za-z]:\\\\)",
)
SYNTHETIC_DATA_BANNED_PATTERNS = (
    r"(?i)\baccount(?:[_ -]?(?:id|number))?\b",
    r"(?i)\bbalance\b",
    r"(?i)\bbroker\b",
    r"(?i)\boperator\b",
    r"(?i)artifacts/(?:archive|current|daily)",
    r"\b(?:19|20)\d{2}-\d{2}-\d{2}\b",
    r"\b(?:19|20)\d{6}_\d{6}\b",
    r"(?i)(?:/home/|/tmp/|[A-Za-z]:\\\\)",
)


def _metadata(bundle_name: str) -> dict[str, object]:
    return json.loads((FIXTURE_ROOT / bundle_name / "fixture_metadata.json").read_text(encoding="utf-8"))


def _artifact_text(bundle_name: str, filename: str) -> str:
    return (FIXTURE_ROOT / bundle_name / filename).read_text(encoding="utf-8")


def test_fixture_inventory_is_exact_and_regular() -> None:
    fixture_entries = list(FIXTURE_ROOT.iterdir())
    assert {entry.name for entry in fixture_entries} == EXPECTED_BUNDLES
    for bundle in fixture_entries:
        assert stat.S_ISDIR(bundle.lstat().st_mode)
        entries = list(bundle.iterdir())
        assert {entry.name for entry in entries} == EXPECTED_FILENAMES
        for entry in entries:
            assert stat.S_ISREG(entry.lstat().st_mode)


def test_metadata_is_bounded_synthetic_and_non_authoritative() -> None:
    for bundle_name in EXPECTED_BUNDLES:
        metadata = _metadata(bundle_name)
        expected_keys = {
            "fixture_schema",
            "fixture_name",
            "evidence_kind",
            "evidence_categories",
            "synthetic_data",
            "authority",
            "policy_status",
        }
        if bundle_name == "sell_only_structural":
            expected_keys.add("sell_grammar")
        if bundle_name == "blocked_data_gap":
            expected_keys.add("blocked_reason_grammar")
        assert set(metadata) == expected_keys
        assert metadata["fixture_schema"] == "step4_fixture_evidence_v1"
        assert metadata["fixture_name"] == bundle_name
        assert metadata["evidence_kind"] == EXPECTED_EVIDENCE_KINDS[bundle_name]
        assert metadata["synthetic_data"] is True

        categories = metadata["evidence_categories"]
        assert isinstance(categories, list) and categories
        assert len(categories) == len(set(categories))
        assert set(categories) <= ALLOWED_EVIDENCE_CATEGORIES

        authority = metadata["authority"]
        assert isinstance(authority, dict)
        assert set(authority) == AUTHORITY_KEYS
        assert all(value is False for value in authority.values())
        assert metadata["policy_status"] == {
            "canonical_validity": "NOT_ASSESSED",
            "b2_policy": "UNRESOLVED",
        }


def test_fixture_specific_policy_boundaries_are_explicit() -> None:
    sell_metadata = _metadata("sell_only_structural")
    assert sell_metadata["authority"]["sell_authorized"] is False  # type: ignore[index]
    assert sell_metadata["sell_grammar"] == "UNRESOLVED"

    blocked_metadata = _metadata("blocked_data_gap")
    assert blocked_metadata["blocked_reason_grammar"] == "UNRESOLVED"
    for filename in ("template4_orders.txt", "order_state_export.txt", "exec_summary.txt"):
        text = _artifact_text("blocked_data_gap", filename)
        assert "DATA_GAP" in text
        assert "COMPILER_BLOCKED" in text


def test_metadata_has_no_mutable_or_identifying_provenance() -> None:
    for bundle_name in EXPECTED_BUNDLES:
        serialized = json.dumps(_metadata(bundle_name), sort_keys=True)
        for pattern in PROVENANCE_BANNED_PATTERNS:
            assert re.search(pattern, serialized) is None, (bundle_name, pattern)


def test_fixture_artifacts_are_synthetic_and_minimized() -> None:
    all_text = ""
    for bundle_name in EXPECTED_BUNDLES:
        for filename in ("template4_orders.txt", "order_state_export.txt", "exec_summary.txt"):
            all_text += _artifact_text(bundle_name, filename)
    for pattern in SYNTHETIC_DATA_BANNED_PATTERNS:
        assert re.search(pattern, all_text) is None, pattern
    assert re.search(r"\b\d+\.\d+\b", all_text) is None

    for bundle_name in {"actionable_buy", "cancel_only", "sell_only_structural"}:
        text = _artifact_text(bundle_name, "template4_orders.txt")
        assert "ticker=FIXTURE" in text
        assert "FIXTURE_DATE" in text
        assert "FIXTURE_PRICE" in text
        assert "FIXTURE_SHARES" in text
        note_values = re.findall(r"\bnote=([^|\n]+)", text)
        assert all(value.strip() == "FIXTURE_NOTE" for value in note_values)
    for bundle_name in {"actionable_buy", "cancel_only"}:
        state = _artifact_text(bundle_name, "order_state_export.txt")
        reason_values = re.findall(r"\breason=([^|\n]+)", state)
        assert reason_values == ["FIXTURE_REASON"]


def test_fixtures_record_structure_only_not_b2_policy() -> None:
    normal_bundles = EXPECTED_BUNDLES - {"blocked_data_gap"}
    for bundle_name in normal_bundles:
        template = _artifact_text(bundle_name, "template4_orders.txt").splitlines()
        state = _artifact_text(bundle_name, "order_state_export.txt").splitlines()
        summary = _artifact_text(bundle_name, "exec_summary.txt").splitlines()
        assert template[0] == "TEMPLATE4_ORDERS"
        assert template.index("SELL_ORDERS") < template.index("BUY_ORDERS")
        assert state[0] == "ORDER_STATE_EXPORT"
        assert "(2a) existing_buy_open_orders_summary" in state
        assert "(2b) sell_open_orders" in state
        assert "DEFERRED_NOT_YET_LIVE" in state
        assert summary[0] == "TEMPLATE5_EXEC_SUMMARY"


def test_fixtures_are_not_runtime_inputs_or_entrypoint_options() -> None:
    needle = "step4_artifact_compatibility_v1"
    search_files = [REPOSITORY_ROOT / "pyproject.toml", REPOSITORY_ROOT / "requirements.txt"]
    search_files.extend((REPOSITORY_ROOT / "src").rglob("*.py"))
    for directory, suffixes in (
        (REPOSITORY_ROOT / "inputs", {".json", ".txt", ".yaml", ".yml"}),
        (REPOSITORY_ROOT / "prompts", {".txt"}),
        (REPOSITORY_ROOT / "schemas", {".json"}),
    ):
        search_files.extend(
            path for path in directory.rglob("*") if path.is_file() and path.suffix in suffixes
        )
    for path in search_files:
        assert needle not in path.read_text(encoding="utf-8")


def test_b1a_documentation_defers_scanner_work() -> None:
    documentation = (REPOSITORY_ROOT / "docs/step4_artifact_compatibility_b1.md").read_text(encoding="utf-8")
    assert "B1b scanner work is deferred." in documentation
    for forbidden in ("--root", "--strict", "scan_roots", "compatibility report"):
        assert forbidden not in documentation
