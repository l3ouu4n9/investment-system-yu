"""Permanent tests for the isolated LTETF-01 proof observer.

These tests deliberately exercise evidence and predicate facts separately from
the status selector.  Controlled repository fixtures exercise source and
artifact collection only; they never execute a target source or validator.
"""

from __future__ import annotations

from dataclasses import replace
import inspect
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from investment_orchestrator.common.paths import repo_root
import investment_orchestrator.observability.ltetf_target_architecture_gap_report as gap
from investment_orchestrator.observability.ltetf_target_architecture_prerequisite_catalog import (
    CATALOG,
    CatalogIntegrityError,
    PROOF_PREDICATES,
    PROOF_PROFILES,
    ReadinessStatus,
)


def _write(root: Path, relative_path: str, content: str | bytes) -> Path:
    path = root / relative_path
    path.parent.mkdir(parents=True, exist_ok=True)
    if type(content) is bytes:
        path.write_bytes(content)
    else:
        path.write_text(content, encoding="utf-8")
    return path


def _minimal_observer_repository(tmp_path: Path) -> Path:
    """Create a repository with only the allowed observer CLI consumer."""
    root = tmp_path / "repository"
    _write(root, "pyproject.toml", "[project]\nname = 'observer-fixture'\n")
    _write(root, "src/investment_orchestrator/__init__.py", "")
    _write(root, "src/investment_orchestrator/observability/__init__.py", "")
    _write(
        root,
        "src/investment_orchestrator/observability/ltetf_target_architecture_prerequisite_catalog.py",
        "",
    )
    _write(
        root,
        "src/investment_orchestrator/observability/ltetf_target_architecture_gap_report.py",
        "",
    )
    _write(
        root,
        "src/investment_orchestrator/cli/observe_ltetf_target_architecture_gaps.py",
        "from investment_orchestrator.observability import ltetf_target_architecture_gap_report\n",
    )
    _write(
        root,
        "schemas/ltetf_target_architecture_gap_report.schema.json",
        (repo_root() / "schemas/ltetf_target_architecture_gap_report.schema.json").read_bytes(),
    )
    return root


def _check(report: dict[str, object], check_id: str) -> dict[str, object]:
    return next(
        item
        for dimension in report["dimensions"]  # type: ignore[index]
        for item in dimension["checks"]  # type: ignore[index]
        if item["check_id"] == check_id
    )


def _policy_schema() -> dict[str, object]:
    scalar = {"type": "string", "minLength": 1, "maxLength": 128}
    schema: dict[str, object] = {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "type": "object",
        "additionalProperties": False,
        "required": [
            "schema_version",
            "schema_path",
            "acceptance_state",
            "policy_identity_sha256",
            "effective_version",
            "activation_marker",
            "permitted_consumer_set",
            "policies",
        ],
        "properties": {
            "schema_version": {"const": "ltetf_operator_mandate_policy_v1"},
            "schema_path": {"type": "string", "minLength": 1, "maxLength": 512},
            "acceptance_state": {"enum": ["candidate", "unaccepted", "accepted"]},
            "policy_identity_sha256": {"type": "string", "minLength": 64, "maxLength": 64},
            "effective_version": scalar,
            "activation_marker": {"enum": ["active", "inactive", "contract_readiness_only"]},
            "permitted_consumer_set": {
                "type": "array",
                "maxItems": 64,
                "items": {"type": "string", "minLength": 1, "maxLength": 512},
            },
            "policies": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "investment_horizon_policy": {
                        "type": "object",
                        "additionalProperties": False,
                        "required": ["horizon"],
                        "properties": {
                            "horizon": {"type": "string", "minLength": 1, "maxLength": 64}
                        },
                    }
                },
            },
        },
    }
    schema["x-contract-identity-sha256"] = gap._sha256_identity(
        gap.SCHEMA_IDENTITY_DOMAIN,
        schema,
        maximum=gap.MAX_EVIDENCE_CANONICAL_BYTES,
    )
    return schema


def _target_contract_source(
    schema: dict[str, object],
    *,
    validator_symbol: str,
    producer_symbol: str,
) -> str:
    """Create static target source evidence without any executable proof data."""
    required = list(schema["required"])
    declaration = {
        "schema_version": schema["properties"]["schema_version"]["const"],
        "schema_identity_sha256": schema["x-contract-identity-sha256"],
        "required_fields": required,
        "prohibited_authority_fields": sorted(gap._PROHIBITED_AUTHORITY_FIELDS),
        "validator_symbol": validator_symbol,
        "producer_symbol": producer_symbol,
        "target_invariants": ["closed_schema", "exact_required_fields"],
        "input_type": "dict",
        "output_type": "dict",
        "failure_codes": ["INVALID_TARGET_INPUT"],
        "legacy_semantics": False,
    }
    return (
        f"LTETF_TARGET_CONTRACT = {declaration!r}\n"
        f"def {validator_symbol}(value: dict) -> dict:\n"
        "    ...\n"
        f"def {producer_symbol}() -> dict:\n"
        "    ...\n"
    )


def _test_contract_source(
    schema: dict[str, object],
    *,
    validator_symbol: str,
    producer_module: str,
) -> str:
    """Supporting-test source only; it intentionally invokes no target code."""
    del schema, validator_symbol, producer_module
    return "def test_target_contract_supporting_evidence_only():\n    assert True\n"


def _policy_payload(
    *,
    state: str = "candidate",
    activation: str = "inactive",
    version: str = "v1",
    consumers: list[str] | None = None,
) -> dict[str, object]:
    payload: dict[str, object] = {
        "schema_version": "ltetf_operator_mandate_policy_v1",
        "schema_path": "schemas/operator_mandate_candidate.schema.json",
        "acceptance_state": state,
        "effective_version": version,
        "activation_marker": activation,
        # This controlled producer does not read the candidate artifact; its
        # declaration must therefore equal the empty actual-reader inventory.
        "permitted_consumer_set": [] if consumers is None else consumers,
        "policies": {"investment_horizon_policy": {"horizon": "long"}},
    }
    payload["policy_identity_sha256"] = gap._policy_identity(payload)
    return payload


def _install_policy_support(
    root: Path,
    payload: dict[str, object],
    *,
    schema: dict[str, object] | None = None,
) -> None:
    schema = _policy_schema() if schema is None else schema
    _write(root, "schemas/operator_mandate_candidate.schema.json", json.dumps(schema))
    _write(
        root,
        "src/investment_orchestrator/target_architecture/operator_mandate_policy.py",
        _target_contract_source(
            schema,
            validator_symbol="validate_operator_mandate_policy",
            producer_symbol="load_operator_mandate_policy",
        ),
    )
    _write(
        root,
        "tests/unit/test_ltetf_operator_mandate_policy.py",
        _test_contract_source(
            schema,
            validator_symbol="validate_operator_mandate_policy",
            producer_module="investment_orchestrator.target_architecture.operator_mandate_policy",
        ),
    )
    _write(root, "inputs/current/ltetf_operator_mandate.json", json.dumps(payload))


_RUNTIME_FACETS = (
    "holdings",
    "cash",
    "tax_lots",
    "open_orders",
    "account_metadata",
    "manual_orders",
)


def _runtime_schema() -> dict[str, object]:
    properties: dict[str, object] = {
        "schema_version": {"const": "ltetf_portfolio_state_v1"},
        "current_slot": {"const": "ltetf_portfolio_state"},
        "is_fixture": {"type": "boolean"},
        "is_archive": {"type": "boolean"},
        "content_identity_sha256": {"type": "string", "minLength": 64, "maxLength": 64},
    }
    properties.update(
        {
            facet: {
                "type": "array",
                "maxItems": 32,
                "items": {"type": "string", "maxLength": 128},
            }
            for facet in _RUNTIME_FACETS
        }
    )
    schema: dict[str, object] = {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "type": "object",
        "additionalProperties": False,
        "required": [
            "schema_version",
            "current_slot",
            "is_fixture",
            "is_archive",
            "content_identity_sha256",
            *_RUNTIME_FACETS,
        ],
        "properties": properties,
    }
    schema["x-contract-identity-sha256"] = gap._sha256_identity(
        gap.SCHEMA_IDENTITY_DOMAIN,
        schema,
        maximum=gap.MAX_EVIDENCE_CANONICAL_BYTES,
    )
    return schema


def _runtime_payload(*, complete: bool = True, suffix: str = "a") -> dict[str, object]:
    payload: dict[str, object] = {
        "schema_version": "ltetf_portfolio_state_v1",
        "current_slot": "ltetf_portfolio_state",
        "is_fixture": False,
        "is_archive": False,
    }
    for facet in _RUNTIME_FACETS:
        payload[facet] = [f"{facet}-{suffix}"] if complete else []
    payload["content_identity_sha256"] = gap._runtime_identity(payload)
    return payload


def _install_runtime_support(root: Path) -> None:
    schema = _runtime_schema()
    _write(root, "schemas/ltetf_portfolio_state.schema.json", json.dumps(schema))
    _write(
        root,
        "src/investment_orchestrator/target_architecture/portfolio_state_producer.py",
        _target_contract_source(
            schema,
            validator_symbol="validate_portfolio_state",
            producer_symbol="produce_portfolio_state",
        ),
    )
    _write(
        root,
        "tests/unit/test_ltetf_portfolio_state_contract.py",
        _test_contract_source(
            schema,
            validator_symbol="validate_portfolio_state",
            producer_module="investment_orchestrator.target_architecture.portfolio_state_producer",
        ),
    )


def _reidentity_schema(schema: dict[str, object]) -> dict[str, object]:
    sealed = dict(schema)
    sealed.pop("x-contract-identity-sha256", None)
    sealed["x-contract-identity-sha256"] = gap._sha256_identity(
        gap.SCHEMA_IDENTITY_DOMAIN,
        sealed,
        maximum=gap.MAX_EVIDENCE_CANONICAL_BYTES,
    )
    return sealed


def _contract_schema(check_id: str) -> dict[str, object]:
    schema: dict[str, object] = {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "type": "object",
        "additionalProperties": False,
        "required": ["schema_version", "value"],
        "properties": {
            "schema_version": {"const": f"ltetf_{check_id}_v1"},
            "value": {"type": "string", "minLength": 1, "maxLength": 64},
        },
    }
    return _reidentity_schema(schema)


def _install_contract_support(
    root: Path,
    check_id: str,
    *,
    source: bool = True,
    test: bool = True,
) -> dict[str, object]:
    schema = _contract_schema(check_id)
    _write(
        root,
        f"schemas/target_architecture/{check_id}.schema.json",
        json.dumps(schema),
    )
    if source:
        _write(
            root,
            f"src/investment_orchestrator/target_architecture/{check_id}.py",
            _target_contract_source(
                schema,
                validator_symbol="validate_contract",
                producer_symbol="produce",
            ),
        )
    if test:
        _write(
            root,
            f"tests/unit/test_ltetf_target_architecture_{check_id}.py",
            _test_contract_source(
                schema,
                validator_symbol="validate_contract",
                producer_module=f"investment_orchestrator.target_architecture.{check_id}",
            ),
        )
    return schema


def _complete_facts() -> gap.AdapterFacts:
    field_names = set(gap.AdapterFacts.__dataclass_fields__)
    values = {
        field_name: True
        for field_name in set(gap._PREDICATE_FACT_FIELDS.values())
        if field_name in field_names
    }
    return gap.AdapterFacts(
        evidence_ids=("repository_inventory:production",),
        supporting_evidence_ids=("repository_inventory:production",),
        runtime_observation=gap.RuntimeObservation.COMPLETE,
        policy_observation=gap.PolicyObservation.ACCEPTED,
        atomic_manual_order_package_proven=True,
        atomic_current_package_pointer_proven=True,
        **values,
    )


def _invalidate_predicate(
    facts: gap.AdapterFacts, predicate_id: str
) -> tuple[gap.AdapterFacts, bool]:
    field_name = gap._PREDICATE_FACT_FIELDS[predicate_id]
    if predicate_id == "P01":
        return replace(facts, evidence_ids=(), supporting_evidence_ids=()), True
    if predicate_id == "P10":
        return replace(facts, runtime_observation=gap.RuntimeObservation.INCOMPLETE), True
    if predicate_id == "P11":
        return replace(facts, runtime_observation=gap.RuntimeObservation.ABSENT), True
    if predicate_id == "P14":
        return replace(
            facts,
            schema_identity_verified=False,
            policy_candidate_valid=False,
            runtime_observation=gap.RuntimeObservation.NONE,
        ), True
    if predicate_id == "P20":
        return replace(facts, prose_excluded=False, prohibited_authority_absent=False), True
    if predicate_id == "P31":
        return replace(
            facts,
            atomic_manual_order_package_proven=False,
            atomic_current_package_pointer_proven=False,
        ), True
    if predicate_id == "P34":
        return facts, False
    return replace(facts, **{field_name: False}), True


