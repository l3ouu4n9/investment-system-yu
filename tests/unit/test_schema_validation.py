from copy import deepcopy
import hashlib
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
from investment_orchestrator.observability.ltetf_evidence_contract_common import (
    DOMAIN_SEPARATORS,
    NORMALIZATION_PROFILE,
    SCHEMA_FILENAME_BY_VERSION,
    SCHEMA_IDENTITY_SHA256_BY_VERSION,
    parse_strict_json_bytes,
)
from investment_orchestrator.observability.ltetf_target_architecture_prerequisite_catalog import CATALOG
from investment_orchestrator.observability import weekly_shadow_01_contracts as ws01a
from investment_orchestrator.state.blocked_run_summary import (
    blocked_run_summary_result_to_dict,
    build_blocked_run_summary,
)


WS01A_SCHEMA_FILENAMES = (
    "weekly_shadow_01_analyst_input.schema.json",
    "weekly_shadow_01_analyst_response.schema.json",
    "weekly_shadow_01_response_capture.schema.json",
    "weekly_shadow_01_response_validation.schema.json",
    "weekly_shadow_01_analyst_report.schema.json",
    "weekly_shadow_01_run_summary.schema.json",
)


LTETF_02A1_SCHEMA_FILENAMES = (
    "ltetf_source_authority_policy.schema.json",
    "ltetf_authorized_source_registry.schema.json",
    "ltetf_field_freshness_policy.schema.json",
    "ltetf_operator_policy_acceptance.schema.json",
    "ltetf_generic_evidence_manifest.schema.json",
    "ltetf_trusted_evaluation_epoch.schema.json",
    "ltetf_structured_market_metrics.schema.json",
    "ltetf_structured_scheduled_events.schema.json",
    "ltetf_prior_thesis_continuity.schema.json",
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


def test_every_repository_schema_is_discovered_parsed_and_draft_2020_12_valid() -> None:
    schema_paths = tuple(sorted((repo_root() / "schemas").glob("*.schema.json")))
    assert schema_paths
    assert len(schema_paths) == len({path.name for path in schema_paths})
    for schema_path in schema_paths:
        schema = json.loads(schema_path.read_text(encoding="utf-8"))
        assert schema["$schema"] == "https://json-schema.org/draft/2020-12/schema", schema_path.name
        Draft202012Validator.check_schema(schema)


def _assert_all_domain_objects_closed(node: object, *, location: str = "$") -> None:
    if isinstance(node, dict):
        if node.get("type") == "object":
            assert node.get("additionalProperties") is False, location
        for key, value in node.items():
            _assert_all_domain_objects_closed(value, location=f"{location}/{key}")
    elif isinstance(node, list):
        for index, value in enumerate(node):
            _assert_all_domain_objects_closed(value, location=f"{location}/{index}")


def test_ltetf_02a1_schema_set_is_exact_closed_bounded_and_identity_bound() -> None:
    assert tuple(path.rsplit("/", 1)[-1] for path in SCHEMA_FILENAME_BY_VERSION.values()) == (
        LTETF_02A1_SCHEMA_FILENAMES
    )
    assert tuple(SCHEMA_FILENAME_BY_VERSION) == (
        "ltetf_source_authority_policy_v1",
        "ltetf_authorized_source_registry_v1",
        "ltetf_field_freshness_policy_v1",
        "ltetf_operator_policy_acceptance_v1",
        "ltetf_generic_evidence_manifest_v1",
        "ltetf_trusted_evaluation_epoch_v1",
        "ltetf_structured_market_metrics_v1",
        "ltetf_structured_scheduled_events_v1",
        "ltetf_prior_thesis_continuity_v1",
    )
    identities: list[str] = []
    for schema_version, schema_relative_path in SCHEMA_FILENAME_BY_VERSION.items():
        schema_path = repo_root() / schema_relative_path
        assert schema_path.name in LTETF_02A1_SCHEMA_FILENAMES
        schema = parse_strict_json_bytes(schema_path.read_bytes())
        assert isinstance(schema, dict)
        assert schema["$schema"] == "https://json-schema.org/draft/2020-12/schema"
        assert schema["$id"] == f"https://investment-system.local/{schema_relative_path}"
        assert schema["additionalProperties"] is False
        Draft202012Validator.check_schema(schema)
        _assert_all_domain_objects_closed(schema)
        identity_payload = {
            "schema_version": schema_version,
            "schema_path": schema_relative_path,
            "schema_id": schema["$id"],
            "normalization_profile_identity_sha256": NORMALIZATION_PROFILE.identity_sha256,
            "schema": schema,
        }
        canonical = json.dumps(
            identity_payload,
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
        independent_identity = hashlib.sha256(
            DOMAIN_SEPARATORS["schema_identity"] + canonical
        ).hexdigest()
        assert independent_identity == SCHEMA_IDENTITY_SHA256_BY_VERSION[schema_version]
        identities.append(independent_identity)
    assert len(identities) == len(set(identities)) == 9


def test_ws01a_schema_set_is_exact_closed_bounded_and_identity_bound() -> None:
    assert tuple(path.rsplit("/", 1)[-1] for path in ws01a.SCHEMA_FILENAME_BY_VERSION.values()) == (
        WS01A_SCHEMA_FILENAMES
    )
    assert tuple(ws01a.SCHEMA_FILENAME_BY_VERSION) == (
        "weekly_shadow_01_analyst_input_v1",
        "weekly_shadow_01_analyst_response_v1",
        "weekly_shadow_01_response_capture_v1",
        "weekly_shadow_01_response_validation_v1",
        "weekly_shadow_01_analyst_report_v1",
        "weekly_shadow_01_run_summary_v1",
    )
    identities: list[str] = []
    for schema_version, schema_relative_path in ws01a.SCHEMA_FILENAME_BY_VERSION.items():
        schema_path = repo_root() / schema_relative_path
        assert schema_path.name in WS01A_SCHEMA_FILENAMES
        schema = json.loads(schema_path.read_bytes().decode("utf-8"))
        assert schema["$schema"] == "https://json-schema.org/draft/2020-12/schema"
        assert schema["$id"] == f"https://investment-system.local/{schema_relative_path}"
        assert schema["additionalProperties"] is False
        Draft202012Validator.check_schema(schema)
        _assert_all_domain_objects_closed(schema)
        identity_payload = {
            "schema_version": schema_version,
            "schema_path": schema_relative_path,
            "schema_id": schema["$id"],
            "schema": schema,
        }
        independent_identity = hashlib.sha256(
            ws01a.DOMAIN_SEPARATORS["schema_identity"] + json.dumps(
                identity_payload,
                ensure_ascii=True,
                sort_keys=True,
                separators=(",", ":"),
                allow_nan=False,
            ).encode("utf-8")
        ).hexdigest()
        assert independent_identity == ws01a.SCHEMA_IDENTITY_SHA256_BY_VERSION[schema_version]
        identities.append(independent_identity)
    assert len(identities) == len(set(identities)) == 6


def test_ltetf_02a1_schemas_bind_exact_constants_and_observer_safety_bounds() -> None:
    source_policy = parse_strict_json_bytes(
        (repo_root() / "schemas/ltetf_source_authority_policy.schema.json").read_bytes()
    )
    assert source_policy["properties"]["authority_effect"]["const"] == "none"
    rules = source_policy["$defs"]["policy_content"]["properties"]["rules"]
    assert rules["minItems"] == rules["maxItems"] == 4
    assert rules["items"] is False

    registry = parse_strict_json_bytes(
        (repo_root() / "schemas/ltetf_authorized_source_registry.schema.json").read_bytes()
    )
    assert registry["$defs"]["policy_content"]["properties"]["sources"]["maxItems"] == 4096
    assert tuple(
        branch["$ref"] for branch in registry["$defs"]["source_locator"]["oneOf"]
    ) == (
        "#/$defs/repository_path_locator",
        "#/$defs/https_origin_locator",
        "#/$defs/opaque_source_id_locator",
    )

    manifest = parse_strict_json_bytes(
        (repo_root() / "schemas/ltetf_generic_evidence_manifest.schema.json").read_bytes()
    )
    assert manifest["properties"]["source_bindings"]["minItems"] == 1
    assert manifest["properties"]["source_bindings"]["maxItems"] == 16
    assert manifest["properties"]["authority_effect"]["const"] == "none"

    for filename in (
        "ltetf_structured_market_metrics.schema.json",
        "ltetf_structured_scheduled_events.schema.json",
    ):
        schema = parse_strict_json_bytes((repo_root() / "schemas" / filename).read_bytes())
        assert schema["properties"]["records"]["minItems"] == 0
        assert schema["properties"]["records"]["maxItems"] == 4096


def test_ltetf_02a1_policy_payloads_cannot_self_accept_and_manifest_has_no_result_fields() -> None:
    prohibited_policy_fields = {
        "accepted",
        "acceptance_state",
        "is_accepted",
        "activation",
        "activation_state",
        "operator_approval",
    }
    for filename in (
        "ltetf_source_authority_policy.schema.json",
        "ltetf_authorized_source_registry.schema.json",
        "ltetf_field_freshness_policy.schema.json",
    ):
        schema = parse_strict_json_bytes((repo_root() / "schemas" / filename).read_bytes())
        root_fields = set(schema["properties"])
        content_fields = set(schema["$defs"]["policy_content"]["properties"])
        assert not prohibited_policy_fields & (root_fields | content_fields)
        assert schema["properties"]["policy_content"]["$ref"] == "#/$defs/policy_content"

    acceptance = parse_strict_json_bytes(
        (repo_root() / "schemas/ltetf_operator_policy_acceptance.schema.json").read_bytes()
    )
    assert "accepted_policy_type" in acceptance["required"]
    assert "acceptance_artifact_identity_sha256" in acceptance["required"]
    assert acceptance["properties"]["authority_effect"]["const"] == "none"

    manifest = parse_strict_json_bytes(
        (repo_root() / "schemas/ltetf_generic_evidence_manifest.schema.json").read_bytes()
    )
    manifest_fields = set(manifest["properties"])
    assert not {
        "validation_status",
        "freshness_status",
        "sufficiency",
        "relevance",
        "actionability",
        "permission",
        "order_readiness",
    } & manifest_fields
    assert {
        "evidence_subject",
        "subject_identity_sha256",
        "source_bindings",
        "content_binding",
        "producer_binding",
        "acquired_at_utc",
        "policy_bindings",
        "ltetf_02a_catalog_identity_sha256",
        "predecessor_binding",
    } <= manifest_fields
