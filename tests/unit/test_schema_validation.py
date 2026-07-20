from copy import deepcopy
import json

from jsonschema import Draft202012Validator
import pytest

from investment_orchestrator.common.paths import repo_root
from investment_orchestrator.common.schema_validation import (
    ArtifactSchemaError,
    validate_artifact_schema,
)
from investment_orchestrator.observability.ltetf_target_architecture_gap_report import (
    ObserverIntegrityError,
    build_gap_report,
    report_content_identity_sha256,
    validate_gap_report_record,
)
from investment_orchestrator.observability.ltetf_target_architecture_prerequisite_catalog import CATALOG
from investment_orchestrator.state.blocked_run_summary import (
    blocked_run_summary_result_to_dict,
    build_blocked_run_summary,
)


def test_validate_artifact_schema_accepts_run_context_payload() -> None:
    payload = {
        "schema_version": "1.0",
        "pipeline": "weekly",
        "as_of_date": "2026-04-18",
        "run_timestamp_et": "2026-04-18 20:30 ET",
        "run_mode": "normal",
        "blocking_issue": None,
        "degraded_steps": [],
        "warnings": [],
        "step_summary": [],
        "has_live_order": False,
    }

    validate_artifact_schema(payload, schema_name="run_context.schema.json")


def test_validate_artifact_schema_rejects_missing_required_field() -> None:
    payload = {
        "schema_version": "1.0",
        "as_of_date": "2026-04-18",
        "run_timestamp_et": "2026-04-18 20:30 ET",
        "run_mode": "normal",
        "blocking_issue": None,
        "degraded_steps": [],
        "warnings": [],
        "step_summary": [],
    }

    with pytest.raises(ArtifactSchemaError) as exc_info:
        validate_artifact_schema(payload, schema_name="run_context.schema.json")

    assert "run_context.schema.json" in str(exc_info.value)
    assert "pipeline" in str(exc_info.value)


def test_validate_artifact_schema_accepts_blocked_run_summary_payload() -> None:
    payload = blocked_run_summary_result_to_dict(
        build_blocked_run_summary(
            step1_decision={
                "state": "STRICT_FRESH",
                "research_availability": "strict_fresh",
                "allowed_actions": ["HOLD", "NO_TRADE", "NEW_BUY", "ORDER_COMPILATION"],
                "blocked_actions": [],
                "manual_review_required": False,
                "blocker_reasons": [],
            },
            step2_block=None,
            step3_block=None,
            step4_block=None,
        )
    )

    validate_artifact_schema(payload, schema_name="blocked_run_summary.schema.json")


def test_validate_artifact_schema_accepts_ltetf_gap_report_payload() -> None:
    validate_artifact_schema(
        build_gap_report(repo_root()),
        schema_name="ltetf_target_architecture_gap_report.schema.json",
    )


def _reseal(report: dict[str, object]) -> dict[str, object]:
    report["content_identity_sha256"] = report_content_identity_sha256(report)
    return report


def test_ltetf_report_schema_is_draft_2020_12_closed_and_uses_exact_dimension_shape() -> None:
    schema_path = repo_root() / "schemas" / "ltetf_target_architecture_gap_report.schema.json"
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(schema)
    assert schema["$schema"] == "https://json-schema.org/draft/2020-12/schema"
    assert schema["additionalProperties"] is False
    dimensions = schema["properties"]["dimensions"]
    assert dimensions["minItems"] == dimensions["maxItems"] == 7
    assert dimensions["items"] is False
    assert len(dimensions["prefixItems"]) == 7
    assert schema["$defs"]["check"]["additionalProperties"] is False
    assert schema["$defs"]["evidence"]["additionalProperties"] is False


def test_raw_ltetf_schema_rejects_exact_dimension_and_check_order_violations() -> None:
    schema = json.loads(
        (repo_root() / "schemas" / "ltetf_target_architecture_gap_report.schema.json").read_text(encoding="utf-8")
    )
    validator = Draft202012Validator(schema)
    report = build_gap_report(repo_root())
    cases: list[dict[str, object]] = []

    substituted = deepcopy(report)
    substituted["dimensions"][0]["checks"][0]["check_id"] = "unexpected_check"
    cases.append(substituted)

    swapped = deepcopy(report)
    swapped["dimensions"][0]["checks"][0], swapped["dimensions"][0]["checks"][1] = (
        swapped["dimensions"][0]["checks"][1],
        swapped["dimensions"][0]["checks"][0],
    )
    cases.append(swapped)

    missing = deepcopy(report)
    missing["dimensions"][0]["checks"].pop()
    missing["dimensions"][0]["check_count"] = 10
    cases.append(missing)

    extra = deepcopy(report)
    extra["dimensions"][0]["checks"].append(deepcopy(extra["dimensions"][0]["checks"][-1]))
    extra["dimensions"][0]["check_count"] = 12
    cases.append(extra)

    duplicate = deepcopy(report)
    duplicate["dimensions"][0]["checks"][1]["check_id"] = duplicate["dimensions"][0]["checks"][0]["check_id"]
    cases.append(duplicate)

    wrong_dimension_id = deepcopy(report)
    wrong_dimension_id["dimensions"][0]["dimension_id"] = "evidence_and_grounding"
    cases.append(wrong_dimension_id)

    wrong_dimension_order = deepcopy(report)
    wrong_dimension_order["dimensions"][0], wrong_dimension_order["dimensions"][1] = (
        wrong_dimension_order["dimensions"][1],
        wrong_dimension_order["dimensions"][0],
    )
    cases.append(wrong_dimension_order)

    # This is globally one of the five permitted ownership pairs, but it is
    # not the frozen pair for investment_horizon_policy.  Raw schema validation
    # must bind ownership at the exact ordered check position.
    wrong_ownership = deepcopy(report)
    wrong_ownership["dimensions"][0]["checks"][0].update(
        {
            "contract_owner": "deterministic_code",
            "runtime_actor": "deterministic_code",
        }
    )
    cases.append(wrong_ownership)

    swapped_ownership = deepcopy(report)
    left = swapped_ownership["dimensions"][0]["checks"][0]
    right = swapped_ownership["dimensions"][1]["checks"][7]
    for field in ("contract_owner", "runtime_actor", "authority_effect"):
        left[field], right[field] = right[field], left[field]
    cases.append(swapped_ownership)

    wrong_authority = deepcopy(report)
    wrong_authority["dimensions"][0]["checks"][0]["authority_effect"] = "authorization"
    cases.append(wrong_authority)

    total_80 = deepcopy(report)
    total_80["summary_counts"]["total_checks"] = 80
    cases.append(total_80)

    total_82 = deepcopy(report)
    total_82["summary_counts"]["total_checks"] = 82
    cases.append(total_82)

    assert all(tuple(validator.iter_errors(case)) for case in cases)