def _assessment(
    check_id: str,
    facts: gap.AdapterFacts,
    *,
    dependencies_proven: bool = True,
) -> gap.CheckAssessment:
    check = next(item for item in CATALOG if item.check_id == check_id)
    statuses = {
        dependency_id: (
            ReadinessStatus.PROVEN_PRESENT
            if dependencies_proven
            else ReadinessStatus.CONTRADICTORY
        )
        for dependency_id in check.dependency_check_ids
    }
    return gap.assess_check(
        check,
        gap.PrerequisiteObservation(gap._ADAPTERS[check_id], facts, ()),
        statuses,
    )


def test_current_repository_report_is_complete_deterministic_and_report_only() -> None:
    evidence = gap.collect_repository_evidence(repo_root())
    first = gap.build_gap_report(repo_root())
    second = gap.build_gap_report(repo_root())
    assert first == second
    assert first["authority"] == gap.AUTHORITY_DECLARATION
    assert first["evaluated_at_utc"] is None
    assert first["evaluation_time_source"] == gap.EVALUATION_TIME_SOURCE
    assert first["content_identity_sha256"] == gap.report_content_identity_sha256(first)
    counts = first["summary_counts"]  # type: ignore[index]
    assert counts["total_checks"] == 81
    assert sum(
        value for key, value in counts.items() if key != "total_checks"
    ) == 81
    assert tuple(
        item["check_count"] for item in first["dimensions"]  # type: ignore[index]
    ) == (11, 15, 7, 9, 12, 9, 18)
    assert tuple(
        item["check_id"]
        for dimension in first["dimensions"]  # type: ignore[index]
        for item in dimension["checks"]
    ) == tuple(check.check_id for check in CATALOG)
    assert evidence.inventory.observer_external_consumers == (
        "src/investment_orchestrator/cli/observe_ltetf_target_architecture_gaps.py",
    )
    assert evidence.inventory.report_artifact_readers == ()
    assert evidence.inventory.prohibited_observer_capability_imports == ()
    assert all(
        item["authority_effect"] == "none"
        for dimension in first["dimensions"]  # type: ignore[index]
        for item in dimension["checks"]
    )
    assert {item["status"] for item in first["dimensions"][-1]["checks"]} >= {"PROVEN_PRESENT"}  # type: ignore[index]
    policy_check = _check(first, "investment_horizon_policy")
    assert policy_check["status"] == "UNRESOLVED_OPERATOR_POLICY"
    assert policy_check["evidence_ids"] == []


def test_every_catalog_predicate_is_executed_and_mutation_blocks_proof() -> None:
    assert set(gap._PREDICATE_FACT_FIELDS) == set(PROOF_PREDICATES)
    for predicate_id in sorted(PROOF_PREDICATES):
        complete = _complete_facts()
        assert gap._predicate_value(predicate_id, complete, dependencies_proven=True)
        changed, dependencies_proven = _invalidate_predicate(complete, predicate_id)
        assert not gap._predicate_value(
            predicate_id, changed, dependencies_proven=dependencies_proven
        )
        synthetic = replace(
            CATALOG[0],
            required_proof_predicates=(predicate_id,),
            disqualifying_conditions=(),
            dependency_check_ids=("risk_drawdown_policy",) if predicate_id == "P34" else (),
        )
        assessment = gap.assess_check(
            synthetic,
            gap.PrerequisiteObservation(gap._ADAPTERS[synthetic.check_id], changed, ()),
            {"risk_drawdown_policy": ReadinessStatus.CONTRADICTORY}
            if predicate_id == "P34"
            else {},
        )
        assert assessment.status is not ReadinessStatus.PROVEN_PRESENT


def test_prompt_or_filename_mentions_are_not_target_semantics_and_legacy_evidence_is_not_p01_proof(tmp_path: Path) -> None:
    root = _minimal_observer_repository(tmp_path)
    source_path = "src/investment_orchestrator/target_architecture/example.py"
    _write(
        root,
        source_path,
        """PROMPT = \"ltetf_target_architecture_v1\"
def validate_contract(value):
    if not value:
        raise ValueError("invalid")
    return value
def produce():
    return validate_contract({"ok": True})
""",
    )
    facts = gap._source_contract_facts(
        root,
        gap.EvidenceAdapter(
            check_id="example",
            kind=gap.AdapterKind.CONTRACT,
            producer_path=source_path,
            validator_symbol="validate_contract",
            producer_symbol="produce",
        ),
    )
    assert facts.validator_exists
    assert facts.producer_exists
    assert not facts.validator_reached
    assert not facts.target_semantics

    evidence = gap.collect_repository_evidence(repo_root())
    check = next(item for item in CATALOG if item.check_id == "evidence_provenance_contract")
    observation = gap.derive_prerequisite_observation(check, evidence)
    assert observation.facts.legacy_evidence_ids
    assert not observation.facts.supporting_evidence_ids
    assert not next(
        outcome for outcome in observation.predicate_outcomes if outcome.predicate_id == "P01"
    ).satisfied


def test_behavioral_predicates_remain_false_for_static_target_evidence(
    tmp_path: Path,
) -> None:
    root = _minimal_observer_repository(tmp_path)
    check_id = "immutable_finding_ledger"
    _install_contract_support(root, check_id)
    check = next(item for item in CATALOG if item.check_id == check_id)
    source_path = f"src/investment_orchestrator/target_architecture/{check_id}.py"

    def outcomes() -> dict[str, gap.PredicateOutcome]:
        return {
            item.predicate_id: item
            for item in gap.derive_prerequisite_observation(
                check, gap.collect_repository_evidence(root)
            ).predicate_outcomes
        }

    baseline = outcomes()
    for predicate in ("P04", "P06", "P17", "P21", "P22", "P40"):
        assert not baseline[predicate].satisfied
        assert baseline[predicate].diagnostic_codes == ("BEHAVIORAL_PROBE_UNAVAILABLE",)

    # Labels, pytest syntax, exception declarations, and unreachable calls are
    # supporting text only; none can turn a static source fact into behavior.
    _write(
        root,
        f"tests/unit/test_ltetf_target_architecture_{check_id}.py",
        """import pytest
fixture_identity_sha256 = '0' * 64
def test_valid_fixture_name_only():
    assert True
def test_invalid_fixture_with_pytest_raises_only():
    with pytest.raises(ValueError):
        raise ValueError('decoy')
""",
    )
    source = (root / source_path).read_text(encoding="utf-8")
    _write(
        root,
        source_path,
        source
        + "\nif False:\n    validate_contract({'schema_version': 'wrong', 'value': 'wrong'})\n"
        + "def permissive(value: dict) -> dict:\n    return {}\n",
    )
    after = outcomes()
    assert all(not after[predicate].satisfied for predicate in ("P04", "P06", "P17", "P21", "P22", "P40"))


def test_observer_source_never_imports_or_invokes_target_code(tmp_path: Path) -> None:
    source = inspect.getsource(gap)
    forbidden = (
        "subprocess.run",
        "tempfile.TemporaryDirectory",
        "_BEHAVIORAL_PROBE_CHILD",
        "_run_behavioral_probe",
        "_behavioral_probe_spec",
        "compile(source",
    )
    assert all(token not in source for token in forbidden)

    root = _minimal_observer_repository(tmp_path)
    _install_contract_support(root, "evidence_provenance_contract")
    path = root / "src/investment_orchestrator/target_architecture/evidence_provenance_contract.py"
    path.write_text("raise RuntimeError('target import must never run')\n" + path.read_text(encoding="utf-8"), encoding="utf-8")
    check = next(item for item in CATALOG if item.check_id == "evidence_provenance_contract")
    observation = gap.derive_prerequisite_observation(
        check, gap.collect_repository_evidence(root)
    )
    assert not next(item for item in observation.predicate_outcomes if item.predicate_id == "P04").satisfied


def test_static_supporting_evidence_is_partial_not_behavioral_proof(tmp_path: Path) -> None:
    root = _minimal_observer_repository(tmp_path)
    _install_contract_support(root, "evidence_provenance_contract")
    result = _check(gap.build_gap_report(root), "evidence_provenance_contract")
    assert result["status"] == "PARTIAL"
    assert "TARGET_PROOF_PARTIAL" in result["reason_codes"]
    outcomes = {item["predicate_id"]: item for item in result["predicate_outcomes"]}
    assert not outcomes["P04"]["satisfied"]
    assert outcomes["P04"]["diagnostic_codes"] == ["BEHAVIORAL_PROBE_UNAVAILABLE"]


@pytest.mark.parametrize(
    ("check_id", "predicate_id", "fact_name"),
    [
        ("trusted_evaluation_clock", "P25", "trusted_clock_valid"),
        ("evidence_selection_policy", "P26", "selection_bounds_enforced"),
        ("evidence_packet_bounds", "P26", "selection_bounds_enforced"),
        ("analyst_invocation_grounding_boundary", "P30", "bounded_llm_input"),
        ("atomic_manual_order_package", "P31", "atomic_manual_order_package_proven"),
        ("atomic_current_package_pointer", "P31", "atomic_current_package_pointer_proven"),
        ("postcompile_final_safety", "P32", "postcompile_validation"),
        ("deterministic_evidence_sufficiency", "P33", "evidence_sufficiency"),
    ],
)
def test_modifier_facts_remain_false_without_authorized_nonexecuting_proof(
    tmp_path: Path,
    check_id: str,
    predicate_id: str,
    fact_name: str,
) -> None:
    root = _minimal_observer_repository(tmp_path)
    check = next(item for item in CATALOG if item.check_id == check_id)
    if check.contract_owner.value == "operator_and_deterministic_validation":
        _install_policy_support(root, _policy_payload(consumers=[]))
    else:
        _install_contract_support(root, check_id)
    _write(root, f"docs/{check_id}_modifier_design.md", f"{predicate_id} complete")
    observation = gap.derive_prerequisite_observation(
        check, gap.collect_repository_evidence(root)
    )
    assert not getattr(observation.facts, fact_name)
    outcome = next(
        item for item in observation.predicate_outcomes if item.predicate_id == predicate_id
    )
    assert not outcome.satisfied
    assert outcome.diagnostic_codes == ("BEHAVIORAL_PROBE_UNAVAILABLE",)


def test_p31_facts_remain_separate_and_broker_absence_supplies_neither(
    tmp_path: Path,
) -> None:
    root = _minimal_observer_repository(tmp_path)
    package = next(item for item in CATALOG if item.check_id == "atomic_manual_order_package")
    pointer = next(item for item in CATALOG if item.check_id == "atomic_current_package_pointer")
    _install_contract_support(root, package.check_id)
    _install_contract_support(root, pointer.check_id)
    evidence = gap.collect_repository_evidence(root)
    package_facts = gap.derive_prerequisite_observation(package, evidence).facts
    pointer_facts = gap.derive_prerequisite_observation(pointer, evidence).facts
    broker = next(item for item in CATALOG if item.check_id == "broker_live_execution_absence")
    broker_facts = gap.derive_prerequisite_observation(broker, evidence).facts
    assert not package_facts.atomic_manual_order_package_proven
    assert not package_facts.atomic_current_package_pointer_proven
    assert not pointer_facts.atomic_current_package_pointer_proven
    assert not pointer_facts.atomic_manual_order_package_proven
    assert not broker_facts.atomic_manual_order_package_proven
    assert not broker_facts.atomic_current_package_pointer_proven


def test_policy_candidate_missing_or_optional_only_required_facets_remains_unresolved(tmp_path: Path) -> None:
    root = _minimal_observer_repository(tmp_path)
    schema = _policy_schema()
    payload = _policy_payload(state="accepted", activation="active")
    payload["policies"] = {"investment_horizon_policy": {}}
    payload["policy_identity_sha256"] = gap._policy_identity(payload)
    _install_policy_support(root, payload, schema=schema)
    empty = _check(gap.build_gap_report(root), "investment_horizon_policy")
    assert empty["status"] == "UNRESOLVED_OPERATOR_POLICY"
    assert empty["reason_codes"] == ["OPERATOR_POLICY_DECISION_REQUIRED"]

    # A schema that permits a section but declares no machine-required facet
    # cannot turn a placeholder into a decision-complete policy.
    optional_only = _policy_schema()
    section = optional_only["properties"]["policies"]["properties"]["investment_horizon_policy"]
    section.pop("required")
    optional_only = _reidentity_schema(optional_only)
    payload = _policy_payload(state="accepted", activation="active")
    payload["policy_identity_sha256"] = gap._policy_identity(payload)
    _install_policy_support(root, payload, schema=optional_only)
    result = _check(gap.build_gap_report(root), "investment_horizon_policy")
    assert result["status"] == "UNRESOLVED_OPERATOR_POLICY"
    assert not {item["predicate_id"] for item in result["predicate_outcomes"] if item["satisfied"]} >= {"P18"}


