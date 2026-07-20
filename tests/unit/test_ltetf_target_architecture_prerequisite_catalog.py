"""Permanent contract tests for the frozen LTETF-01 prerequisite catalog."""

from __future__ import annotations

from collections import Counter
from dataclasses import replace

import pytest

from investment_orchestrator.observability.ltetf_target_architecture_prerequisite_catalog import (
    ALLOWED_OWNER_ACTOR_PAIRS,
    CATALOG,
    CATALOG_IDENTITY_SHA256,
    DIMENSION_COUNTS,
    EXPECTED_CHECK_IDS,
    LLM_RUNTIME_ACTOR_CHECK_IDS,
    MAX_CATALOG_CANONICAL_BYTES,
    NOT_APPLICABLE_RUNTIME_ACTOR_CHECK_IDS,
    OWNERSHIP_PAIR_COUNTS,
    AuthorityEffect,
    CatalogIntegrityError,
    ContractOwner,
    RuntimeActor,
    canonical_catalog_bytes,
    catalog_check_to_dict,
    catalog_identity_sha256,
    validate_catalog,
)


def test_catalog_has_exact_frozen_check_order_and_dimension_counts() -> None:
    assert tuple(check.check_id for check in CATALOG) == EXPECTED_CHECK_IDS
    assert len(CATALOG) == 81
    assert Counter(check.dimension_id for check in CATALOG) == DIMENSION_COUNTS
    validate_catalog()


def test_catalog_entries_carry_every_frozen_contract_field() -> None:
    expected_fields = {
        "check_id",
        "dimension_id",
        "title",
        "contract_owner",
        "runtime_actor",
        "target_requirement",
        "allowed_evidence_kinds",
        "required_proof_predicates",
        "optional_supporting_evidence",
        "disqualifying_conditions",
        "status_decision_table",
        "reason_codes_by_status",
        "blocker_type",
        "blocker_code",
        "dependency_check_ids",
        "authority_effect",
    }
    assert all(set(catalog_check_to_dict(check)) == expected_fields for check in CATALOG)
    assert all(check.authority_effect is AuthorityEffect.NONE for check in CATALOG)
    assert all("canonical_owner" not in catalog_check_to_dict(check) for check in CATALOG)


def test_catalog_has_exact_corrected_ownership_pairs_and_totals() -> None:
    ownership_counts = Counter(
        (check.contract_owner, check.runtime_actor) for check in CATALOG
    )
    assert ownership_counts == OWNERSHIP_PAIR_COUNTS
    assert set(ownership_counts) == ALLOWED_OWNER_ACTOR_PAIRS
    assert {
        check.check_id for check in CATALOG if check.runtime_actor is RuntimeActor.LLM
    } == LLM_RUNTIME_ACTOR_CHECK_IDS
    assert {
        check.check_id
        for check in CATALOG
        if check.runtime_actor is RuntimeActor.NOT_APPLICABLE
    } == NOT_APPLICABLE_RUNTIME_ACTOR_CHECK_IDS
    owners = {check.check_id: (check.contract_owner, check.runtime_actor) for check in CATALOG}
    assert owners["new_buy_eligibility_evaluation"] == (
        ContractOwner.DETERMINISTIC_CODE,
        RuntimeActor.DETERMINISTIC_CODE,
    )
    assert owners["sell_lot_eligibility_evaluation"] == (
        ContractOwner.DETERMINISTIC_CODE,
        RuntimeActor.DETERMINISTIC_CODE,
    )