def test_ltetf_report_producer_rejects_reordered_duplicate_or_missing_checks_and_summary_mismatch() -> None:
    report = build_gap_report(repo_root())
    bad_order = deepcopy(report)
    bad_order["dimensions"].reverse()
    with pytest.raises(ObserverIntegrityError, match="OBSERVER_SCHEMA_INVALID|REPORT_RECORD_INVALID"):
        validate_gap_report_record(_reseal(bad_order), root=repo_root())

    duplicate = deepcopy(report)
    first_checks = duplicate["dimensions"][0]["checks"]
    first_checks[1]["check_id"] = first_checks[0]["check_id"]
    with pytest.raises(ObserverIntegrityError, match="OBSERVER_SCHEMA_INVALID|REPORT_RECORD_INVALID"):
        validate_gap_report_record(_reseal(duplicate), root=repo_root())

    missing = deepcopy(report)
    missing["dimensions"][0]["checks"].pop()
    missing["dimensions"][0]["check_count"] = 10
    with pytest.raises(ObserverIntegrityError, match="OBSERVER_SCHEMA_INVALID|REPORT_RECORD_INVALID"):
        validate_gap_report_record(_reseal(missing), root=repo_root())

    counts = deepcopy(report)
    counts["summary_counts"]["PROVEN_PRESENT"] += 1
    with pytest.raises(ObserverIntegrityError, match="REPORT_RECORD_INVALID"):
        validate_gap_report_record(_reseal(counts), root=repo_root())

    counts = deepcopy(report)
    counts["summary_counts"]["PROVEN_PRESENT"] -= 1
    with pytest.raises(ObserverIntegrityError, match="REPORT_RECORD_INVALID"):
        validate_gap_report_record(_reseal(counts), root=repo_root())

    raw_schema = json.loads(
        (repo_root() / "schemas" / "ltetf_target_architecture_gap_report.schema.json").read_text(encoding="utf-8")
    )
    assert not tuple(Draft202012Validator(raw_schema).iter_errors(counts))


@pytest.mark.parametrize(
    "path",
    ["a//b", "a/", "a/./b", "a/../b", "../a", "/a", "C:/a", "a\\b", "a\x00b"],
)
def test_ltetf_report_rejects_non_normalized_repository_relative_paths(path: str) -> None:
    report = deepcopy(build_gap_report(repo_root()))
    record = next(item for item in report["evidence"] if item["repository_relative_path"] is not None)
    record["repository_relative_path"] = path
    with pytest.raises(ObserverIntegrityError, match="OBSERVER_SCHEMA_INVALID|REPORT_RECORD_INVALID"):
        validate_gap_report_record(_reseal(report), root=repo_root())


def test_ltetf_report_binds_catalog_identity_ownership_and_exact_order() -> None:
    report = deepcopy(build_gap_report(repo_root()))
    report["prerequisite_catalog_identity_sha256"] = "0" * 64
    with pytest.raises(ObserverIntegrityError, match="OBSERVER_CATALOG_INVALID"):
        validate_gap_report_record(_reseal(report), root=repo_root())

    old_catalog = deepcopy(build_gap_report(repo_root()))
    old_catalog["prerequisite_catalog_identity_sha256"] = (
        "bc134dfed930716f602213dea11c31b02df89f6d1aeff849e270c289514565e4"
    )
    with pytest.raises(ObserverIntegrityError, match="OBSERVER_CATALOG_INVALID"):
        validate_gap_report_record(_reseal(old_catalog), root=repo_root())

    ownership = deepcopy(build_gap_report(repo_root()))
    ownership["dimensions"][0]["checks"][0]["contract_owner"] = "deterministic_code"
    with pytest.raises(ObserverIntegrityError, match="OBSERVER_SCHEMA_INVALID|REPORT_RECORD_INVALID"):
        validate_gap_report_record(_reseal(ownership), root=repo_root())

    report = build_gap_report(repo_root())
    assert tuple(
        check["check_id"]
        for dimension in report["dimensions"]
        for check in dimension["checks"]
    ) == tuple(check.check_id for check in CATALOG)