def test_every_catalog_required_predicate_mutation_prevents_proven_present() -> None:
    complete = _complete_facts()
    for check in CATALOG:
        proven = gap.assess_check(
            check,
            gap.PrerequisiteObservation(gap._ADAPTERS[check.check_id], complete, ()),
            {dependency: ReadinessStatus.PROVEN_PRESENT for dependency in check.dependency_check_ids},
        )
        assert proven.status is ReadinessStatus.PROVEN_PRESENT, check.check_id
        for predicate_id in check.required_proof_predicates:
            changed, dependencies_proven = _invalidate_predicate(complete, predicate_id)
            try:
                result = gap.assess_check(
                    check,
                    gap.PrerequisiteObservation(gap._ADAPTERS[check.check_id], changed, ()),
                    {
                        dependency: (
                            ReadinessStatus.PROVEN_PRESENT
                            if dependencies_proven
                            else ReadinessStatus.CONTRADICTORY
                        )
                        for dependency in check.dependency_check_ids
                    },
                )
            except gap.ObserverIntegrityError as error:
                # Incomplete negative inventory is intentionally no-report,
                # which is stricter than assigning a non-proven status.
                assert error.code == "EVIDENCE_COLLECTION_FAILED"
            else:
                assert result.status is not ReadinessStatus.PROVEN_PRESENT, (check.check_id, predicate_id)


def test_frozen_profile_expansions_and_named_modifiers_are_not_implicit() -> None:
    assert PROOF_PROFILES == {
        "PA": ("P01", "P02", "P03", "P04", "P05", "P12", "P13", "P14", "P15", "P16", "P18", "P22", "P27", "P38", "P39"),
        "PC": ("P01", "P02", "P03", "P04", "P05", "P09", "P14", "P17", "P18", "P19", "P22", "P27", "P38", "P39"),
        "PI": ("P01", "P02", "P03", "P04", "P05", "P06", "P07", "P08", "P09", "P14", "P17", "P18", "P19", "P21", "P22", "P27", "P38", "P39", "P40"),
        "PD": ("P01", "P02", "P03", "P04", "P05", "P10", "P11", "P14", "P18", "P22", "P27", "P35", "P37", "P38", "P39"),
        "PL": ("P01", "P02", "P03", "P04", "P05", "P09", "P14", "P17", "P18", "P19", "P22", "P24", "P27", "P29", "P36", "P38", "P39"),
        "PAI": ("P01", "P02", "P03", "P04", "P05", "P06", "P07", "P08", "P12", "P13", "P14", "P15", "P16", "P18", "P21", "P22", "P27", "P38", "P39", "P40"),
        "PN": ("P01", "P27", "P28", "P38", "P39"),
    }
    modifiers = {"P25", "P26", "P30", "P31", "P32", "P33"}
    assert modifiers <= {predicate for check in CATALOG for predicate in check.required_proof_predicates}
    evidence = gap.collect_repository_evidence(repo_root())
    assert all(
        tuple(outcome.predicate_id for outcome in gap.derive_prerequisite_observation(check, evidence).predicate_outcomes)
        == check.required_proof_predicates
        for check in CATALOG
    )


def test_controlled_facts_reach_every_closed_status_without_check_id_status_routing() -> None:
    complete = _complete_facts()
    cases = {
        "PROVEN_PRESENT": ("evidence_provenance_contract", complete, True),
        "PRESENT_UNACCEPTED": (
            "investment_horizon_policy",
            replace(complete, policy_observation=gap.PolicyObservation.UNACCEPTED, policy_accepted=False),
            True,
        ),
        "DRAFT_ONLY": (
            "target_orchestration_contract",
            replace(
                complete,
                draft_only=True,
                contract_frozen=False,
                target_compatible_partial=False,
                evidence_ids=(),
                supporting_evidence_ids=("draft:investment_goal_profile_v1",),
            ),
            True,
        ),
        "PARTIAL": ("evidence_provenance_contract", replace(complete, target_semantics=False), True),
        "MISSING": ("evidence_provenance_contract", replace(complete, producer_exists=False), True),
        "UNRESOLVED_OPERATOR_POLICY": (
            "investment_horizon_policy",
            replace(complete, policy_observation=gap.PolicyObservation.NONE, policy_candidate_valid=False),
            True,
        ),
        "UNRESOLVED_CONTRACT": (
            "evidence_provenance_contract",
            replace(complete, contract_frozen=False, target_compatible_partial=False, evidence_ids=(), supporting_evidence_ids=()),
            True,
        ),
        "UNAVAILABLE_RUNTIME_DATA": (
            "holdings_state_contract",
            replace(complete, runtime_observation=gap.RuntimeObservation.ABSENT),
            True,
        ),
        "CONTRADICTORY": (
            "dormant_p4a_runtime_isolation",
            replace(complete, direct_contradiction=True, contradiction_reason="PROHIBITED_CONSUMER_PRESENT", contradiction_evidence_ids=("repository_inventory:production",)),
            True,
        ),
    }
    for expected, (check_id, facts, deps) in cases.items():
        assert _assessment(check_id, facts, dependencies_proven=deps).status.value == expected
    dependent = _assessment("evidence_provenance_contract", complete, dependencies_proven=False)
    assert dependent.status is ReadinessStatus.PARTIAL
    assert dependent.reason_codes == ("DEPENDENCY_NOT_PROVEN",)
    source = inspect.getsource(gap)
    assert "_POLICY_CHECK_IDS" not in source
    assert "_RUNTIME_DATA_CHECK_IDS" not in source
    assert "_NEGATIVE_CHECK_IDS" not in source
    assert "check_id -> status" not in source


def test_policy_candidates_require_recomputed_identity_activation_validator_and_consumers(tmp_path: Path) -> None:
    root = _minimal_observer_repository(tmp_path)
    payload = _policy_payload()
    _install_policy_support(root, payload)
    assert _check(gap.build_gap_report(root), "investment_horizon_policy")["status"] == "PRESENT_UNACCEPTED"

    forged = dict(payload)
    forged["policy_identity_sha256"] = "a" * 64
    _write(root, "inputs/current/ltetf_operator_mandate.json", json.dumps(forged))
    assert _check(gap.build_gap_report(root), "investment_horizon_policy")["status"] == "UNRESOLVED_OPERATOR_POLICY"

    stale = dict(payload)
    stale["effective_version"] = "v2"
    _write(root, "inputs/current/ltetf_operator_mandate.json", json.dumps(stale))
    assert _check(gap.build_gap_report(root), "investment_horizon_policy")["status"] == "UNRESOLVED_OPERATOR_POLICY"

    missing_version = dict(payload)
    missing_version.pop("effective_version")
    missing_version["policy_identity_sha256"] = gap._policy_identity(missing_version)
    _write(root, "inputs/current/ltetf_operator_mandate.json", json.dumps(missing_version))
    assert _check(gap.build_gap_report(root), "investment_horizon_policy")["status"] == "UNRESOLVED_OPERATOR_POLICY"

    active_candidate = _policy_payload(activation="active")
    _write(root, "inputs/current/ltetf_operator_mandate.json", json.dumps(active_candidate))
    assert _check(gap.build_gap_report(root), "investment_horizon_policy")["status"] == "UNRESOLVED_OPERATOR_POLICY"

    unsupported_schema = _policy_schema()
    unsupported_schema["$schema"] = "http://json-schema.org/draft-07/schema#"
    _write(root, "schemas/operator_mandate_candidate.schema.json", json.dumps(unsupported_schema))
    _write(root, "inputs/current/ltetf_operator_mandate.json", json.dumps(_policy_payload()))
    assert _check(gap.build_gap_report(root), "investment_horizon_policy")["status"] == "UNRESOLVED_OPERATOR_POLICY"


def test_accepted_policy_remains_partial_without_behavioral_proof_and_conflicts_are_direct(tmp_path: Path) -> None:
    root = _minimal_observer_repository(tmp_path)
    accepted = _policy_payload(state="accepted", activation="active")
    _install_policy_support(root, accepted)
    report = gap.build_gap_report(root)
    accepted_check = _check(report, "investment_horizon_policy")
    assert accepted_check["status"] == "PARTIAL"
    assert "TARGET_PROOF_PARTIAL" in accepted_check["reason_codes"]

    conflict = _policy_payload(state="accepted", activation="active", version="v2")
    _write(root, "inputs/current/ltetf_operator_mandate_second.json", json.dumps(conflict))
    conflicted = _check(gap.build_gap_report(root), "investment_horizon_policy")
    assert conflicted["status"] == "CONTRADICTORY"
    assert conflicted["reason_codes"] == ["ACTIVE_POLICY_IDENTITY_CONFLICT"]
    assert len(conflicted["evidence_ids"]) >= 2


def test_operator_policy_precedence_retains_only_relevant_draft_support(tmp_path: Path) -> None:
    current = gap.build_gap_report(repo_root())
    for check in CATALOG:
        if gap._ADAPTERS[check.check_id].kind is gap.AdapterKind.POLICY:
            assert _check(current, check.check_id)["status"] == "UNRESOLVED_OPERATOR_POLICY"

    root = _minimal_observer_repository(tmp_path)
    _write(
        root,
        "docs/investment_goal_profile_v1.md",
        "LTETF-01 prerequisite: investment_horizon_policy\n",
    )
    relevant = _check(gap.build_gap_report(root), "investment_horizon_policy")
    assert relevant["status"] == "UNRESOLVED_OPERATOR_POLICY"
    assert relevant["reason_codes"] == ["OPERATOR_POLICY_DECISION_REQUIRED"]
    assert relevant["evidence_ids"] == ["draft:investment_goal_profile_v1"]
    assert "DRAFT_DOCUMENT_NOT_ACTIVE_POLICY" in relevant["diagnostic_codes"]

    root = _minimal_observer_repository(tmp_path / "unrelated")
    _write(
        root,
        "docs/investment_goal_profile_v1.md",
        "LTETF-01 prerequisite: risk_drawdown_policy\n",
    )
    unrelated = _check(gap.build_gap_report(root), "investment_horizon_policy")
    assert unrelated["status"] == "UNRESOLVED_OPERATOR_POLICY"
    assert unrelated["evidence_ids"] == []


def test_nonpolicy_design_only_evidence_reaches_draft_only(tmp_path: Path) -> None:
    root = _minimal_observer_repository(tmp_path)
    _write(
        root,
        "docs/investment_goal_profile_v1.md",
        "LTETF-01 prerequisite: target_orchestration_contract\n",
    )
    result = _check(gap.build_gap_report(root), "target_orchestration_contract")
    assert result["status"] == "DRAFT_ONLY"
    assert result["reason_codes"] == ["DRAFT_EVIDENCE_ONLY"]
    assert result["evidence_ids"] == ["draft:investment_goal_profile_v1"]


def test_empty_policy_section_is_not_a_complete_candidate_facet(tmp_path: Path) -> None:
    root = _minimal_observer_repository(tmp_path)
    schema = _policy_schema()
    section = schema["properties"]["policies"]["properties"]["investment_horizon_policy"]
    section.pop("required")
    schema = _reidentity_schema(schema)
    payload = _policy_payload()
    payload["policies"] = {"investment_horizon_policy": {}}
    payload["policy_identity_sha256"] = gap._policy_identity(payload)
    _install_policy_support(root, payload, schema=schema)
    result = _check(gap.build_gap_report(root), "investment_horizon_policy")
    outcomes = {item["predicate_id"]: item for item in result["predicate_outcomes"]}
    assert result["status"] == "UNRESOLVED_OPERATOR_POLICY"
    assert not outcomes["P18"]["satisfied"]


def test_policy_consumer_mismatch_and_unsupported_nonactive_acceptance_are_not_accepted(tmp_path: Path) -> None:
    root = _minimal_observer_repository(tmp_path)
    _install_policy_support(root, _policy_payload(state="accepted", activation="active"))
    _write(
        root,
        "src/investment_orchestrator/policy_consumer.py",
        "from pathlib import Path\nPath('inputs/current/ltetf_operator_mandate.json').read_text()\n",
    )
    result = _check(gap.build_gap_report(root), "investment_horizon_policy")
    assert result["status"] == "UNRESOLVED_OPERATOR_POLICY"
    assert "POLICY_CONSUMER_MISMATCH" in result["diagnostic_codes"]

    root = _minimal_observer_repository(tmp_path / "nonactive")
    _install_policy_support(root, _policy_payload(state="accepted", activation="contract_readiness_only"))
    assert _check(gap.build_gap_report(root), "investment_horizon_policy")["status"] == "UNRESOLVED_OPERATOR_POLICY"