def test_catalog_validation_rejects_duplicate_unknown_dependency_cycle_and_forbidden_pair() -> None:
    duplicate = (replace(CATALOG[0], check_id=CATALOG[1].check_id),) + CATALOG[1:]
    with pytest.raises(CatalogIntegrityError, match="CATALOG_DUPLICATE_CHECK_ID"):
        validate_catalog(duplicate)

    unknown_dependency = (replace(CATALOG[0], dependency_check_ids=("unknown_check",)),) + CATALOG[1:]
    with pytest.raises(CatalogIntegrityError, match="CATALOG_UNKNOWN_DEPENDENCY"):
        validate_catalog(unknown_dependency)

    duplicate_dependency = CATALOG[:2] + (
        replace(
            CATALOG[2],
            dependency_check_ids=("investment_horizon_policy", "investment_horizon_policy"),
        ),
    ) + CATALOG[3:]
    with pytest.raises(CatalogIntegrityError, match="CATALOG_DUPLICATE_DEPENDENCY"):
        validate_catalog(duplicate_dependency)

    self_dependency = (replace(CATALOG[0], dependency_check_ids=(CATALOG[0].check_id,)),) + CATALOG[1:]
    with pytest.raises(CatalogIntegrityError, match="CATALOG_DEPENDENCY_CYCLE"):
        validate_catalog(self_dependency)

    cycle = (replace(CATALOG[0], dependency_check_ids=(CATALOG[1].check_id,)),) + (
        replace(CATALOG[1], dependency_check_ids=(CATALOG[0].check_id,)),
    ) + CATALOG[2:]
    with pytest.raises(CatalogIntegrityError, match="CATALOG_DEPENDENCY_CYCLE"):
        validate_catalog(cycle)

    forbidden_pair = (replace(CATALOG[0], contract_owner=ContractOwner.DETERMINISTIC_CODE, runtime_actor=RuntimeActor.OPERATOR),) + CATALOG[1:]
    with pytest.raises(CatalogIntegrityError, match="CATALOG_OWNERSHIP_PAIR_INVALID"):
        validate_catalog(forbidden_pair)

    unknown_actor = (replace(CATALOG[0], runtime_actor="unknown"),) + CATALOG[1:]  # type: ignore[arg-type]
    with pytest.raises(CatalogIntegrityError, match="CATALOG_ENUM_TYPE_INVALID"):
        validate_catalog(unknown_actor)


def test_catalog_identity_is_stable_and_binds_entry_content_after_validation() -> None:
    assert catalog_identity_sha256() == CATALOG_IDENTITY_SHA256
    assert CATALOG_IDENTITY_SHA256 == "b5126ecb9d3753af5ac7dcb40d7712eeb3234bdaff609c42d65d9e957dc8d71e"
    assert 0 < len(canonical_catalog_bytes()) <= MAX_CATALOG_CANONICAL_BYTES
    changed_title = (replace(CATALOG[0], title="Changed title"),) + CATALOG[1:]
    assert catalog_identity_sha256(changed_title) != CATALOG_IDENTITY_SHA256
    malformed = (replace(CATALOG[0], required_proof_predicates=("UNKNOWN",)),) + CATALOG[1:]
    with pytest.raises(CatalogIntegrityError, match="CATALOG_PROOF_PREDICATE_INVALID"):
        canonical_catalog_bytes(malformed)


def test_broker_negative_profile_has_no_atomic_modifier_and_atomicity_is_check_scoped() -> None:
    predicates = {check.check_id: check.required_proof_predicates for check in CATALOG}
    assert predicates["broker_live_execution_absence"] == (
        "P01", "P27", "P28", "P38", "P39",
    )
    assert predicates["atomic_manual_order_package"][-1] == "P31"
    assert predicates["atomic_current_package_pointer"][-1] == "P31"
    old_broker_binding = tuple(
        replace(
            check,
            required_proof_predicates=(*check.required_proof_predicates, "P31"),
        )
        if check.check_id == "broker_live_execution_absence"
        else check
        for check in CATALOG
    )
    assert catalog_identity_sha256(old_broker_binding) != CATALOG_IDENTITY_SHA256


def test_named_modifier_assignment_inventory_is_stable() -> None:
    predicates = {check.check_id: set(check.required_proof_predicates) for check in CATALOG}
    assert {check_id for check_id, values in predicates.items() if "P25" in values} == {
        "trusted_evaluation_clock",
    }
    assert {check_id for check_id, values in predicates.items() if "P26" in values} == {
        "evidence_selection_policy",
        "evidence_packet_bounds",
    }
    assert {check_id for check_id, values in predicates.items() if "P30" in values} == {
        "analyst_invocation_grounding_boundary",
    }
    assert {check_id for check_id, values in predicates.items() if "P31" in values} == {
        "atomic_manual_order_package",
        "atomic_current_package_pointer",
    }
    assert {check_id for check_id, values in predicates.items() if "P32" in values} == {
        "postcompile_final_safety",
    }
    assert {check_id for check_id, values in predicates.items() if "P33" in values} == {
        "deterministic_evidence_sufficiency",
    }


def test_catalog_defined_but_currently_unused_predicates_remain_identity_bound() -> None:
    required = {predicate for check in CATALOG for predicate in check.required_proof_predicates}
    assert {"P20", "P23", "P34"}.isdisjoint(required)