def test_runtime_data_distinguishes_contract_absence_producer_absence_absence_partial_invalid_complete_and_conflict(tmp_path: Path) -> None:
    root = _minimal_observer_repository(tmp_path)
    assert _check(gap.build_gap_report(root), "holdings_state_contract")["status"] == "UNRESOLVED_CONTRACT"

    _write(root, "schemas/ltetf_portfolio_state.schema.json", json.dumps(_runtime_schema()))
    assert _check(gap.build_gap_report(root), "holdings_state_contract")["status"] == "MISSING"

    _install_runtime_support(root)
    assert _check(gap.build_gap_report(root), "holdings_state_contract")["status"] == "UNAVAILABLE_RUNTIME_DATA"

    _write(root, "inputs/current/ltetf_portfolio_state.json", json.dumps(_runtime_payload(complete=False)))
    partial = _check(gap.build_gap_report(root), "holdings_state_contract")
    assert partial["status"] == "PARTIAL"
    assert "RUNTIME_DATA_INCOMPLETE" in partial["diagnostic_codes"]

    _write(root, "inputs/current/ltetf_portfolio_state.json", "{")
    invalid = _check(gap.build_gap_report(root), "holdings_state_contract")
    assert invalid["status"] == "PARTIAL"
    assert "CURRENT_RUNTIME_DATA_INVALID" in invalid["reason_codes"]

    complete = _runtime_payload(complete=True)
    _write(root, "inputs/current/ltetf_portfolio_state.json", json.dumps(complete))
    complete_result = _check(gap.build_gap_report(root), "holdings_state_contract")
    # A complete static runtime record remains conservative: its contract
    # predicates include behavioral enforcement that this observer never runs.
    assert complete_result["status"] == "PARTIAL"
    assert "BEHAVIORAL_PROBE_UNAVAILABLE" in complete_result["diagnostic_codes"]

    _write(root, "inputs/current/ltetf_portfolio_state_conflict.json", json.dumps(_runtime_payload(complete=True, suffix="b")))
    conflict = _check(gap.build_gap_report(root), "holdings_state_contract")
    assert conflict["status"] == "CONTRADICTORY"
    assert conflict["reason_codes"] == ["CURRENT_RUNTIME_IDENTITY_CONFLICT"]
    assert len(conflict["evidence_ids"]) >= 2

    (root / "inputs/current/ltetf_portfolio_state_conflict.json").unlink()
    (root / "inputs/current/ltetf_portfolio_state.json").unlink()
    _write(
        root,
        "inputs/current/ltetf_portfolio_state_history.json",
        json.dumps(_runtime_payload(complete=True)),
    )
    historical = _check(gap.build_gap_report(root), "holdings_state_contract")
    assert historical["status"] == "PARTIAL"
    assert "RUNTIME_SLOT_NOT_CURRENT" in historical["diagnostic_codes"]


def test_runtime_contract_requires_every_target_facet_in_properties_and_required(tmp_path: Path) -> None:
    root = _minimal_observer_repository(tmp_path)
    schema = _runtime_schema()
    schema["required"].remove("holdings")
    schema = _reidentity_schema(schema)
    _write(root, "schemas/ltetf_portfolio_state.schema.json", json.dumps(schema))
    result = _check(gap.build_gap_report(root), "holdings_state_contract")
    assert result["status"] == "UNRESOLVED_CONTRACT"

    root = _minimal_observer_repository(tmp_path / "open")
    open_schema = _runtime_schema()
    open_schema["additionalProperties"] = True
    open_schema = _reidentity_schema(open_schema)
    _write(root, "schemas/ltetf_portfolio_state.schema.json", json.dumps(open_schema))
    assert _check(gap.build_gap_report(root), "holdings_state_contract")["status"] == "UNRESOLVED_CONTRACT"

    root = _minimal_observer_repository(tmp_path / "complete")
    _install_runtime_support(root)
    _write(root, "inputs/current/ltetf_portfolio_state.json", json.dumps(_runtime_payload()))
    complete = _check(gap.build_gap_report(root), "holdings_state_contract")
    assert complete["status"] == "PARTIAL"
    assert "BEHAVIORAL_PROBE_UNAVAILABLE" in complete["diagnostic_codes"]


def test_all_nine_statuses_are_reachable_through_repository_evidence(tmp_path: Path) -> None:
    root = _minimal_observer_repository(tmp_path / "proven")
    assert _check(gap.build_gap_report(root), "broker_live_execution_absence")["status"] == "PROVEN_PRESENT"

    root = _minimal_observer_repository(tmp_path / "unaccepted")
    _install_policy_support(root, _policy_payload())
    assert _check(gap.build_gap_report(root), "investment_horizon_policy")["status"] == "PRESENT_UNACCEPTED"

    root = _minimal_observer_repository(tmp_path / "draft")
    _write(root, "docs/investment_goal_profile_v1.md", "LTETF-01 prerequisite: target_orchestration_contract\n")
    assert _check(gap.build_gap_report(root), "target_orchestration_contract")["status"] == "DRAFT_ONLY"

    root = _minimal_observer_repository(tmp_path / "partial")
    _install_contract_support(root, "evidence_provenance_contract", test=False)
    assert _check(gap.build_gap_report(root), "evidence_provenance_contract")["status"] == "PARTIAL"

    root = _minimal_observer_repository(tmp_path / "missing")
    _install_contract_support(root, "evidence_provenance_contract", source=False, test=False)
    assert _check(gap.build_gap_report(root), "evidence_provenance_contract")["status"] == "MISSING"

    root = _minimal_observer_repository(tmp_path / "operator")
    assert _check(gap.build_gap_report(root), "investment_horizon_policy")["status"] == "UNRESOLVED_OPERATOR_POLICY"

    root = _minimal_observer_repository(tmp_path / "contract")
    assert _check(gap.build_gap_report(root), "evidence_provenance_contract")["status"] == "UNRESOLVED_CONTRACT"

    root = _minimal_observer_repository(tmp_path / "runtime")
    _install_runtime_support(root)
    assert _check(gap.build_gap_report(root), "holdings_state_contract")["status"] == "UNAVAILABLE_RUNTIME_DATA"

    root = _minimal_observer_repository(tmp_path / "contradiction")
    _write(root, "src/investment_orchestrator/broker_operation.py", "def submit_order(order):\n    return order\n")
    assert _check(gap.build_gap_report(root), "broker_live_execution_absence")["status"] == "CONTRADICTORY"


def test_target_adapter_consumer_allowlist_is_checked_from_resolved_production_imports(tmp_path: Path) -> None:
    root = _minimal_observer_repository(tmp_path)
    _install_runtime_support(root)
    _write(root, "inputs/current/ltetf_portfolio_state.json", json.dumps(_runtime_payload()))
    assert _check(gap.build_gap_report(root), "holdings_state_contract")["status"] == "PARTIAL"
    _write(
        root,
        "src/investment_orchestrator/unapproved_target_consumer.py",
        "from investment_orchestrator.target_architecture import portfolio_state_producer\n",
    )
    report = gap.build_gap_report(root)
    facts = gap.derive_prerequisite_observation(
        next(item for item in CATALOG if item.check_id == "holdings_state_contract"),
        gap.collect_repository_evidence(root),
    ).facts
    assert not facts.consumers_compatible
    check = _check(report, "portfolio_snapshot_identity")
    outcomes = {outcome["predicate_id"]: outcome for outcome in check["predicate_outcomes"]}
    assert not outcomes["P08"]["satisfied"]
    assert not outcomes["P40"]["satisfied"]


@pytest.mark.parametrize(
    "source",
    [
        "import investment_orchestrator.observability.ltetf_target_architecture_gap_report\n",
        "import investment_orchestrator.observability.ltetf_target_architecture_gap_report as observer\n",
        "from investment_orchestrator.observability import ltetf_target_architecture_gap_report\n",
        "import importlib\nimportlib.import_module('investment_orchestrator.observability.ltetf_target_architecture_gap_report')\n",
        "__import__('investment_orchestrator.observability.ltetf_target_architecture_gap_report')\n",
        "from importlib import import_module as dynamic_import\ndynamic_import('investment_orchestrator.observability.ltetf_target_architecture_gap_report')\n",
        "from builtins import __import__ as dynamic_import\ndynamic_import('investment_orchestrator.observability.ltetf_target_architecture_gap_report')\n",
    ],
)
def test_direct_aliased_and_literal_dynamic_observer_imports_are_inventory_consumers(tmp_path: Path, source: str) -> None:
    root = _minimal_observer_repository(tmp_path)
    _write(root, "src/investment_orchestrator/consumer.py", source)
    with pytest.raises(gap.ObserverIntegrityError, match="CONSUMER_INVENTORY_INCOMPLETE"):
        gap.build_gap_report(root)


def test_relative_literal_dynamic_and_unresolved_imports_fail_closed(tmp_path: Path) -> None:
    root = _minimal_observer_repository(tmp_path)
    _write(root, "src/investment_orchestrator/pkg/__init__.py", "")
    _write(
        root,
        "src/investment_orchestrator/pkg/consumer.py",
        "from ..observability import ltetf_target_architecture_gap_report\n",
    )
    with pytest.raises(gap.ObserverIntegrityError, match="CONSUMER_INVENTORY_INCOMPLETE"):
        gap.build_and_write_gap_report(root)
    assert not (root / gap.REPORT_NAMESPACE_RELATIVE_PATH).exists()

    root = _minimal_observer_repository(tmp_path / "relative_dynamic")
    _write(root, "src/investment_orchestrator/pkg/__init__.py", "")
    _write(
        root,
        "src/investment_orchestrator/pkg/consumer.py",
        "import importlib\nimportlib.import_module('..observability.ltetf_target_architecture_gap_report', package='investment_orchestrator.pkg')\n",
    )
    with pytest.raises(gap.ObserverIntegrityError, match="CONSUMER_INVENTORY_INCOMPLETE"):
        gap.build_and_write_gap_report(root)
    assert not (root / gap.REPORT_NAMESPACE_RELATIVE_PATH).exists()

    root = _minimal_observer_repository(tmp_path / "dynamic")
    _write(
        root,
        "src/investment_orchestrator/consumer.py",
        "import importlib\nname = 'investment_orchestrator.observability'\nimportlib.import_module(name)\n",
    )
    with pytest.raises(gap.ObserverIntegrityError, match="CONSUMER_INVENTORY_INCOMPLETE"):
        gap.build_gap_report(root)


@pytest.mark.parametrize(
    "pyproject_fragment",
    [
        "[project.scripts]\nobserver = 'investment_orchestrator.observability.ltetf_target_architecture_gap_report:build_gap_report'\n",
        "[project.gui-scripts]\nobserver = 'investment_orchestrator.observability.ltetf_target_architecture_gap_report:build_gap_report'\n",
        "[project.entry-points.example]\nobserver = 'investment_orchestrator.observability.ltetf_target_architecture_gap_report:build_gap_report'\n",
        "[tool.example.entry-points]\nobserver = 'investment_orchestrator.observability.ltetf_target_architecture_gap_report:build_gap_report'\n",
    ],
)
def test_all_pyproject_entry_point_groups_are_inventoried(tmp_path: Path, pyproject_fragment: str) -> None:
    root = _minimal_observer_repository(tmp_path)
    _write(root, "pyproject.toml", "[project]\nname = 'observer-fixture'\n" + pyproject_fragment)
    with pytest.raises(gap.ObserverIntegrityError, match="CONSUMER_INVENTORY_INCOMPLETE"):
        gap.build_gap_report(root)


@pytest.mark.parametrize(
    "source",
    [
        "from pathlib import Path\nPath('artifacts').rglob('*.json')\n",
        "import os\nos.walk('artifacts')\n",
        "def read_json(value): return value.read_text()\nread_json('artifacts/target_architecture/report_only/ltetf_01/reports/x.json')\n",
        "from pathlib import Path\nname = 'x.json'\nPath(f'artifacts/target_architecture/report_only/ltetf_01/reports/{name}').read_text()\n",
        "from pathlib import Path\nnamespace = 'target_architecture/report_only/ltetf_01/reports'\nname = 'x.json'\npath = f'artifacts/{namespace}/{name}'\nPath(path).read_text()\n",
        "def consume(report):\n    return report['prerequisite_catalog_identity_sha256']\n",
        "import importlib\nselector = unknown_selector\nloader = getattr(importlib, selector)\nloader('investment_orchestrator.observability.ltetf_target_architecture_gap_report')\n",
        "eval(\"open('artifacts/target_architecture/report_only/ltetf_01/reports/x.json')\")\n",
    ],
)
def test_generic_readers_and_unresolved_dynamic_constructs_fail_closed(tmp_path: Path, source: str) -> None:
    root = _minimal_observer_repository(tmp_path)
    _write(root, "src/investment_orchestrator/consumer.py", source)
    with pytest.raises(gap.ObserverIntegrityError, match="CONSUMER_INVENTORY_INCOMPLETE"):
        gap.build_gap_report(root)


def test_provably_unrelated_dynamic_constructs_do_not_block_inventory(tmp_path: Path) -> None:
    root = _minimal_observer_repository(tmp_path)
    _write(
        root,
        "src/investment_orchestrator/unrelated_dynamic.py",
        """import importlib
def import_module(name):
    return name
selector = 'import_module'
loader = getattr(importlib, selector)
loader('json')
eval('1 + 1')
""",
    )
    assert gap.build_gap_report(root)["summary_counts"]

    _write(
        root,
        "src/investment_orchestrator/unrelated_scanner.py",
        "from pathlib import Path\nPath('artifacts/history').rglob('*.json')\n",
    )
    assert gap.build_gap_report(root)["summary_counts"]


@pytest.mark.parametrize("statement", ("eval(code)", "exec(code)"))
def test_relevant_nonliteral_exec_and_eval_fail_closed_without_execution(
    tmp_path: Path,
    statement: str,
) -> None:
    root = _minimal_observer_repository(tmp_path)
    _write(
        root,
        "src/investment_orchestrator/consumer.py",
        "report_path = 'artifacts/target_architecture/report_only/ltetf_01/reports/report.json'\n"
        "code = generated_source\n"
        f"{statement}\n",
    )
    with pytest.raises(gap.ObserverIntegrityError, match="CONSUMER_INVENTORY_INCOMPLETE"):
        gap.build_gap_report(root)


def test_relevant_literal_dynamic_code_is_parsed_never_executed_and_fails_closed_when_malformed(
    tmp_path: Path,
) -> None:
    root = _minimal_observer_repository(tmp_path)
    _write(
        root,
        "src/investment_orchestrator/consumer.py",
        "report_path = 'artifacts/target_architecture/report_only/ltetf_01/reports/report.json'\n"
        "eval('def malformed(:')\n",
    )
    with pytest.raises(gap.ObserverIntegrityError, match="CONSUMER_INVENTORY_INCOMPLETE"):
        gap.build_gap_report(root)

    root = _minimal_observer_repository(tmp_path / "literal_safe")
    _write(
        root,
        "src/investment_orchestrator/consumer.py",
        "report_path = 'artifacts/target_architecture/report_only/ltetf_01/reports/report.json'\n"
        "eval('1 + 1')\n",
    )
    # Static analysis reads the literal expression but never evaluates it;
    # a bounded constant expression neither imports nor reads the report.
    assert gap.build_gap_report(root)["summary_counts"]


def test_provably_unrelated_nonliteral_dynamic_code_does_not_block_inventory(tmp_path: Path) -> None:
    root = _minimal_observer_repository(tmp_path)
    _write(
        root,
        "src/investment_orchestrator/unrelated_dynamic.py",
        "code = generated_source\neval(code)\n",
    )
    assert gap.build_gap_report(root)["summary_counts"]


@pytest.mark.parametrize(
    "source",
    [
        "import importlib as il\nselector = 'import_module'\nloader = getattr(il, selector)\nmodule_name = 'investment_orchestrator.observability.ltetf_target_architecture_gap_report'\nloader(module_name)\n",
        "import importlib\nselector = unknown_selector\nloader = getattr(importlib, selector)\nloader('investment_orchestrator.observability.ltetf_target_architecture_gap_report')\n",
        "import importlib\nloader = importlib.import_module\nnamespace = 'investment_orchestrator.observability'\nmodule_name = f'{namespace}.{observer_module_name}'\nloader(module_name)\n",
    ],
)
def test_getattr_and_nonliteral_relevant_dynamic_imports_fail_closed(tmp_path: Path, source: str) -> None:
    root = _minimal_observer_repository(tmp_path)
    _write(root, "src/investment_orchestrator/consumer.py", source)
    with pytest.raises(gap.ObserverIntegrityError, match="CONSUMER_INVENTORY_INCOMPLETE"):
        gap.build_gap_report(root)


def test_shadowed_and_unrelated_import_module_names_are_not_import_machinery(tmp_path: Path) -> None:
    root = _minimal_observer_repository(tmp_path)
    _write(
        root,
        "src/investment_orchestrator/local_loader.py",
        "def import_module(value):\n    return value\n",
    )
    _write(
        root,
        "src/investment_orchestrator/consumer.py",
        """from investment_orchestrator.local_loader import import_module
def local_import_module(value):
    return value
import_module('investment_orchestrator.observability.ltetf_target_architecture_gap_report')
local_import_module('investment_orchestrator.observability.ltetf_target_architecture_gap_report')
""",
    )
    assert gap.build_gap_report(root)["summary_counts"]

    root = _minimal_observer_repository(tmp_path / "shadowed_getattr")
    _write(
        root,
        "src/investment_orchestrator/consumer.py",
        """import importlib
def getattr(module, selector):
    return lambda value: value
loader = getattr(importlib, 'import_module')
loader('investment_orchestrator.observability.ltetf_target_architecture_gap_report')
""",
    )
    assert gap.build_gap_report(root)["summary_counts"]


@pytest.mark.parametrize(
    "source",
    [
        "from investment_orchestrator.shared_loader import load_json_file\npath = 'artifacts/target_architecture/report_only/ltetf_01/reports/report.json'\nload_json_file(path)\n",
        "from investment_orchestrator.shared_loader import load_json_file as read\npath = 'artifacts/target_architecture/report_only/ltetf_01/reports/report.json'\nread(path)\n",
        "from investment_orchestrator.shared_loader import load_json_file\ndef wrapper(path):\n    return load_json_file(path)\npath = 'artifacts/target_architecture/report_only/ltetf_01/reports/report.json'\nwrapper(path)\n",
        "def unresolved_wrapper(path):\n    return load_unknown(path)\npath = 'artifacts/target_architecture/report_only/ltetf_01/reports/report.json'\nunresolved_wrapper(path)\n",
    ],
)
def test_shared_loader_and_wrapper_report_path_flows_fail_closed(tmp_path: Path, source: str) -> None:
    root = _minimal_observer_repository(tmp_path)
    _write(
        root,
        "src/investment_orchestrator/shared_loader.py",
        "import json\ndef load_json_file(path):\n    return json.loads(path.read_text())\n",
    )
    _write(root, "src/investment_orchestrator/consumer.py", source)
    with pytest.raises(gap.ObserverIntegrityError, match="CONSUMER_INVENTORY_INCOMPLETE"):
        gap.build_gap_report(root)


def test_shared_loader_outside_observer_namespace_is_not_a_report_reader(tmp_path: Path) -> None:
    root = _minimal_observer_repository(tmp_path)
    _write(
        root,
        "src/investment_orchestrator/shared_loader.py",
        "def load_json_file(path):\n    return path.read_text()\n",
    )
    _write(
        root,
        "src/investment_orchestrator/consumer.py",
        "from investment_orchestrator.shared_loader import load_json_file\nload_json_file('artifacts/current/unrelated.json')\n",
    )
    evidence = gap.collect_repository_evidence(root)
    assert evidence.inventory.report_artifact_readers == ()


@pytest.mark.parametrize(
    "consumer_path, source",
    [
        (
            "src/investment_orchestrator/consumer.py",
            "from investment_orchestrator.helper import consume as custom_name\n"
            "report_path = 'artifacts/target_architecture/report_only/ltetf_01/reports/report.json'\n"
            "custom_name(report_path)\n",
        ),
        (
            "src/investment_orchestrator/pkg/consumer.py",
            "from .helper import consume as custom_name\n"
            "report_path = 'artifacts/target_architecture/report_only/ltetf_01/reports/report.json'\n"
            "custom_name(report_path)\n",
        ),
        (
            "src/investment_orchestrator/pkg/consumer.py",
            "from . import helper\n"
            "report_path = 'artifacts/target_architecture/report_only/ltetf_01/reports/report.json'\n"
            "helper.consume(report_path)\n",
        ),
    ],
)
def test_custom_named_imported_reader_wrappers_are_resolved_one_level(
    tmp_path: Path,
    consumer_path: str,
    source: str,
) -> None:
    root = _minimal_observer_repository(tmp_path)
    if "/pkg/" in consumer_path:
        _write(root, "src/investment_orchestrator/pkg/__init__.py", "")
        helper_path = "src/investment_orchestrator/pkg/helper.py"
    else:
        helper_path = "src/investment_orchestrator/helper.py"
    _write(root, helper_path, "def consume(path):\n    return path.read_text()\n")
    _write(root, consumer_path, source)
    with pytest.raises(gap.ObserverIntegrityError, match="CONSUMER_INVENTORY_INCOMPLETE"):
        gap.build_gap_report(root)


@pytest.mark.parametrize(
    ("helper_source", "consumer_path", "consumer_source"),
    [
        (
            "def consume(metadata, path):\n    return path.read_text()\n",
            "src/investment_orchestrator/consumer.py",
            "from investment_orchestrator.helper import consume\n"
            "report_path = 'artifacts/target_architecture/report_only/ltetf_01/reports/report.json'\n"
            "consume('metadata', report_path)\n",
        ),
        (
            "def consume(metadata, /, path):\n    return path.read_text()\n",
            "src/investment_orchestrator/consumer.py",
            "from investment_orchestrator.helper import consume\n"
            "report_path = 'artifacts/target_architecture/report_only/ltetf_01/reports/report.json'\n"
            "consume('metadata', path=report_path)\n",
        ),
        (
            "def consume(first, metadata, path):\n    return path.read_text()\n",
            "src/investment_orchestrator/consumer.py",
            "from investment_orchestrator.helper import consume\n"
            "report_path = 'artifacts/target_architecture/report_only/ltetf_01/reports/report.json'\n"
            "consume('first', 'metadata', report_path)\n",
        ),
        (
            "def consume(metadata, path):\n    return path.read_text()\n",
            "src/investment_orchestrator/consumer.py",
            "from investment_orchestrator.helper import consume\n"
            "report_path = 'artifacts/target_architecture/report_only/ltetf_01/reports/report.json'\n"
            "consume(metadata='metadata', path=report_path)\n",
        ),
        (
            "def consume(metadata, *, path):\n    return path.read_text()\n",
            "src/investment_orchestrator/consumer.py",
            "from investment_orchestrator.helper import consume\n"
            "report_path = 'artifacts/target_architecture/report_only/ltetf_01/reports/report.json'\n"
            "consume('metadata', path=report_path)\n",
        ),
        (
            "def consume(first, path):\n    return path.read_text()\n",
            "src/investment_orchestrator/consumer.py",
            "from investment_orchestrator.helper import consume\n"
            "report_path = 'artifacts/target_architecture/report_only/ltetf_01/reports/report.json'\n"
            "consume('first', path=report_path)\n",
        ),
        (
            "def consume(metadata='default', path=None):\n    return path.read_text()\n",
            "src/investment_orchestrator/consumer.py",
            "from investment_orchestrator.helper import consume\n"
            "report_path = 'artifacts/target_architecture/report_only/ltetf_01/reports/report.json'\n"
            "consume(path=report_path)\n",
        ),
        (
            "def consume(metadata, path):\n    target = path\n    return target.read_text()\n",
            "src/investment_orchestrator/consumer.py",
            "from investment_orchestrator.helper import consume as custom_consumer\n"
            "report_path = 'artifacts/target_architecture/report_only/ltetf_01/reports/report.json'\n"
            "custom_consumer('metadata', report_path)\n",
        ),
        (
            "def nested(path):\n    return path.read_text()\ndef consume(metadata, path):\n    return nested(path)\n",
            "src/investment_orchestrator/consumer.py",
            "from investment_orchestrator.helper import consume\n"
            "report_path = 'artifacts/target_architecture/report_only/ltetf_01/reports/report.json'\n"
            "consume('metadata', report_path)\n",
        ),
        (
            "def consume(metadata, path):\n    return path.read_text()\n",
            "src/investment_orchestrator/pkg/consumer.py",
            "from .helper import consume as custom_consumer\n"
            "report_path = 'artifacts/target_architecture/report_only/ltetf_01/reports/report.json'\n"
            "custom_consumer('metadata', report_path)\n",
        ),
    ],
)
def test_imported_wrapper_binds_each_relevant_argument_to_its_exact_parameter(
    tmp_path: Path,
    helper_source: str,
    consumer_path: str,
    consumer_source: str,
) -> None:
    root = _minimal_observer_repository(tmp_path)
    if "/pkg/" in consumer_path:
        _write(root, "src/investment_orchestrator/pkg/__init__.py", "")
        helper_path = "src/investment_orchestrator/pkg/helper.py"
    else:
        helper_path = "src/investment_orchestrator/helper.py"
    _write(root, helper_path, helper_source)
    _write(root, consumer_path, consumer_source)

    evidence = gap.collect_repository_evidence(root)
    assert evidence.inventory.report_artifact_readers == (consumer_path,)
    with pytest.raises(gap.ObserverIntegrityError, match="CONSUMER_INVENTORY_INCOMPLETE"):
        gap.build_gap_report(root)


@pytest.mark.parametrize(
    ("helper_source", "call_source"),
    [
        (
            "def consume(path, metadata):\n    return metadata.read_text()\n",
            "consume(report_path, 'artifacts/current/unrelated.json')\n",
        ),
        (
            "def consume(path, metadata):\n    return load_json_file(metadata)\n",
            "consume(report_path, 'artifacts/current/unrelated.json')\n",
        ),
        (
            "def consume(path):\n    return path == 'unchanged'\n",
            "consume(report_path)\n",
        ),
        (
            "def consume(path):\n    logger.info('%s', path)\n    return path\n",
            "consume(report_path)\n",
        ),
        (
            "def consume(path, metadata='artifacts/current/unrelated.json'):\n    return metadata.read_text()\n",
            "consume(report_path)\n",
        ),
        (
            "def load_and_read(path):\n    return path\n",
            "load_and_read(report_path)\n",
        ),
    ],
)
def test_imported_wrapper_does_not_attribute_unrelated_parameter_reads_to_report_path(
    tmp_path: Path,
    helper_source: str,
    call_source: str,
) -> None:
    root = _minimal_observer_repository(tmp_path)
    _write(root, "src/investment_orchestrator/helper.py", helper_source)
    function_name = "load_and_read" if helper_source.startswith("def load_and_read") else "consume"
    _write(
        root,
        "src/investment_orchestrator/consumer.py",
        f"from investment_orchestrator.helper import {function_name}\n"
        "report_path = 'artifacts/target_architecture/report_only/ltetf_01/reports/report.json'\n"
        + call_source,
    )

    evidence = gap.collect_repository_evidence(root)
    assert evidence.inventory.report_artifact_readers == ()
    assert gap.build_gap_report(root)["summary_counts"]


@pytest.mark.parametrize(
    ("helper_source", "consumer_source"),
    [
        (
            "def consume(path):\n    return path.read_text()\n",
            "from investment_orchestrator.helper import consume\n"
            "report_path = 'artifacts/target_architecture/report_only/ltetf_01/reports/report.json'\n"
            "arguments = (report_path,)\n"
            "consume(*arguments)\n",
        ),
        (
            "def consume(*, path):\n    return path.read_text()\n",
            "from investment_orchestrator.helper import consume\n"
            "report_path = 'artifacts/target_architecture/report_only/ltetf_01/reports/report.json'\n"
            "arguments = {'path': report_path}\n"
            "consume(**arguments)\n",
        ),
        (
            "def consume(path):\n    return path.read_text()\n",
            "from investment_orchestrator.helper import consume\n"
            "report_path = 'artifacts/target_architecture/report_only/ltetf_01/reports/report.json'\n"
            "consume(report_path, path=report_path)\n",
        ),
        (
            "def consume(path):\n    return path.read_text()\n",
            "from investment_orchestrator.helper import consume\n"
            "report_path = 'artifacts/target_architecture/report_only/ltetf_01/reports/report.json'\n"
            "consume(unexpected=report_path)\n",
        ),
        (
            "def consume(path):\n    return path.read_text()\n",
            "from investment_orchestrator.helper import consume\n"
            "report_path = 'artifacts/target_architecture/report_only/ltetf_01/reports/report.json'\n"
            "consume(report_path, 'extra')\n",
        ),
        (
            "def consume(path):\n    return path.read_text()\n",
            "from investment_orchestrator.helper import consume as wrapper\n"
            "report_path = 'artifacts/target_architecture/report_only/ltetf_01/reports/report.json'\n"
            "wrapper = dynamic_wrapper\n"
            "wrapper(report_path)\n",
        ),
        (
            "def level_two(path):\n    return path.read_text()\ndef level_one(path):\n    return level_two(path)\ndef consume(path):\n    return level_one(path)\n",
            "from investment_orchestrator.helper import consume\n"
            "report_path = 'artifacts/target_architecture/report_only/ltetf_01/reports/report.json'\n"
            "consume(report_path)\n",
        ),
    ],
)
def test_relevant_unresolved_wrapper_bindings_fail_closed_without_publication(
    tmp_path: Path,
    helper_source: str,
    consumer_source: str,
) -> None:
    root = _minimal_observer_repository(tmp_path)
    _write(root, "src/investment_orchestrator/helper.py", helper_source)
    _write(root, "src/investment_orchestrator/consumer.py", consumer_source)
    with pytest.raises(gap.ObserverIntegrityError, match="CONSUMER_INVENTORY_INCOMPLETE"):
        gap.build_and_write_gap_report(root)
    assert not (root / gap.REPORT_NAMESPACE_RELATIVE_PATH).exists()


@pytest.mark.parametrize(
    ("helper_source", "consumer_source"),
    [
        (
            "def consume(required_metadata, path):\n    return path.read_text()\n",
            "from investment_orchestrator.helper import consume\n"
            "report_path = 'artifacts/target_architecture/report_only/ltetf_01/reports/report.json'\n"
            "consume(path=report_path)\n",
        ),
        (
            "def consume(path, required_metadata):\n    return path.read_text()\n",
            "from investment_orchestrator.helper import consume\n"
            "report_path = 'artifacts/target_architecture/report_only/ltetf_01/reports/report.json'\n"
            "consume(report_path)\n",
        ),
        (
            "def consume(path, *, required_mode):\n    return path.read_text()\n",
            "from investment_orchestrator.helper import consume\n"
            "report_path = 'artifacts/target_architecture/report_only/ltetf_01/reports/report.json'\n"
            "consume(report_path)\n",
        ),
        (
            "def consume(first, second, path):\n    return path.read_text()\n",
            "from investment_orchestrator.helper import consume\n"
            "report_path = 'artifacts/target_architecture/report_only/ltetf_01/reports/report.json'\n"
            "consume(path=report_path)\n",
        ),
    ],
)
def test_relevant_missing_required_imported_wrapper_parameters_fail_closed_without_overwrite(
    tmp_path: Path,
    helper_source: str,
    consumer_source: str,
) -> None:
    root = _minimal_observer_repository(tmp_path)
    previous = gap.build_and_write_gap_report(root)
    original = previous.read_bytes()
    _write(root, "src/investment_orchestrator/helper.py", helper_source)
    _write(root, "src/investment_orchestrator/consumer.py", consumer_source)

    with pytest.raises(gap.ObserverIntegrityError, match="CONSUMER_INVENTORY_INCOMPLETE"):
        gap.build_and_write_gap_report(root)
    assert previous.read_bytes() == original


def test_defaulted_imported_wrapper_parameter_remains_valid_and_isolated(tmp_path: Path) -> None:
    root = _minimal_observer_repository(tmp_path)
    _write(
        root,
        "src/investment_orchestrator/helper.py",
        "def consume(path, metadata='default'):\n    return path\n",
    )
    _write(
        root,
        "src/investment_orchestrator/consumer.py",
        "from investment_orchestrator.helper import consume\n"
        "report_path = 'artifacts/target_architecture/report_only/ltetf_01/reports/report.json'\n"
        "consume(report_path)\n",
    )

    evidence = gap.collect_repository_evidence(root)
    assert evidence.inventory.report_artifact_readers == ()
    assert gap.build_gap_report(root)["summary_counts"]


@pytest.mark.parametrize(
    ("metadata", "expected_policy_consumer"),
    [
        ("'prerequisite_catalog_identity_sha256'", False),
        ("'inputs/current/ltetf_operator_mandate.json'", True),
    ],
)
def test_imported_wrapper_keeps_report_and_other_relevant_parameter_flows_separate(
    tmp_path: Path,
    metadata: str,
    expected_policy_consumer: bool,
) -> None:
    root = _minimal_observer_repository(tmp_path)
    _write(
        root,
        "src/investment_orchestrator/helper.py",
        "def consume(path, metadata):\n    return metadata.read_text()\n",
    )
    _write(
        root,
        "src/investment_orchestrator/consumer.py",
        "from investment_orchestrator.helper import consume\n"
        "report_path = 'artifacts/target_architecture/report_only/ltetf_01/reports/report.json'\n"
        f"observer_metadata = {metadata}\n"
        "consume(report_path, observer_metadata)\n",
    )

    evidence = gap.collect_repository_evidence(root)
    assert evidence.inventory.report_artifact_readers == ()
    assert bool(evidence.inventory.policy_artifact_consumers) is expected_policy_consumer
    assert gap.build_gap_report(root)["summary_counts"]


def test_parameter_specific_local_alias_does_not_taint_report_path(tmp_path: Path) -> None:
    root = _minimal_observer_repository(tmp_path)
    _write(
        root,
        "src/investment_orchestrator/helper.py",
        "def consume(metadata, path):\n    target = metadata\n    return target.read_text()\n",
    )
    _write(
        root,
        "src/investment_orchestrator/consumer.py",
        "from investment_orchestrator.helper import consume\n"
        "report_path = 'artifacts/target_architecture/report_only/ltetf_01/reports/report.json'\n"
        "consume('artifacts/current/unrelated.json', report_path)\n",
    )

    evidence = gap.collect_repository_evidence(root)
    assert evidence.inventory.report_artifact_readers == ()
    assert gap.build_gap_report(root)["summary_counts"]


def _assert_relevant_inventory_failure_preserves_report(
    root: Path,
    previous: Path,
    *,
    inventory_is_incomplete: bool = True,
) -> None:
    """Assert a closed inventory failure cannot certify or replace a report."""
    original = previous.read_bytes()
    if inventory_is_incomplete:
        with pytest.raises(gap.ObserverIntegrityError, match="CONSUMER_INVENTORY_INCOMPLETE"):
            gap.collect_repository_evidence(root)
    else:
        evidence = gap.collect_repository_evidence(root)
        assert evidence.inventory.report_artifact_readers
    with pytest.raises(gap.ObserverIntegrityError, match="CONSUMER_INVENTORY_INCOMPLETE"):
        gap.build_and_write_gap_report(root)
    assert previous.read_bytes() == original
    assert tuple(previous.parent.glob("*.json")) == (previous,)


def _write_relevant_inventory_failure(
    root: Path,
    relative_path: str,
    content: str,
) -> Path:
    previous = gap.build_and_write_gap_report(root)
    _write(root, relative_path, content)
    return previous


@pytest.mark.parametrize(
    "source",
    [
        "report_path = 'artifacts/target_architecture/report_only/ltetf_01/reports/report.json'\nunknown_callable(report_path)\n",
        "report_path = 'artifacts/target_architecture/report_only/ltetf_01/reports/report.json'\nunknown_object.consume(report_path)\n",
        "report_path = 'artifacts/target_architecture/report_only/ltetf_01/reports/report.json'\nfactory()(report_path)\n",
        "report_path = 'artifacts/target_architecture/report_only/ltetf_01/reports/report.json'\nhandlers['consumer'](report_path)\n",
        "report_path = 'artifacts/target_architecture/report_only/ltetf_01/reports/report.json'\n(lambda value: value)(report_path)\n",
        "report_path = 'artifacts/target_architecture/report_only/ltetf_01/reports/report.json'\n(lambda: report_path.read_text())()\n",
        "report_path = 'artifacts/target_architecture/report_only/ltetf_01/reports/report.json'\nhandlers[report_path]()\n",
        "report_path = 'artifacts/target_architecture/report_only/ltetf_01/reports/report.json'\nreport_path.consume()\n",
        "def outer():\n"
        "    def consume(path):\n"
        "        return path.read_text()\n"
        "report_path = 'artifacts/target_architecture/report_only/ltetf_01/reports/report.json'\n"
        "consume(report_path)\n",
    ],
    ids=(
        "unknown-name",
        "unknown-attribute",
        "call-result",
        "subscript",
        "lambda",
        "captured-lambda",
        "relevant-subscript",
        "relevant-receiver",
        "nested-definition-unavailable",
    ),
)
def test_relevant_unknown_and_unsupported_callables_fail_closed_without_publication(
    tmp_path: Path,
    source: str,
) -> None:
    root = _minimal_observer_repository(tmp_path)
    previous = _write_relevant_inventory_failure(
        root, "src/investment_orchestrator/consumer.py", source
    )
    _assert_relevant_inventory_failure_preserves_report(root, previous)


@pytest.mark.parametrize(
    "source",
    [
        "def consume(metadata):\n    return metadata\n"
        "report_path = 'artifacts/target_architecture/report_only/ltetf_01/reports/report.json'\n"
        "consume('metadata', report_path)\n",
        "def consume(metadata):\n    return metadata\n"
        "report_path = 'artifacts/target_architecture/report_only/ltetf_01/reports/report.json'\n"
        "consume(metadata='x', report_path=report_path)\n",
        "def consume(path, /):\n    return path\n"
        "report_path = 'artifacts/target_architecture/report_only/ltetf_01/reports/report.json'\n"
        "consume(path=report_path)\n",
        "def consume(path):\n    return path\n"
        "report_path = 'artifacts/target_architecture/report_only/ltetf_01/reports/report.json'\n"
        "consume(report_path, path=report_path)\n",
        "def consume(path, required_metadata):\n    return path\n"
        "report_path = 'artifacts/target_architecture/report_only/ltetf_01/reports/report.json'\n"
        "consume(report_path)\n",
        "def consume(path, *, required_mode):\n    return path\n"
        "report_path = 'artifacts/target_architecture/report_only/ltetf_01/reports/report.json'\n"
        "consume(report_path)\n",
    ],
    ids=(
        "excess-positional",
        "unknown-keyword",
        "positional-only-keyword",
        "duplicate-binding",
        "missing-positional",
        "missing-keyword-only",
    ),
)
def test_relevant_invalid_local_call_arguments_fail_closed_without_publication(
    tmp_path: Path,
    source: str,
) -> None:
    root = _minimal_observer_repository(tmp_path)
    previous = _write_relevant_inventory_failure(
        root, "src/investment_orchestrator/consumer.py", source
    )
    _assert_relevant_inventory_failure_preserves_report(root, previous)


def test_defaulted_local_parameters_remain_valid_and_parameter_specific(tmp_path: Path) -> None:
    root = _minimal_observer_repository(tmp_path)
    _write(
        root,
        "src/investment_orchestrator/consumer.py",
        "def consume(path, metadata='default', *, mode='safe'):\n"
        "    return metadata, mode\n"
        "report_path = 'artifacts/target_architecture/report_only/ltetf_01/reports/report.json'\n"
        "consume(report_path)\n",
    )
    evidence = gap.collect_repository_evidence(root)
    assert evidence.inventory.report_artifact_readers == ()
    assert gap.build_gap_report(root)["summary_counts"]


@pytest.mark.parametrize(
    "definitions",
    [
        "if condition:\n"
        "    def consume(path):\n"
        "        return path.read_text()\n"
        "else:\n"
        "    def consume(path):\n"
        "        return path\n",
        "if condition:\n"
        "    def consume(path):\n"
        "        return path\n"
        "else:\n"
        "    def consume(path):\n"
        "        return path.read_text()\n",
    ],
    ids=("reader-first", "reader-second"),
)
def test_conditional_callable_definitions_are_ambiguous_not_last_definition_wins(
    tmp_path: Path,
    definitions: str,
) -> None:
    root = _minimal_observer_repository(tmp_path)
    previous = gap.build_and_write_gap_report(root)
    _write(
        root,
        "src/investment_orchestrator/consumer.py",
        definitions
        + "report_path = 'artifacts/target_architecture/report_only/ltetf_01/reports/report.json'\n"
        + "consume(report_path)\n",
    )
    _assert_relevant_inventory_failure_preserves_report(root, previous)


def test_statement_ordered_alias_reassignment_is_parameter_specific(tmp_path: Path) -> None:
    stale = _minimal_observer_repository(tmp_path / "stale")
    _write(
        stale,
        "src/investment_orchestrator/consumer.py",
        "def consume(path, metadata):\n"
        "    target = path\n"
        "    target = metadata\n"
        "    return target.read_text()\n"
        "report_path = 'artifacts/target_architecture/report_only/ltetf_01/reports/report.json'\n"
        "consume(report_path, 'artifacts/current/unrelated.json')\n",
    )
    evidence = gap.collect_repository_evidence(stale)
    assert evidence.inventory.report_artifact_readers == ()
    assert gap.build_gap_report(stale)["summary_counts"]

    inverse = _minimal_observer_repository(tmp_path / "inverse")
    inverse_previous = gap.build_and_write_gap_report(inverse)
    _write(
        inverse,
        "src/investment_orchestrator/consumer.py",
        "def consume(path, metadata):\n"
        "    target = metadata\n"
        "    target = path\n"
        "    return target.read_text()\n"
        "report_path = 'artifacts/target_architecture/report_only/ltetf_01/reports/report.json'\n"
        "consume(report_path, 'artifacts/current/unrelated.json')\n",
    )
    _assert_relevant_inventory_failure_preserves_report(
        inverse, inverse_previous, inventory_is_incomplete=False
    )

    branch = _minimal_observer_repository(tmp_path / "branch")
    branch_previous = gap.build_and_write_gap_report(branch)
    _write(
        branch,
        "src/investment_orchestrator/consumer.py",
        "def consume(path, metadata):\n"
        "    target = path\n"
        "    if condition:\n"
        "        target = metadata\n"
        "    return target.read_text()\n"
        "report_path = 'artifacts/target_architecture/report_only/ltetf_01/reports/report.json'\n"
        "consume(report_path, 'artifacts/current/unrelated.json')\n",
    )
    _assert_relevant_inventory_failure_preserves_report(branch, branch_previous)


def test_nested_function_flow_is_invocation_scoped_and_bounded(tmp_path: Path) -> None:
    uninvoked = _minimal_observer_repository(tmp_path / "uninvoked")
    _write(
        uninvoked,
        "src/investment_orchestrator/consumer.py",
        "def consume(path):\n"
        "    def hidden():\n"
        "        return path.read_text()\n"
        "    return path\n"
        "report_path = 'artifacts/target_architecture/report_only/ltetf_01/reports/report.json'\n"
        "consume(report_path)\n",
    )
    evidence = gap.collect_repository_evidence(uninvoked)
    assert evidence.inventory.report_artifact_readers == ()
    assert gap.build_gap_report(uninvoked)["summary_counts"]

    global_uninvoked = _minimal_observer_repository(tmp_path / "global-uninvoked")
    _write(
        global_uninvoked,
        "src/investment_orchestrator/consumer.py",
        "report_path = 'artifacts/target_architecture/report_only/ltetf_01/reports/report.json'\n"
        "def consume():\n"
        "    def hidden():\n"
        "        return report_path.read_text()\n"
        "    return report_path\n"
        "consume()\n",
    )
    evidence = gap.collect_repository_evidence(global_uninvoked)
    assert evidence.inventory.report_artifact_readers == ()
    assert gap.build_gap_report(global_uninvoked)["summary_counts"]

    global_invoked = _minimal_observer_repository(tmp_path / "global-invoked")
    global_invoked_previous = gap.build_and_write_gap_report(global_invoked)
    _write(
        global_invoked,
        "src/investment_orchestrator/consumer.py",
        "report_path = 'artifacts/target_architecture/report_only/ltetf_01/reports/report.json'\n"
        "def consume():\n"
        "    def hidden():\n"
        "        return report_path.read_text()\n"
        "    return hidden()\n"
        "consume()\n",
    )
    _assert_relevant_inventory_failure_preserves_report(
        global_invoked, global_invoked_previous, inventory_is_incomplete=False
    )

    shadowed = _minimal_observer_repository(tmp_path / "shadowed")
    _write(
        shadowed,
        "src/investment_orchestrator/consumer.py",
        "def hidden(path):\n"
        "    return path.read_text()\n"
        "def consume(path):\n"
        "    def hidden():\n"
        "        return path\n"
        "    return hidden()\n"
        "report_path = 'artifacts/target_architecture/report_only/ltetf_01/reports/report.json'\n"
        "consume(report_path)\n",
    )
    evidence = gap.collect_repository_evidence(shadowed)
    assert evidence.inventory.report_artifact_readers == ()
    assert gap.build_gap_report(shadowed)["summary_counts"]

    invoked = _minimal_observer_repository(tmp_path / "invoked")
    invoked_previous = gap.build_and_write_gap_report(invoked)
    _write(
        invoked,
        "src/investment_orchestrator/consumer.py",
        "def consume(path):\n"
        "    def hidden():\n"
        "        return path.read_text()\n"
        "    hidden()\n"
        "report_path = 'artifacts/target_architecture/report_only/ltetf_01/reports/report.json'\n"
        "consume(report_path)\n",
    )
    _assert_relevant_inventory_failure_preserves_report(
        invoked, invoked_previous, inventory_is_incomplete=False
    )

    unavailable = _minimal_observer_repository(tmp_path / "unavailable")
    unavailable_previous = gap.build_and_write_gap_report(unavailable)
    _write(
        unavailable,
        "src/investment_orchestrator/consumer.py",
        "def outer():\n"
        "    def hidden(path):\n"
        "        return path.read_text()\n"
        "def consume(path):\n"
        "    return hidden(path)\n"
        "report_path = 'artifacts/target_architecture/report_only/ltetf_01/reports/report.json'\n"
        "consume(report_path)\n",
    )
    _assert_relevant_inventory_failure_preserves_report(unavailable, unavailable_previous)


@pytest.mark.parametrize(
    "consumer_source",
    [
        "def consume(path):\n"
        "    if condition:\n"
        "        def hidden():\n"
        "            return path.read_text()\n"
        "    else:\n"
        "        def hidden():\n"
        "            return path\n"
        "    hidden()\n"
        "report_path = 'artifacts/target_architecture/report_only/ltetf_01/reports/report.json'\n"
        "consume(report_path)\n",
        "def consume(path):\n"
        "    def first():\n"
        "        def second():\n"
        "            return path.read_text()\n"
        "        return second()\n"
        "    return first()\n"
        "report_path = 'artifacts/target_architecture/report_only/ltetf_01/reports/report.json'\n"
        "consume(report_path)\n",
        "def consume(path):\n"
        "    def first():\n"
        "        return second(path)\n"
        "    def second(value):\n"
        "        return first()\n"
        "    return first()\n"
        "report_path = 'artifacts/target_architecture/report_only/ltetf_01/reports/report.json'\n"
        "consume(report_path)\n",
    ],
    ids=("ambiguous-nested", "second-level", "nested-cycle"),
)
def test_relevant_nested_ambiguity_depth_and_cycles_fail_closed(
    tmp_path: Path,
    consumer_source: str,
) -> None:
    root = _minimal_observer_repository(tmp_path)
    previous = _write_relevant_inventory_failure(
        root, "src/investment_orchestrator/consumer.py", consumer_source
    )
    _assert_relevant_inventory_failure_preserves_report(root, previous)


def test_unrelated_unknown_invalid_and_ambiguous_calls_remain_nonblocking(tmp_path: Path) -> None:
    root = _minimal_observer_repository(tmp_path)
    _write(
        root,
        "src/investment_orchestrator/consumer.py",
        "unknown_callable('artifacts/current/unrelated.json')\n"
        "def consume(value):\n"
        "    return value\n"
        "consume('metadata', 'extra')\n"
        "if condition:\n"
        "    def alternate(value):\n"
        "        return value\n"
        "else:\n"
        "    def alternate(value):\n"
        "        return value\n"
        "alternate('artifacts/current/unrelated.json')\n"
        "def outer(value):\n"
        "    def hidden():\n"
        "        return value.read_text()\n"
        "    return value\n"
        "outer('artifacts/current/unrelated.json')\n"
        "def alias(value):\n"
        "    target = value\n"
        "    if condition:\n"
        "        target = 'other'\n"
        "    return target\n"
        "alias('artifacts/current/unrelated.json')\n",
    )
    evidence = gap.collect_repository_evidence(root)
    assert evidence.inventory.report_artifact_readers == ()
    assert gap.build_gap_report(root)["summary_counts"]


@pytest.mark.parametrize(
    "source",
    [
        "def read_json(path):\n    return path\n",
        "def load_json_file(path):\n    return {'value': path}\n",
        "def consume(path):\n    return path\n",
        "def open(path):\n    return path\n",
    ],
)
def test_local_loader_name_decoys_and_shadowed_open_are_not_reader_sinks(
    tmp_path: Path,
    source: str,
) -> None:
    root = _minimal_observer_repository(tmp_path)
    function_name = source.split("(", 1)[0].removeprefix("def ")
    _write(
        root,
        "src/investment_orchestrator/consumer.py",
        source
        + "report_path = 'artifacts/target_architecture/report_only/ltetf_01/reports/report.json'\n"
        + f"{function_name}(report_path)\n",
    )

    evidence = gap.collect_repository_evidence(root)
    assert evidence.inventory.report_artifact_readers == ()
    assert gap.build_gap_report(root)["summary_counts"]


def test_resolved_repository_shared_loader_with_real_sink_is_a_report_reader(tmp_path: Path) -> None:
    root = _minimal_observer_repository(tmp_path)
    _write(
        root,
        "src/investment_orchestrator/shared_loader.py",
        "import json\ndef load_json_file(path):\n    return json.loads(path.read_text())\n",
    )
    _write(
        root,
        "src/investment_orchestrator/consumer.py",
        "from investment_orchestrator.shared_loader import load_json_file as read\n"
        "report_path = 'artifacts/target_architecture/report_only/ltetf_01/reports/report.json'\n"
        "read(report_path)\n",
    )

    evidence = gap.collect_repository_evidence(root)
    assert evidence.inventory.report_artifact_readers == (
        "src/investment_orchestrator/consumer.py",
    )
    with pytest.raises(gap.ObserverIntegrityError, match="CONSUMER_INVENTORY_INCOMPLETE"):
        gap.build_gap_report(root)


def test_relevant_imported_wrapper_escape_and_unavailable_callee_fail_closed_without_overwrite(
    tmp_path: Path,
) -> None:
    root = _minimal_observer_repository(tmp_path)
    previous = gap.build_and_write_gap_report(root)
    original = previous.read_bytes()
    _write(root, "src/investment_orchestrator/helper.py", "def consume(path):\n    return path.read_text()\n")
    _write(
        root,
        "src/investment_orchestrator/consumer.py",
        "from investment_orchestrator.helper import consume\n"
        "report_path = '../artifacts/target_architecture/report_only/ltetf_01/reports/report.json'\n"
        "consume(report_path)\n",
    )
    with pytest.raises(gap.ObserverIntegrityError, match="CONSUMER_INVENTORY_INCOMPLETE"):
        gap.build_and_write_gap_report(root)
    assert previous.read_bytes() == original

    unavailable = _minimal_observer_repository(tmp_path / "unavailable")
    _write(
        unavailable,
        "src/investment_orchestrator/consumer.py",
        "from investment_orchestrator.missing import consume\n"
        "report_path = 'artifacts/target_architecture/report_only/ltetf_01/reports/report.json'\n"
        "consume(report_path)\n",
    )
    with pytest.raises(gap.ObserverIntegrityError, match="CONSUMER_INVENTORY_INCOMPLETE"):
        gap.build_and_write_gap_report(unavailable)
    assert not (unavailable / gap.REPORT_NAMESPACE_RELATIVE_PATH).exists()


def test_provably_unrelated_variadic_imported_wrappers_do_not_block_inventory(
    tmp_path: Path,
) -> None:
    root = _minimal_observer_repository(tmp_path)
    _write(
        root,
        "src/investment_orchestrator/helper.py",
        "def consume(*items, **values):\n    return items, values\n",
    )
    _write(
        root,
        "src/investment_orchestrator/consumer.py",
        "from investment_orchestrator.helper import consume\n"
        "arguments = ('artifacts/current/unrelated.json',)\n"
        "values = {'path': 'artifacts/current/other.json'}\n"
        "consume(*arguments, **values)\n",
    )
    evidence = gap.collect_repository_evidence(root)
    assert evidence.inventory.report_artifact_readers == ()
    assert gap.build_gap_report(root)["summary_counts"]


def test_unresolved_cyclic_and_unrelated_imported_reader_wrappers_are_classified_conservatively(
    tmp_path: Path,
) -> None:
    root = _minimal_observer_repository(tmp_path / "unresolved")
    _write(
        root,
        "src/investment_orchestrator/consumer.py",
        "from investment_orchestrator.not_local import arbitrary_name\n"
        "report_path = 'artifacts/target_architecture/report_only/ltetf_01/reports/report.json'\n"
        "arbitrary_name(report_path)\n",
    )
    with pytest.raises(gap.ObserverIntegrityError, match="CONSUMER_INVENTORY_INCOMPLETE"):
        gap.build_and_write_gap_report(root)
    assert not (root / gap.REPORT_NAMESPACE_RELATIVE_PATH).exists()

    root = _minimal_observer_repository(tmp_path / "cycle")
    _write(
        root,
        "src/investment_orchestrator/first.py",
        "from investment_orchestrator.second import other\n"
        "def arbitrary_name(path):\n    return other(path)\n",
    )
    _write(
        root,
        "src/investment_orchestrator/second.py",
        "from investment_orchestrator.first import arbitrary_name\n"
        "def other(path):\n    return arbitrary_name(path)\n",
    )
    _write(
        root,
        "src/investment_orchestrator/consumer.py",
        "from investment_orchestrator.first import arbitrary_name\n"
        "report_path = 'artifacts/target_architecture/report_only/ltetf_01/reports/report.json'\n"
        "arbitrary_name(report_path)\n",
    )
    with pytest.raises(gap.ObserverIntegrityError, match="CONSUMER_INVENTORY_INCOMPLETE"):
        gap.build_and_write_gap_report(root)
    assert not (root / gap.REPORT_NAMESPACE_RELATIVE_PATH).exists()

    root = _minimal_observer_repository(tmp_path / "unrelated")
    _write(root, "src/investment_orchestrator/helper.py", "def arbitrary_name(path):\n    return path.read_text()\n")
    _write(
        root,
        "src/investment_orchestrator/consumer.py",
        "from investment_orchestrator.helper import arbitrary_name\n"
        "arbitrary_name('artifacts/current/unrelated.json')\n",
    )
    assert gap.build_gap_report(root)["summary_counts"]


def test_nested_json_file_read_is_resolved_as_a_report_reader(tmp_path: Path) -> None:
    root = _minimal_observer_repository(tmp_path)
    _write(
        root,
        "src/investment_orchestrator/consumer.py",
        "from pathlib import Path\nimport json\njson.loads(Path('artifacts/target_architecture/report_only/ltetf_01/reports/report.json').read_text())\n",
    )
    evidence = gap.collect_repository_evidence(root)
    assert evidence.inventory.report_artifact_readers == (
        "src/investment_orchestrator/consumer.py",
    )
    with pytest.raises(gap.ObserverIntegrityError, match="CONSUMER_INVENTORY_INCOMPLETE"):
        gap.build_gap_report(root)


def test_parse_failure_symlink_and_report_path_escape_fail_closed(tmp_path: Path) -> None:
    root = _minimal_observer_repository(tmp_path)
    _write(root, "src/investment_orchestrator/broken.py", "def broken(:\n")
    with pytest.raises(gap.ObserverIntegrityError, match="CONSUMER_INVENTORY_INCOMPLETE"):
        gap.build_gap_report(root)

    root = _minimal_observer_repository(tmp_path / "escape")
    _write(
        root,
        "src/investment_orchestrator/consumer.py",
        "open('../artifacts/target_architecture/report_only/ltetf_01/reports/x.json')\n",
    )
    with pytest.raises(gap.ObserverIntegrityError, match="CONSUMER_INVENTORY_INCOMPLETE"):
        gap.build_gap_report(root)

    root = _minimal_observer_repository(tmp_path / "symlink")
    target = root / "src/investment_orchestrator/consumer.py"
    target.symlink_to(root / "src/investment_orchestrator/observability/__init__.py")
    with pytest.raises(gap.ObserverIntegrityError, match="CONSUMER_INVENTORY_INCOMPLETE"):
        gap.build_gap_report(root)


def test_output_is_idempotent_never_current_and_conflicting_identity_path_is_not_overwritten(tmp_path: Path) -> None:
    root = _minimal_observer_repository(tmp_path)
    report = gap.build_gap_report(root)
    first = gap.write_gap_report(root, report)
    second = gap.write_gap_report(root, report)
    assert first == second
    assert first.read_bytes() == gap.canonical_gap_report_bytes(report, root=root)
    assert not any(path.name == "current" for path in first.parent.iterdir())

    first.unlink()
    first.write_bytes(b"different")
    with pytest.raises(gap.ObserverIntegrityError, match="REPORT_OUTPUT_CONFLICT"):
        gap.write_gap_report(root, report)
    assert first.read_bytes() == b"different"


@pytest.mark.parametrize(
    "failure",
    [
        "catalog",
        "schema",
        "inventory",
        "evidence",
        "identity",
    ],
)
def test_integrity_failures_publish_no_partial_report(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure: str) -> None:
    root = _minimal_observer_repository(tmp_path)
    if failure == "catalog":
        def invalid_catalog() -> None:
            raise CatalogIntegrityError("invalid")
        monkeypatch.setattr(gap, "validate_catalog", invalid_catalog)
        expected = "OBSERVER_CATALOG_INVALID"
        operation = lambda: gap.build_and_write_gap_report(root)
    elif failure == "schema":
        _write(root, "schemas/ltetf_target_architecture_gap_report.schema.json", '{"type": 7}')
        expected = "OBSERVER_SCHEMA_INVALID"
        operation = lambda: gap.build_and_write_gap_report(root)
    elif failure == "inventory":
        _write(
            root,
            "src/investment_orchestrator/consumer.py",
            "eval(\"open('artifacts/target_architecture/report_only/ltetf_01/reports/x.json')\")\n",
        )
        expected = "CONSUMER_INVENTORY_INCOMPLETE"
        operation = lambda: gap.build_and_write_gap_report(root)
    elif failure == "evidence":
        def invalid_evidence(root_value: Path) -> gap.RepositoryEvidence:
            raise gap.ObserverIntegrityError("EVIDENCE_COLLECTION_FAILED")
        monkeypatch.setattr(gap, "collect_repository_evidence", invalid_evidence)
        expected = "EVIDENCE_COLLECTION_FAILED"
        operation = lambda: gap.build_and_write_gap_report(root)
    else:
        report = gap.build_gap_report(root)
        report["content_identity_sha256"] = "0" * 64
        expected = "IDENTITY_COMPUTATION_FAILED"
        operation = lambda: gap.write_gap_report(root, report)
    with pytest.raises(gap.ObserverIntegrityError, match=expected):
        operation()
    assert not (root / gap.REPORT_NAMESPACE_RELATIVE_PATH).exists()


def test_canonical_bound_failure_has_no_report_and_cli_is_standalone(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = _minimal_observer_repository(tmp_path)
    report = gap.build_gap_report(root)
    monkeypatch.setattr(
        gap,
        "_canonical_json_bytes",
        lambda record, *, maximum: (_ for _ in ()).throw(gap.ObserverIntegrityError("CANONICAL_BOUND_EXCEEDED")),
    )
    with pytest.raises(gap.ObserverIntegrityError, match="CANONICAL_BOUND_EXCEEDED"):
        gap.write_gap_report(root, report)
    assert not (root / gap.REPORT_NAMESPACE_RELATIVE_PATH).exists()

    root = _minimal_observer_repository(tmp_path / "cli")
    environment = dict(os.environ)
    environment["PYTHONPATH"] = str(repo_root() / "src")
    completed = subprocess.run(
        [sys.executable, "-m", "investment_orchestrator.cli.observe_ltetf_target_architecture_gaps"],
        cwd=root,
        env=environment,
        check=False,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0
    assert completed.stderr == ""
    output = completed.stdout.strip()
    assert output.startswith("artifacts/target_architecture/report_only/ltetf_01/reports/")
    assert (root / output).is_file()


def test_negative_proofs_are_inventory_backed_and_weekly_baseline_is_conservative() -> None:
    report = gap.build_gap_report(repo_root())
    for check_id in (
        "dormant_p4a_runtime_isolation",
        "broker_live_execution_absence",
        "automatic_weekly_llm_absence",
    ):
        check = _check(report, check_id)
        assert check["status"] == "PROVEN_PRESENT"
        assert "repository_inventory:production" in check["evidence_ids"]
        outcomes = {outcome["predicate_id"]: outcome for outcome in check["predicate_outcomes"]}
        assert all(outcomes[predicate]["satisfied"] for predicate in ("P27", "P28", "P38", "P39"))
    weekly = _check(report, "fail_closed_weekly_baseline")
    assert weekly["status"] != "PROVEN_PRESENT"
    assert "LEGACY_SEMANTICS_MISMATCH" not in weekly["reason_codes"]
    outcomes = {outcome["predicate_id"]: outcome for outcome in weekly["predicate_outcomes"]}
    assert not outcomes["P02"]["satisfied"]


def test_broker_operation_symbol_is_a_direct_negative_proof_contradiction(tmp_path: Path) -> None:
    root = _minimal_observer_repository(tmp_path)
    _write(
        root,
        "src/investment_orchestrator/broker_operation.py",
        "def submit_order(order):\n    return order\n",
    )
    report = gap.build_gap_report(root)
    broker = _check(report, "broker_live_execution_absence")
    assert broker["status"] == "CONTRADICTORY"
    assert broker["reason_codes"] == ["PROHIBITED_CONSUMER_PRESENT"]
    assert broker["evidence_ids"] == ["repository_inventory:production"]


def test_broker_negative_proof_never_supplies_atomic_package_or_pointer_facts() -> None:
    evidence = gap.collect_repository_evidence(repo_root())
    broker = next(item for item in CATALOG if item.check_id == "broker_live_execution_absence")
    observation = gap.derive_prerequisite_observation(broker, evidence)
    assert "P31" not in broker.required_proof_predicates
    assert not observation.facts.atomic_manual_order_package_proven
    assert not observation.facts.atomic_current_package_pointer_proven

    complete = _complete_facts()
    package = _assessment("atomic_manual_order_package", complete)
    pointer = _assessment("atomic_current_package_pointer", complete)
    assert package.status is ReadinessStatus.PROVEN_PRESENT
    assert pointer.status is ReadinessStatus.PROVEN_PRESENT

    no_package = replace(complete, atomic_manual_order_package_proven=False)
    assert _assessment("atomic_manual_order_package", no_package).status is not ReadinessStatus.PROVEN_PRESENT
    no_pointer = replace(complete, atomic_current_package_pointer_proven=False)
    assert _assessment("atomic_current_package_pointer", no_pointer).status is not ReadinessStatus.PROVEN_PRESENT
    assert _assessment("atomic_current_package_pointer", complete, dependencies_proven=False).status is ReadinessStatus.PARTIAL
