"""Frozen LTETF-01 prerequisite catalog.

The catalog is intentionally declarative.  It describes migration-readiness
prerequisites only; no entry has permission, routing, publication, or order
authority.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import hashlib
import json
from typing import Final


CATALOG_VERSION: Final = "ltetf_target_architecture_prerequisite_catalog_v1"
CATALOG_IDENTITY_DOMAIN: Final = b"ltetf_target_architecture_prerequisite_catalog_v1\0"
MAX_CATALOG_CANONICAL_BYTES: Final = 524_288


class CatalogIntegrityError(RuntimeError):
    """Raised when the frozen prerequisite catalog is internally invalid."""


class EvidenceKind(str, Enum):
    SCHEMA = "schema"
    VALIDATOR = "validator"
    PRODUCTION_MODULE = "production_module"
    TEST_CONTRACT = "test_contract"
    OPERATOR_INPUT = "operator_input"
    RUNTIME_ARTIFACT = "runtime_artifact"
    REPOSITORY_CONFIG = "repository_config"
    DRAFT_DOCUMENT = "draft_document"
    REPOSITORY_INVENTORY = "repository_inventory"


class ContractOwner(str, Enum):
    DETERMINISTIC_CODE = "deterministic_code"
    OPERATOR_AND_DETERMINISTIC_VALIDATION = "operator_and_deterministic_validation"


class RuntimeActor(str, Enum):
    OPERATOR = "operator"
    LLM = "llm"
    DETERMINISTIC_CODE = "deterministic_code"
    NOT_APPLICABLE = "not_applicable"


class AuthorityEffect(str, Enum):
    NONE = "none"


class ReadinessStatus(str, Enum):
    PROVEN_PRESENT = "PROVEN_PRESENT"
    PRESENT_UNACCEPTED = "PRESENT_UNACCEPTED"
    DRAFT_ONLY = "DRAFT_ONLY"
    PARTIAL = "PARTIAL"
    MISSING = "MISSING"
    UNRESOLVED_OPERATOR_POLICY = "UNRESOLVED_OPERATOR_POLICY"
    UNRESOLVED_CONTRACT = "UNRESOLVED_CONTRACT"
    UNAVAILABLE_RUNTIME_DATA = "UNAVAILABLE_RUNTIME_DATA"
    CONTRADICTORY = "CONTRADICTORY"


class BlockerType(str, Enum):
    OPERATOR_POLICY = "operator_policy"
    CONTRACT = "contract"
    RUNTIME_DATA = "runtime_data"
    IMPLEMENTATION = "implementation"
    CONTRADICTION = "contradiction"


DIMENSION_IDS: Final = (
    "operator_mandate",
    "evidence_and_grounding",
    "structured_portfolio_state",
    "llm_analyst_and_signal_contract",
    "portfolio_construction",
    "semantic_audit_and_resolution",
    "approval_order_and_migration_safety",
)

DIMENSION_COUNTS: Final = {
    "operator_mandate": 11,
    "evidence_and_grounding": 15,
    "structured_portfolio_state": 7,
    "llm_analyst_and_signal_contract": 9,
    "portfolio_construction": 12,
    "semantic_audit_and_resolution": 9,
    "approval_order_and_migration_safety": 18,
}

# This separate immutable sequence is deliberately not derived from ``CATALOG``.
# It is the frozen public ordering contract used to reject reordered catalog
# construction before identity computation.
EXPECTED_CHECK_IDS: Final = (
    "investment_horizon_policy",
    "risk_drawdown_policy",
    "strategic_allocation_policy",
    "liquidity_cash_policy",
    "contribution_withdrawal_policy",
    "turnover_policy",
    "tax_realization_policy",
    "etf_product_policy",
    "concentration_overlap_policy",
    "sell_lot_policy",
    "manual_live_state_process_policy",
    "source_policy_contract",
    "authorized_source_inventory",
    "evidence_provenance_contract",
    "evidence_timestamp_semantics",
    "trusted_evaluation_clock",
    "field_level_freshness_contract",
    "evidence_conflict_gap_contract",
    "structured_market_metrics",
    "structured_scheduled_events",
    "evidence_selection_policy",
    "evidence_packet_bounds",
    "deterministic_evidence_sufficiency",
    "prior_thesis_continuity",
    "review_trigger_contract",
    "prompt_envelope_identity",
    "holdings_state_contract",
    "cash_state_contract",
    "tax_lot_state_contract",
    "open_order_state_contract",
    "account_metadata_contract",
    "manual_order_state_contract",
    "portfolio_snapshot_identity",
    "analyst_disposition_vocabulary",
    "analyst_required_fields_contract",
    "analyst_prohibited_authority_contract",
    "analyst_evidence_reference_contract",
    "analyst_generation_provenance",
    "analyst_parser_validator",
    "analyst_semantic_identity",
    "analyst_invocation_grounding_boundary",
    "signal_mapping_contract",
    "portfolio_objective_contract",
    "portfolio_hard_constraints",
    "rebalance_band_policy",
    "no_trade_band_policy",
    "hysteresis_cooldown_policy",
    "after_cost_improvement_hurdle",
    "transaction_cost_policy",
    "liquidity_minimum_trade_policy",
    "new_buy_eligibility_evaluation",
    "sell_lot_eligibility_evaluation",
    "tax_constraint_enforcement",
    "deterministic_portfolio_constructor",
    "semantic_finding_vocabulary",
    "semantic_audit_prohibited_authority_contract",
    "semantic_audit_parser_validator",
    "semantic_finding_identity",
    "immutable_finding_ledger",
    "semantic_resolution_table",
    "bounded_recomputation_policy",
    "semantic_recomputation_lineage",
    "repeated_finding_policy",
    "resolved_proposal_review_package",
    "proposal_approval_intent_contract",
    "approval_authentication_threat_model",
    "approval_expiry_policy",
    "price_drift_recompilation_tolerance",
    "precompile_eligibility_contract",
    "deterministic_order_compiler",
    "complete_buy_order_validator",
    "complete_sell_order_validator",
    "postcompile_final_safety",
    "atomic_manual_order_package",
    "atomic_current_package_pointer",
    "artifact_retention_policy",
    "dormant_p4a_runtime_isolation",
    "broker_live_execution_absence",
    "fail_closed_weekly_baseline",
    "automatic_weekly_llm_absence",
    "target_orchestration_contract",
)

STATUS_ROLLUP_PRECEDENCE: Final = (
    ReadinessStatus.CONTRADICTORY,
    ReadinessStatus.UNRESOLVED_OPERATOR_POLICY,
    ReadinessStatus.UNRESOLVED_CONTRACT,
    ReadinessStatus.MISSING,
    ReadinessStatus.PARTIAL,
    ReadinessStatus.UNAVAILABLE_RUNTIME_DATA,
    ReadinessStatus.DRAFT_ONLY,
    ReadinessStatus.PRESENT_UNACCEPTED,
    ReadinessStatus.PROVEN_PRESENT,
)

ALLOWED_OWNER_ACTOR_PAIRS: Final = frozenset(
    {
        (ContractOwner.DETERMINISTIC_CODE, RuntimeActor.DETERMINISTIC_CODE),
        (ContractOwner.DETERMINISTIC_CODE, RuntimeActor.LLM),
        (ContractOwner.DETERMINISTIC_CODE, RuntimeActor.NOT_APPLICABLE),
        (
            ContractOwner.OPERATOR_AND_DETERMINISTIC_VALIDATION,
            RuntimeActor.OPERATOR,
        ),
        (
            ContractOwner.OPERATOR_AND_DETERMINISTIC_VALIDATION,
            RuntimeActor.DETERMINISTIC_CODE,
        ),
    }
)

LLM_RUNTIME_ACTOR_CHECK_IDS: Final = frozenset(
    {
        "analyst_disposition_vocabulary",
        "analyst_required_fields_contract",
        "analyst_evidence_reference_contract",
        "semantic_finding_vocabulary",
    }
)

NOT_APPLICABLE_RUNTIME_ACTOR_CHECK_IDS: Final = frozenset(
    {
        "dormant_p4a_runtime_isolation",
        "broker_live_execution_absence",
        "automatic_weekly_llm_absence",
    }
)

OWNERSHIP_PAIR_COUNTS: Final = {
    (ContractOwner.DETERMINISTIC_CODE, RuntimeActor.DETERMINISTIC_CODE): 41,
    (ContractOwner.DETERMINISTIC_CODE, RuntimeActor.LLM): 4,
    (ContractOwner.DETERMINISTIC_CODE, RuntimeActor.NOT_APPLICABLE): 3,
    (
        ContractOwner.OPERATOR_AND_DETERMINISTIC_VALIDATION,
        RuntimeActor.OPERATOR,
    ): 25,
    (
        ContractOwner.OPERATOR_AND_DETERMINISTIC_VALIDATION,
        RuntimeActor.DETERMINISTIC_CODE,
    ): 8,
}


PROOF_PREDICATES: Final = {
    "P01": "repository-local regular non-symlink evidence",
    "P02": "exact supported contract/schema version",
    "P03": "closed and bounded schema",
    "P04": "valid fixtures accepted and invalid fixtures rejected",
    "P05": "exact production validator exists",
    "P06": "validator is reached by its allowed producer/consumer",
    "P07": "exact production producer exists",
    "P08": "actual production consumers equal the allowed set",
    "P09": "permanent tests support the contract",
    "P10": "current runtime artifact validates",
    "P11": "artifact is current, not fixture/archive/history",
    "P12": "machine-readable policy candidate validates",
    "P13": "acceptance_state is exact accepted value",
    "P14": "canonical identity independently verifies",
    "P15": "effective_version and activation marker validate",
    "P16": "declared and actual policy consumers agree",
    "P17": "target semantics match; legacy semantics are not substituted",
    "P18": "all required facets are present",
    "P19": "prohibited authority fields/effects are absent",
    "P20": "report-only markers are exact",
    "P21": "deterministic calculation/enforcement exists",
    "P22": "invalid/missing inputs fail closed",
    "P23": "immutable identity/lineage is complete",
    "P24": "evidence references resolve to bound inputs",
    "P25": "trusted clock contract and value validate",
    "P26": "deterministic selection and bounds are enforced",
    "P27": "complete repository inventory succeeds",
    "P28": "prohibited consumers/capabilities are absent",
    "P29": "exact code-owned vocabulary is enforced",
    "P30": "Deep Research is excluded and only bounded packet input is accepted",
    "P31": "group-atomic package/current-pointer behavior is proven",
    "P32": "validation occurs on compiled orders after compilation",
    "P33": "evidence sufficiency is code-owned and deterministic",
    "P34": "all dependency checks satisfy their required level",
    "P35": "competing current identities do not conflict",
    "P36": "generation provenance binds prompt, raw output and model/surface",
    "P37": "all required structured runtime facets validate",
    "P38": "prose, filenames and prompts are excluded as proof",
    "P39": "tests are never sole proof",
    "P40": "producer, validator and allowed consumer are mutually compatible",
}

PROOF_PROFILES: Final = {
    "PA": (
        "P01", "P02", "P03", "P04", "P05", "P12", "P13", "P14", "P15", "P16", "P18", "P22", "P27", "P38", "P39",
    ),
    "PC": (
        "P01", "P02", "P03", "P04", "P05", "P09", "P14", "P17", "P18", "P19", "P22", "P27", "P38", "P39",
    ),
    "PI": (
        "P01", "P02", "P03", "P04", "P05", "P06", "P07", "P08", "P09", "P14", "P17", "P18", "P19", "P21", "P22", "P27", "P38", "P39", "P40",
    ),
    "PD": (
        "P01", "P02", "P03", "P04", "P05", "P10", "P11", "P14", "P18", "P22", "P27", "P35", "P37", "P38", "P39",
    ),
    "PL": (
        "P01", "P02", "P03", "P04", "P05", "P09", "P14", "P17", "P18", "P19", "P22", "P24", "P27", "P29", "P36", "P38", "P39",
    ),
    "PAI": (
        "P01", "P02", "P03", "P04", "P05", "P06", "P07", "P08", "P12", "P13", "P14", "P15", "P16", "P18", "P21", "P22", "P27", "P38", "P39", "P40",
    ),
    "PN": ("P01", "P27", "P28", "P38", "P39"),
}

EVIDENCE_PROFILES: Final = {
    "EPOL": (
        EvidenceKind.SCHEMA, EvidenceKind.VALIDATOR, EvidenceKind.OPERATOR_INPUT,
        EvidenceKind.PRODUCTION_MODULE, EvidenceKind.TEST_CONTRACT,
        EvidenceKind.REPOSITORY_CONFIG, EvidenceKind.DRAFT_DOCUMENT,
        EvidenceKind.RUNTIME_ARTIFACT,
    ),
    "ECON": (
        EvidenceKind.SCHEMA, EvidenceKind.VALIDATOR, EvidenceKind.PRODUCTION_MODULE,
        EvidenceKind.TEST_CONTRACT, EvidenceKind.REPOSITORY_CONFIG,
        EvidenceKind.DRAFT_DOCUMENT, EvidenceKind.RUNTIME_ARTIFACT,
    ),
    "EDATA": (
        EvidenceKind.SCHEMA, EvidenceKind.VALIDATOR, EvidenceKind.PRODUCTION_MODULE,
        EvidenceKind.TEST_CONTRACT, EvidenceKind.OPERATOR_INPUT,
        EvidenceKind.RUNTIME_ARTIFACT, EvidenceKind.REPOSITORY_CONFIG,
    ),
    "ELLM": (
        EvidenceKind.SCHEMA, EvidenceKind.VALIDATOR, EvidenceKind.PRODUCTION_MODULE,
        EvidenceKind.TEST_CONTRACT, EvidenceKind.REPOSITORY_CONFIG,
        EvidenceKind.DRAFT_DOCUMENT, EvidenceKind.RUNTIME_ARTIFACT,
    ),
    "ENEG": (
        EvidenceKind.PRODUCTION_MODULE, EvidenceKind.TEST_CONTRACT,
        EvidenceKind.REPOSITORY_CONFIG, EvidenceKind.REPOSITORY_INVENTORY,
    ),
}

SUPPORT_PROFILES: Final = {
    "SPOL": (EvidenceKind.DRAFT_DOCUMENT, EvidenceKind.REPOSITORY_CONFIG, EvidenceKind.TEST_CONTRACT),
    "SCON": (EvidenceKind.DRAFT_DOCUMENT, EvidenceKind.TEST_CONTRACT, EvidenceKind.REPOSITORY_CONFIG),
    "SDATA": (EvidenceKind.TEST_CONTRACT, EvidenceKind.DRAFT_DOCUMENT),
    "SNEG": (EvidenceKind.TEST_CONTRACT, EvidenceKind.DRAFT_DOCUMENT),
}

DISQUALIFIER_PROFILES: Final = {
    "XPOL": (
        "bare_yaml_or_documentation_without_acceptance",
        "missing_policy_identity_or_effective_marker",
        "unauthorized_policy_consumer",
    ),
    "XCON": (
        "legacy_semantics_substituted_for_target",
        "test_only_capability",
        "prompt_comment_or_filename_as_capability_proof",
        "missing_required_validator_or_producer",
        "prohibited_authority_effect",
    ),
    "XDATA": (
        "fixture_archive_or_history_as_current_data",
        "malformed_current_data_as_unavailable",
        "incomplete_runtime_facets_as_complete",
    ),
    "XNEG": (
        "incomplete_inventory_as_zero_consumers",
        "unresolved_dynamic_import_or_path",
        "prohibited_production_consumer_present",
    ),
}

REASON_CODES_BY_PROFILE: Final = {
    "POL": {
        ReadinessStatus.PROVEN_PRESENT: ("COMPLETE_PROOF_SATISFIED",),
        ReadinessStatus.PRESENT_UNACCEPTED: ("MACHINE_READABLE_CANDIDATE_NOT_ACCEPTED",),
        ReadinessStatus.DRAFT_ONLY: ("DRAFT_EVIDENCE_ONLY",),
        ReadinessStatus.PARTIAL: ("TARGET_PROOF_PARTIAL", "DEPENDENCY_NOT_PROVEN"),
        ReadinessStatus.MISSING: ("TARGET_IMPLEMENTATION_ABSENT", "TARGET_EVIDENCE_ABSENT"),
        ReadinessStatus.UNRESOLVED_OPERATOR_POLICY: ("OPERATOR_POLICY_DECISION_REQUIRED",),
        ReadinessStatus.UNRESOLVED_CONTRACT: ("TARGET_CONTRACT_NOT_FROZEN", "UNKNOWN_CONTRACT_VERSION"),
        ReadinessStatus.CONTRADICTORY: ("VALID_EVIDENCE_CONFLICT", "ACTIVE_POLICY_IDENTITY_CONFLICT"),
    },
    "CON": {
        ReadinessStatus.PROVEN_PRESENT: ("COMPLETE_PROOF_SATISFIED",),
        ReadinessStatus.PRESENT_UNACCEPTED: ("MACHINE_READABLE_CANDIDATE_NOT_ACCEPTED",),
        ReadinessStatus.DRAFT_ONLY: ("DRAFT_EVIDENCE_ONLY",),
        ReadinessStatus.PARTIAL: ("TARGET_PROOF_PARTIAL", "LEGACY_SEMANTICS_MISMATCH", "DEPENDENCY_NOT_PROVEN"),
        ReadinessStatus.MISSING: ("TARGET_IMPLEMENTATION_ABSENT", "TARGET_EVIDENCE_ABSENT"),
        ReadinessStatus.UNRESOLVED_CONTRACT: ("TARGET_CONTRACT_NOT_FROZEN", "UNKNOWN_CONTRACT_VERSION"),
        ReadinessStatus.CONTRADICTORY: ("VALID_EVIDENCE_CONFLICT", "PROHIBITED_CONSUMER_PRESENT"),
    },
    "DATA": {
        ReadinessStatus.PROVEN_PRESENT: ("COMPLETE_PROOF_SATISFIED",),
        ReadinessStatus.PARTIAL: ("RUNTIME_DATA_PARTIAL", "CURRENT_RUNTIME_DATA_INVALID", "DEPENDENCY_NOT_PROVEN"),
        ReadinessStatus.MISSING: ("TARGET_IMPLEMENTATION_ABSENT",),
        ReadinessStatus.UNRESOLVED_CONTRACT: ("TARGET_CONTRACT_NOT_FROZEN",),
        ReadinessStatus.UNAVAILABLE_RUNTIME_DATA: ("CURRENT_STRUCTURED_DATA_UNAVAILABLE",),
        ReadinessStatus.CONTRADICTORY: ("CURRENT_RUNTIME_IDENTITY_CONFLICT",),
    },
    "NEG": {
        ReadinessStatus.PROVEN_PRESENT: ("COMPLETE_PROOF_SATISFIED",),
        ReadinessStatus.CONTRADICTORY: ("PROHIBITED_CONSUMER_PRESENT",),
    },
}


@dataclass(frozen=True, slots=True)
class CatalogCheck:
    check_id: str
    dimension_id: str
    title: str
    contract_owner: ContractOwner
    runtime_actor: RuntimeActor
    target_requirement: str
    allowed_evidence_kinds: tuple[EvidenceKind, ...]
    required_proof_predicates: tuple[str, ...]
    optional_supporting_evidence: tuple[EvidenceKind, ...]
    disqualifying_conditions: tuple[str, ...]
    status_decision_table: str
    reason_codes_by_status: tuple[tuple[str, tuple[str, ...]], ...]
    blocker_type: BlockerType
    blocker_code: str
    dependency_check_ids: tuple[str, ...]
    authority_effect: AuthorityEffect


def _reason_profile(name: str) -> tuple[tuple[str, tuple[str, ...]], ...]:
    profile = REASON_CODES_BY_PROFILE[name]
    return tuple((status.value, reasons) for status, reasons in profile.items())


def _entry(
    check_id: str,
    dimension_id: str,
    title: str,
    contract_owner: ContractOwner,
    runtime_actor: RuntimeActor,
    target_requirement: str,
    *,
    evidence: str,
    proof: str,
    support: str,
    disqualify: str,
    decision: str,
    blocker_type: BlockerType,
    deps: tuple[str, ...] = (),
    modifiers: tuple[str, ...] = (),
) -> CatalogCheck:
    predicates = PROOF_PROFILES[proof] + modifiers
    return CatalogCheck(
        check_id=check_id,
        dimension_id=dimension_id,
        title=title,
        contract_owner=contract_owner,
        runtime_actor=runtime_actor,
        target_requirement=target_requirement,
        allowed_evidence_kinds=EVIDENCE_PROFILES[evidence],
        required_proof_predicates=predicates,
        optional_supporting_evidence=SUPPORT_PROFILES[support],
        disqualifying_conditions=DISQUALIFIER_PROFILES[disqualify],
        status_decision_table=decision,
        reason_codes_by_status=_reason_profile(decision),
        blocker_type=blocker_type,
        blocker_code=f"LTETF01_{check_id.upper()}_BLOCKED",
        dependency_check_ids=deps,
        authority_effect=AuthorityEffect.NONE,
    )


_OD = ContractOwner.OPERATOR_AND_DETERMINISTIC_VALIDATION
_DC = ContractOwner.DETERMINISTIC_CODE
_OP = RuntimeActor.OPERATOR
_LLM = RuntimeActor.LLM
_DET = RuntimeActor.DETERMINISTIC_CODE
_NA = RuntimeActor.NOT_APPLICABLE


CATALOG: Final = (
    # operator_mandate
    _entry("investment_horizon_policy", "operator_mandate", "Investment horizon policy", _OD, _OP, "Accepted thesis, review, and holding horizon policy.", evidence="EPOL", proof="PA", support="SPOL", disqualify="XPOL", decision="POL", blocker_type=BlockerType.OPERATOR_POLICY),
    _entry("risk_drawdown_policy", "operator_mandate", "Risk and drawdown policy", _OD, _OP, "Accepted loss tolerance, drawdown response, and risk-limit policy.", evidence="EPOL", proof="PA", support="SPOL", disqualify="XPOL", decision="POL", blocker_type=BlockerType.OPERATOR_POLICY),
    _entry("strategic_allocation_policy", "operator_mandate", "Strategic allocation policy", _OD, _OP, "Accepted asset and sleeve allocation semantics.", evidence="EPOL", proof="PA", support="SPOL", disqualify="XPOL", decision="POL", blocker_type=BlockerType.OPERATOR_POLICY, deps=("investment_horizon_policy", "risk_drawdown_policy")),
    _entry("liquidity_cash_policy", "operator_mandate", "Liquidity and cash policy", _OD, _OP, "Accepted reserve, deployable-cash, and liquidity rules.", evidence="EPOL", proof="PA", support="SPOL", disqualify="XPOL", decision="POL", blocker_type=BlockerType.OPERATOR_POLICY, deps=("risk_drawdown_policy",)),
    _entry("contribution_withdrawal_policy", "operator_mandate", "Contribution and withdrawal policy", _OD, _OP, "Accepted external-flow treatment.", evidence="EPOL", proof="PA", support="SPOL", disqualify="XPOL", decision="POL", blocker_type=BlockerType.OPERATOR_POLICY, deps=("liquidity_cash_policy",)),
    _entry("turnover_policy", "operator_mandate", "Turnover policy", _OD, _OP, "Accepted turnover resistance and realization policy.", evidence="EPOL", proof="PA", support="SPOL", disqualify="XPOL", decision="POL", blocker_type=BlockerType.OPERATOR_POLICY, deps=("investment_horizon_policy",)),
    _entry("tax_realization_policy", "operator_mandate", "Tax and realization policy", _OD, _OP, "Accepted account, jurisdiction, and tax-realization rules.", evidence="EPOL", proof="PA", support="SPOL", disqualify="XPOL", decision="POL", blocker_type=BlockerType.OPERATOR_POLICY, deps=("investment_horizon_policy",)),
    _entry("etf_product_policy", "operator_mandate", "ETF product policy", _OD, _OP, "Accepted universe, product, and vehicle eligibility rules.", evidence="EPOL", proof="PA", support="SPOL", disqualify="XPOL", decision="POL", blocker_type=BlockerType.OPERATOR_POLICY),
    _entry("concentration_overlap_policy", "operator_mandate", "Concentration and overlap policy", _OD, _OP, "Accepted exposure, overlap, and concentration limits.", evidence="EPOL", proof="PA", support="SPOL", disqualify="XPOL", decision="POL", blocker_type=BlockerType.OPERATOR_POLICY, deps=("strategic_allocation_policy", "etf_product_policy")),
    _entry("sell_lot_policy", "operator_mandate", "SELL and lot policy", _OD, _OP, "Accepted thesis-exit, risk-reduction, tax-lot, and oversell semantics.", evidence="EPOL", proof="PA", support="SPOL", disqualify="XPOL", decision="POL", blocker_type=BlockerType.OPERATOR_POLICY, deps=("turnover_policy", "tax_realization_policy")),
    _entry("manual_live_state_process_policy", "operator_mandate", "Manual live-state process policy", _OD, _OP, "Accepted process for broker truth, fills, open orders, and operator updates.", evidence="EPOL", proof="PA", support="SPOL", disqualify="XPOL", decision="POL", blocker_type=BlockerType.OPERATOR_POLICY),
    # evidence_and_grounding
    _entry("source_policy_contract", "evidence_and_grounding", "Source-policy object", _OD, _OP, "Accepted machine-readable authoritative-source policy contract.", evidence="EPOL", proof="PA", support="SPOL", disqualify="XPOL", decision="POL", blocker_type=BlockerType.CONTRACT),
    _entry("authorized_source_inventory", "evidence_and_grounding", "Authorized source inventory", _OD, _OP, "Accepted inventory bound to source-policy identity.", evidence="EPOL", proof="PA", support="SPOL", disqualify="XPOL", decision="POL", blocker_type=BlockerType.OPERATOR_POLICY, deps=("source_policy_contract",)),
    _entry("evidence_provenance_contract", "evidence_and_grounding", "Evidence provenance", _DC, _DET, "Source, acquisition, artifact, and transformation lineage.", evidence="ECON", proof="PC", support="SCON", disqualify="XCON", decision="CON", blocker_type=BlockerType.CONTRACT, deps=("authorized_source_inventory",)),
    _entry("evidence_timestamp_semantics", "evidence_and_grounding", "Evidence timestamp semantics", _DC, _DET, "Closed observed, published, acquired, and effective timestamp meanings.", evidence="ECON", proof="PC", support="SCON", disqualify="XCON", decision="CON", blocker_type=BlockerType.CONTRACT, deps=("evidence_provenance_contract",)),
    _entry("trusted_evaluation_clock", "evidence_and_grounding", "Trusted evaluation clock", _DC, _DET, "Deterministic trusted evaluation-time source.", evidence="ECON", proof="PC", support="SCON", disqualify="XCON", decision="CON", blocker_type=BlockerType.CONTRACT, modifiers=("P25",)),
    _entry("field_level_freshness_contract", "evidence_and_grounding", "Field-level freshness", _DC, _DET, "Per-field age and freshness evaluation.", evidence="ECON", proof="PI", support="SCON", disqualify="XCON", decision="CON", blocker_type=BlockerType.CONTRACT, deps=("evidence_timestamp_semantics", "trusted_evaluation_clock")),
    _entry("evidence_conflict_gap_contract", "evidence_and_grounding", "Evidence conflict and gap semantics", _DC, _DET, "Closed conflict, missingness, and unusable-data semantics.", evidence="ECON", proof="PC", support="SCON", disqualify="XCON", decision="CON", blocker_type=BlockerType.CONTRACT, deps=("evidence_provenance_contract",)),
    _entry("structured_market_metrics", "evidence_and_grounding", "Structured market metrics", _DC, _DET, "Validated source-bound calculations performed before the LLM.", evidence="EDATA", proof="PI", support="SDATA", disqualify="XDATA", decision="DATA", blocker_type=BlockerType.IMPLEMENTATION, deps=("authorized_source_inventory", "evidence_provenance_contract", "evidence_timestamp_semantics")),
    _entry("structured_scheduled_events", "evidence_and_grounding", "Structured scheduled events", _DC, _DET, "Validated source-bound scheduled-event data.", evidence="EDATA", proof="PI", support="SDATA", disqualify="XDATA", decision="DATA", blocker_type=BlockerType.IMPLEMENTATION, deps=("authorized_source_inventory", "evidence_timestamp_semantics")),
    _entry("evidence_selection_policy", "evidence_and_grounding", "Evidence selection policy", _OD, _DET, "Accepted deterministic relevance and exclusion policy.", evidence="EPOL", proof="PA", support="SPOL", disqualify="XPOL", decision="POL", blocker_type=BlockerType.OPERATOR_POLICY, deps=("authorized_source_inventory", "evidence_conflict_gap_contract"), modifiers=("P26",)),
    _entry("evidence_packet_bounds", "evidence_and_grounding", "Evidence packet bounds", _OD, _DET, "Accepted deterministic item, byte, and selection bounds.", evidence="EPOL", proof="PA", support="SPOL", disqualify="XPOL", decision="POL", blocker_type=BlockerType.OPERATOR_POLICY, deps=("evidence_selection_policy",), modifiers=("P26",)),
    _entry("deterministic_evidence_sufficiency", "evidence_and_grounding", "Deterministic evidence sufficiency", _DC, _DET, "Code-owned sufficiency result independent of LLM prose.", evidence="ECON", proof="PI", support="SCON", disqualify="XCON", decision="CON", blocker_type=BlockerType.CONTRACT, deps=("field_level_freshness_contract", "evidence_conflict_gap_contract", "evidence_packet_bounds"), modifiers=("P33",)),
    _entry("prior_thesis_continuity", "evidence_and_grounding", "Prior-thesis continuity", _DC, _DET, "Validated previous thesis, evidence, and change lineage.", evidence="EDATA", proof="PI", support="SDATA", disqualify="XDATA", decision="DATA", blocker_type=BlockerType.CONTRACT, deps=("evidence_provenance_contract",)),
    _entry("review_trigger_contract", "evidence_and_grounding", "Review trigger contract", _OD, _DET, "Accepted trigger vocabulary, thresholds, and report contract without permission effect.", evidence="EPOL", proof="PAI", support="SPOL", disqualify="XPOL", decision="POL", blocker_type=BlockerType.OPERATOR_POLICY, deps=("field_level_freshness_contract", "structured_scheduled_events", "prior_thesis_continuity")),
    _entry("prompt_envelope_identity", "evidence_and_grounding", "Prompt-envelope identity", _DC, _DET, "Identity-bound exact bounded evidence input and prompt contract.", evidence="ECON", proof="PI", support="SCON", disqualify="XCON", decision="CON", blocker_type=BlockerType.CONTRACT, deps=("evidence_packet_bounds", "evidence_provenance_contract")),
    # structured_portfolio_state
    _entry("holdings_state_contract", "structured_portfolio_state", "Holdings state", _DC, _DET, "Complete validated current holdings.", evidence="EDATA", proof="PD", support="SDATA", disqualify="XDATA", decision="DATA", blocker_type=BlockerType.RUNTIME_DATA),
    _entry("cash_state_contract", "structured_portfolio_state", "Cash state", _DC, _DET, "Validated current cash and reserved/deployable distinctions.", evidence="EDATA", proof="PD", support="SDATA", disqualify="XDATA", decision="DATA", blocker_type=BlockerType.RUNTIME_DATA),
    _entry("tax_lot_state_contract", "structured_portfolio_state", "Tax-lot state", _DC, _DET, "Validated lot-level acquisition, basis, and sellability.", evidence="EDATA", proof="PD", support="SDATA", disqualify="XDATA", decision="DATA", blocker_type=BlockerType.RUNTIME_DATA, deps=("tax_realization_policy",)),
    _entry("open_order_state_contract", "structured_portfolio_state", "Open-order state", _DC, _DET, "Validated complete BUY and SELL live-order state.", evidence="EDATA", proof="PD", support="SDATA", disqualify="XDATA", decision="DATA", blocker_type=BlockerType.RUNTIME_DATA, deps=("manual_live_state_process_policy",)),
    _entry("account_metadata_contract", "structured_portfolio_state", "Account metadata", _DC, _DET, "Validated account type, jurisdiction, broker, and restrictions.", evidence="EDATA", proof="PD", support="SDATA", disqualify="XDATA", decision="DATA", blocker_type=BlockerType.RUNTIME_DATA, deps=("tax_realization_policy",)),
    _entry("manual_order_state_contract", "structured_portfolio_state", "Manual-order state", _DC, _DET, "Validated review, submission, fill, and cancel state for manual artifacts.", evidence="EDATA", proof="PD", support="SDATA", disqualify="XDATA", decision="DATA", blocker_type=BlockerType.RUNTIME_DATA, deps=("manual_live_state_process_policy",)),
    _entry("portfolio_snapshot_identity", "structured_portfolio_state", "Portfolio snapshot identity", _DC, _DET, "One complete immutable identity over all required state.", evidence="EDATA", proof="PI", support="SDATA", disqualify="XDATA", decision="DATA", blocker_type=BlockerType.CONTRACT, deps=("holdings_state_contract", "cash_state_contract", "tax_lot_state_contract", "open_order_state_contract", "account_metadata_contract", "manual_order_state_contract")),
    # llm_analyst_and_signal_contract
    _entry("analyst_disposition_vocabulary", "llm_analyst_and_signal_contract", "Analyst disposition vocabulary", _DC, _LLM, "Exact held and unheld vocabulary including WATCH.", evidence="ELLM", proof="PL", support="SCON", disqualify="XCON", decision="CON", blocker_type=BlockerType.CONTRACT),
    _entry("analyst_required_fields_contract", "llm_analyst_and_signal_contract", "Analyst required fields", _DC, _LLM, "Thesis, drivers, risks, uncertainty, invalidation, indicators, horizon, gaps, and evidence references.", evidence="ELLM", proof="PL", support="SCON", disqualify="XCON", decision="CON", blocker_type=BlockerType.CONTRACT, deps=("analyst_disposition_vocabulary",)),
    _entry("analyst_prohibited_authority_contract", "llm_analyst_and_signal_contract", "Analyst prohibited authority", _DC, _DET, "Reject weights, quantities, budgets, permission, readiness, orders, and final safety.", evidence="ECON", proof="PC", support="SCON", disqualify="XCON", decision="CON", blocker_type=BlockerType.CONTRACT, deps=("analyst_required_fields_contract",)),
    _entry("analyst_evidence_reference_contract", "llm_analyst_and_signal_contract", "Analyst evidence references", _DC, _LLM, "Every trusted claim references packet evidence under closed rules.", evidence="ELLM", proof="PL", support="SCON", disqualify="XCON", decision="CON", blocker_type=BlockerType.CONTRACT, deps=("analyst_required_fields_contract", "evidence_provenance_contract")),
    _entry("analyst_generation_provenance", "llm_analyst_and_signal_contract", "Analyst generation provenance", _DC, _DET, "Model, surface, prompt, raw-response, and manual-paste lineage without authentication claims.", evidence="ECON", proof="PC", support="SCON", disqualify="XCON", decision="CON", blocker_type=BlockerType.CONTRACT, deps=("prompt_envelope_identity",), modifiers=("P36",)),
    _entry("analyst_parser_validator", "llm_analyst_and_signal_contract", "Analyst parser and validator", _DC, _DET, "Strict bounded parser and complete decoded semantic validator.", evidence="ECON", proof="PC", support="SCON", disqualify="XCON", decision="CON", blocker_type=BlockerType.CONTRACT, deps=("analyst_required_fields_contract", "analyst_prohibited_authority_contract", "analyst_evidence_reference_contract")),
    _entry("analyst_semantic_identity", "llm_analyst_and_signal_contract", "Analyst semantic identity", _DC, _DET, "Canonical identity over validated structured analysis and grounding pointers.", evidence="ECON", proof="PC", support="SCON", disqualify="XCON", decision="CON", blocker_type=BlockerType.CONTRACT, deps=("analyst_parser_validator", "analyst_generation_provenance")),
    _entry("analyst_invocation_grounding_boundary", "llm_analyst_and_signal_contract", "Analyst invocation grounding boundary", _DC, _DET, "No Deep Research and only bounded code-produced source-bound input.", evidence="ECON", proof="PI", support="SCON", disqualify="XCON", decision="CON", blocker_type=BlockerType.CONTRACT, deps=("prompt_envelope_identity", "analyst_parser_validator"), modifiers=("P30",)),
    _entry("signal_mapping_contract", "llm_analyst_and_signal_contract", "Signal mapping contract", _OD, _DET, "Accepted versioned identity-bound mapping that cannot grant permission or directly scale size from conviction.", evidence="EPOL", proof="PAI", support="SPOL", disqualify="XPOL", decision="POL", blocker_type=BlockerType.OPERATOR_POLICY, deps=("analyst_disposition_vocabulary", "analyst_semantic_identity", "deterministic_evidence_sufficiency")),
    # portfolio_construction
    _entry("portfolio_objective_contract", "portfolio_construction", "Portfolio objective contract", _OD, _OP, "Accepted lexicographic objective and HOLD/NO_TRADE semantic separation.", evidence="EPOL", proof="PA", support="SPOL", disqualify="XPOL", decision="POL", blocker_type=BlockerType.OPERATOR_POLICY, deps=("strategic_allocation_policy", "risk_drawdown_policy")),
    _entry("portfolio_hard_constraints", "portfolio_construction", "Portfolio hard constraints", _OD, _DET, "Accepted constraints that qualitative signals cannot override.", evidence="EPOL", proof="PAI", support="SPOL", disqualify="XPOL", decision="POL", blocker_type=BlockerType.OPERATOR_POLICY, deps=("portfolio_objective_contract", "concentration_overlap_policy", "liquidity_cash_policy")),
    _entry("rebalance_band_policy", "portfolio_construction", "Rebalance-band policy", _OD, _OP, "Accepted target-band semantics.", evidence="EPOL", proof="PA", support="SPOL", disqualify="XPOL", decision="POL", blocker_type=BlockerType.OPERATOR_POLICY, deps=("strategic_allocation_policy",)),
    _entry("no_trade_band_policy", "portfolio_construction", "No-trade-band policy", _OD, _OP, "Accepted suppression bands independent of HOLD semantics.", evidence="EPOL", proof="PA", support="SPOL", disqualify="XPOL", decision="POL", blocker_type=BlockerType.OPERATOR_POLICY, deps=("rebalance_band_policy", "turnover_policy")),
    _entry("hysteresis_cooldown_policy", "portfolio_construction", "Hysteresis and cooldown policy", _OD, _OP, "Accepted churn-resistance rules.", evidence="EPOL", proof="PA", support="SPOL", disqualify="XPOL", decision="POL", blocker_type=BlockerType.OPERATOR_POLICY, deps=("no_trade_band_policy", "turnover_policy")),
    _entry("after_cost_improvement_hurdle", "portfolio_construction", "After-cost improvement hurdle", _OD, _OP, "Accepted after-cost benefit threshold.", evidence="EPOL", proof="PA", support="SPOL", disqualify="XPOL", decision="POL", blocker_type=BlockerType.OPERATOR_POLICY, deps=("transaction_cost_policy", "turnover_policy")),
    _entry("transaction_cost_policy", "portfolio_construction", "Transaction-cost policy", _OD, _OP, "Accepted deterministic cost model and bounds.", evidence="EPOL", proof="PA", support="SPOL", disqualify="XPOL", decision="POL", blocker_type=BlockerType.OPERATOR_POLICY),
    _entry("liquidity_minimum_trade_policy", "portfolio_construction", "Liquidity and minimum-trade policy", _OD, _OP, "Accepted liquidity and minimum-trade rules.", evidence="EPOL", proof="PA", support="SPOL", disqualify="XPOL", decision="POL", blocker_type=BlockerType.OPERATOR_POLICY, deps=("liquidity_cash_policy", "etf_product_policy")),
    _entry("new_buy_eligibility_evaluation", "portfolio_construction", "NEW_BUY eligibility evaluation", _DC, _DET, "Complete deterministic asymmetric candidate evaluation.", evidence="ECON", proof="PI", support="SCON", disqualify="XCON", decision="CON", blocker_type=BlockerType.IMPLEMENTATION, deps=("signal_mapping_contract", "portfolio_hard_constraints", "liquidity_minimum_trade_policy")),
    _entry("sell_lot_eligibility_evaluation", "portfolio_construction", "SELL and lot eligibility evaluation", _DC, _DET, "Complete thesis-exit, risk-reduction, lot, and oversell evaluation.", evidence="ECON", proof="PI", support="SCON", disqualify="XCON", decision="CON", blocker_type=BlockerType.IMPLEMENTATION, deps=("signal_mapping_contract", "sell_lot_policy", "tax_lot_state_contract")),
    _entry("tax_constraint_enforcement", "portfolio_construction", "Tax constraint enforcement", _DC, _DET, "Deterministic account and lot tax constraints.", evidence="ECON", proof="PI", support="SCON", disqualify="XCON", decision="CON", blocker_type=BlockerType.IMPLEMENTATION, deps=("tax_realization_policy", "tax_lot_state_contract", "account_metadata_contract")),
    _entry("deterministic_portfolio_constructor", "portfolio_construction", "Deterministic portfolio constructor", _DC, _DET, "Deterministic proposal weights and actions with no LLM quantities.", evidence="ECON", proof="PI", support="SCON", disqualify="XCON", decision="CON", blocker_type=BlockerType.IMPLEMENTATION, deps=("portfolio_objective_contract", "portfolio_hard_constraints", "new_buy_eligibility_evaluation", "sell_lot_eligibility_evaluation", "tax_constraint_enforcement", "portfolio_snapshot_identity")),
    # semantic_audit_and_resolution
    _entry("semantic_finding_vocabulary", "semantic_audit_and_resolution", "Semantic finding vocabulary", _DC, _LLM, "Closed findings-only semantic challenge vocabulary.", evidence="ELLM", proof="PL", support="SCON", disqualify="XCON", decision="CON", blocker_type=BlockerType.CONTRACT, deps=("analyst_semantic_identity", "deterministic_portfolio_constructor")),
    _entry("semantic_audit_prohibited_authority_contract", "semantic_audit_and_resolution", "Semantic-audit prohibited authority", _DC, _DET, "Reject approval, readiness, weights, quantities, permissions, and orders.", evidence="ECON", proof="PC", support="SCON", disqualify="XCON", decision="CON", blocker_type=BlockerType.CONTRACT, deps=("semantic_finding_vocabulary",)),
    _entry("semantic_audit_parser_validator", "semantic_audit_and_resolution", "Semantic-audit parser and validator", _DC, _DET, "Strict raw parser, semantic validator, grounding, and proposal-lineage validation.", evidence="ECON", proof="PC", support="SCON", disqualify="XCON", decision="CON", blocker_type=BlockerType.CONTRACT, deps=("semantic_finding_vocabulary", "semantic_audit_prohibited_authority_contract")),
    _entry("semantic_finding_identity", "semantic_audit_and_resolution", "Semantic finding identity", _DC, _DET, "Canonical finding-set identity bound to analysis and proposal.", evidence="ECON", proof="PC", support="SCON", disqualify="XCON", decision="CON", blocker_type=BlockerType.CONTRACT, deps=("semantic_audit_parser_validator",)),
    _entry("immutable_finding_ledger", "semantic_audit_and_resolution", "Immutable finding ledger", _DC, _DET, "Append-only identities and no silent finding deletion.", evidence="ECON", proof="PI", support="SCON", disqualify="XCON", decision="CON", blocker_type=BlockerType.IMPLEMENTATION, deps=("semantic_finding_identity",)),
    _entry("semantic_resolution_table", "semantic_audit_and_resolution", "Semantic resolution table", _OD, _DET, "Accepted exact mapping to CONTINUE, RECOMPUTE, REVIEW, NO_TRADE, and BLOCKED.", evidence="EPOL", proof="PAI", support="SPOL", disqualify="XPOL", decision="POL", blocker_type=BlockerType.OPERATOR_POLICY, deps=("semantic_finding_vocabulary", "immutable_finding_ledger")),
    _entry("bounded_recomputation_policy", "semantic_audit_and_resolution", "Bounded recomputation policy", _OD, _DET, "Accepted maximum attempts and terminal behavior.", evidence="EPOL", proof="PAI", support="SPOL", disqualify="XPOL", decision="POL", blocker_type=BlockerType.OPERATOR_POLICY, deps=("semantic_resolution_table",)),
    _entry("semantic_recomputation_lineage", "semantic_audit_and_resolution", "Semantic recomputation lineage", _DC, _DET, "Every changed proposal retains predecessor and unresolved-finding identities and is re-audited.", evidence="ECON", proof="PI", support="SCON", disqualify="XCON", decision="CON", blocker_type=BlockerType.IMPLEMENTATION, deps=("bounded_recomputation_policy", "immutable_finding_ledger")),
    _entry("repeated_finding_policy", "semantic_audit_and_resolution", "Repeated-finding policy", _OD, _DET, "Accepted deterministic escalation and preservation behavior.", evidence="EPOL", proof="PA", support="SPOL", disqualify="XPOL", decision="POL", blocker_type=BlockerType.OPERATOR_POLICY, deps=("semantic_resolution_table", "immutable_finding_ledger")),
    # approval_order_and_migration_safety
    _entry("resolved_proposal_review_package", "approval_order_and_migration_safety", "Resolved proposal review package", _DC, _DET, "Immutable package binding mandate, evidence, analysis, signals, proposal, findings, resolution, state, pricing, and policies.", evidence="ECON", proof="PI", support="SCON", disqualify="XCON", decision="CON", blocker_type=BlockerType.CONTRACT, deps=("deterministic_portfolio_constructor", "semantic_recomputation_lineage")),
    _entry("proposal_approval_intent_contract", "approval_order_and_migration_safety", "Proposal approval intent contract", _OD, _OP, "Validated approval or rejection bound to exact resolved-package identity.", evidence="EPOL", proof="PAI", support="SPOL", disqualify="XPOL", decision="POL", blocker_type=BlockerType.CONTRACT, deps=("resolved_proposal_review_package",)),
    _entry("approval_authentication_threat_model", "approval_order_and_migration_safety", "Approval authentication threat model", _OD, _OP, "Accepted threat model and explicit local-versus-authenticated semantics.", evidence="EPOL", proof="PA", support="SPOL", disqualify="XPOL", decision="POL", blocker_type=BlockerType.OPERATOR_POLICY, deps=("proposal_approval_intent_contract",)),
    _entry("approval_expiry_policy", "approval_order_and_migration_safety", "Approval expiry policy", _OD, _OP, "Accepted evaluation and expiry boundary.", evidence="EPOL", proof="PA", support="SPOL", disqualify="XPOL", decision="POL", blocker_type=BlockerType.OPERATOR_POLICY, deps=("proposal_approval_intent_contract", "trusted_evaluation_clock")),
    _entry("price_drift_recompilation_tolerance", "approval_order_and_migration_safety", "Price-drift recompilation tolerance", _OD, _OP, "Accepted price/state-change and recompilation tolerance.", evidence="EPOL", proof="PA", support="SPOL", disqualify="XPOL", decision="POL", blocker_type=BlockerType.OPERATOR_POLICY, deps=("proposal_approval_intent_contract", "approval_expiry_policy")),
    _entry("precompile_eligibility_contract", "approval_order_and_migration_safety", "Precompile eligibility contract", _DC, _DET, "Deterministic prerequisite evaluation distinct from approval validity.", evidence="ECON", proof="PI", support="SCON", disqualify="XCON", decision="CON", blocker_type=BlockerType.CONTRACT, deps=("proposal_approval_intent_contract", "price_drift_recompilation_tolerance")),
    _entry("deterministic_order_compiler", "approval_order_and_migration_safety", "Deterministic order compiler", _DC, _DET, "Deterministic target/current reconciliation and quantity arithmetic.", evidence="ECON", proof="PI", support="SCON", disqualify="XCON", decision="CON", blocker_type=BlockerType.IMPLEMENTATION, deps=("precompile_eligibility_contract", "portfolio_snapshot_identity")),
    _entry("complete_buy_order_validator", "approval_order_and_migration_safety", "Complete BUY order validator", _DC, _DET, "Complete lineage, budget, cash, quantity, grouping, and readiness validation.", evidence="ECON", proof="PI", support="SCON", disqualify="XCON", decision="CON", blocker_type=BlockerType.IMPLEMENTATION, deps=("deterministic_order_compiler", "cash_state_contract", "open_order_state_contract")),
    _entry("complete_sell_order_validator", "approval_order_and_migration_safety", "Complete SELL order validator", _DC, _DET, "Complete lot, oversell, open-order, tax, and lineage validation.", evidence="ECON", proof="PI", support="SCON", disqualify="XCON", decision="CON", blocker_type=BlockerType.IMPLEMENTATION, deps=("deterministic_order_compiler", "sell_lot_eligibility_evaluation", "tax_lot_state_contract", "open_order_state_contract")),
    _entry("postcompile_final_safety", "approval_order_and_migration_safety", "Postcompile final safety", _DC, _DET, "True validation of compiled orders after compilation.", evidence="ECON", proof="PI", support="SCON", disqualify="XCON", decision="CON", blocker_type=BlockerType.IMPLEMENTATION, deps=("complete_buy_order_validator", "complete_sell_order_validator"), modifiers=("P32",)),
    _entry("atomic_manual_order_package", "approval_order_and_migration_safety", "Atomic manual-order package", _DC, _DET, "Group-atomic immutable review-only package.", evidence="ECON", proof="PI", support="SCON", disqualify="XCON", decision="CON", blocker_type=BlockerType.IMPLEMENTATION, deps=("postcompile_final_safety",), modifiers=("P31",)),
    _entry("atomic_current_package_pointer", "approval_order_and_migration_safety", "Atomic current-package pointer", _DC, _DET, "Atomic pointer to one complete accepted package identity.", evidence="ECON", proof="PI", support="SCON", disqualify="XCON", decision="CON", blocker_type=BlockerType.IMPLEMENTATION, deps=("atomic_manual_order_package",), modifiers=("P31",)),
    _entry("artifact_retention_policy", "approval_order_and_migration_safety", "Artifact retention policy", _OD, _OP, "Accepted retention, supersession, and current-pointer policy.", evidence="EPOL", proof="PA", support="SPOL", disqualify="XPOL", decision="POL", blocker_type=BlockerType.OPERATOR_POLICY, deps=("atomic_current_package_pointer",)),
    _entry("dormant_p4a_runtime_isolation", "approval_order_and_migration_safety", "Dormant p4a runtime isolation", _DC, _NA, "p4a3 and downstream p4 symbols have no target or runtime consumer.", evidence="ENEG", proof="PN", support="SNEG", disqualify="XNEG", decision="NEG", blocker_type=BlockerType.CONTRADICTION),
    _entry("broker_live_execution_absence", "approval_order_and_migration_safety", "Broker/live execution absence", _DC, _NA, "Complete production inventory proves broker/live execution is absent.", evidence="ENEG", proof="PN", support="SNEG", disqualify="XNEG", decision="NEG", blocker_type=BlockerType.CONTRADICTION),
    _entry("fail_closed_weekly_baseline", "approval_order_and_migration_safety", "Fail-closed weekly baseline", _DC, _DET, "Current weekly deterministically terminates safely and suppresses downstream stages.", evidence="ECON", proof="PI", support="SCON", disqualify="XCON", decision="CON", blocker_type=BlockerType.IMPLEMENTATION),
    _entry("automatic_weekly_llm_absence", "approval_order_and_migration_safety", "Automatic weekly LLM absence", _DC, _NA, "Complete inventory proves weekly never invokes an LLM automatically.", evidence="ENEG", proof="PN", support="SNEG", disqualify="XNEG", decision="NEG", blocker_type=BlockerType.CONTRADICTION),
    _entry("target_orchestration_contract", "approval_order_and_migration_safety", "Target orchestration contract", _DC, _DET, "One state machine separating reachability, permission, HOLD, NO_TRADE, approval, compilation, and final safety.", evidence="ECON", proof="PC", support="SCON", disqualify="XCON", decision="CON", blocker_type=BlockerType.CONTRACT, deps=("review_trigger_contract", "analyst_invocation_grounding_boundary", "semantic_resolution_table", "precompile_eligibility_contract", "postcompile_final_safety")),
)


def catalog_check_to_dict(check: CatalogCheck) -> dict[str, object]:
    """Return the exact canonical representation of one catalog entry."""
    return {
        "check_id": check.check_id,
        "dimension_id": check.dimension_id,
        "title": check.title,
        "contract_owner": check.contract_owner.value,
        "runtime_actor": check.runtime_actor.value,
        "target_requirement": check.target_requirement,
        "allowed_evidence_kinds": [item.value for item in check.allowed_evidence_kinds],
        "required_proof_predicates": list(check.required_proof_predicates),
        "optional_supporting_evidence": [item.value for item in check.optional_supporting_evidence],
        "disqualifying_conditions": list(check.disqualifying_conditions),
        "status_decision_table": check.status_decision_table,
        "reason_codes_by_status": {
            status: list(codes) for status, codes in check.reason_codes_by_status
        },
        "blocker_type": check.blocker_type.value,
        "blocker_code": check.blocker_code,
        "dependency_check_ids": list(check.dependency_check_ids),
        "authority_effect": check.authority_effect.value,
    }


def _validate_dependency_cycles(checks_by_id: dict[str, CatalogCheck]) -> None:
    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(check_id: str) -> None:
        if check_id in visiting:
            raise CatalogIntegrityError("CATALOG_DEPENDENCY_CYCLE")
        if check_id in visited:
            return
        visiting.add(check_id)
        for dependency in checks_by_id[check_id].dependency_check_ids:
            visit(dependency)
        visiting.remove(check_id)
        visited.add(check_id)

    for check_id in checks_by_id:
        visit(check_id)


def validate_catalog(catalog: tuple[CatalogCheck, ...] = CATALOG) -> None:
    """Validate all frozen catalog invariants before identity computation."""
    if type(catalog) is not tuple:
        raise CatalogIntegrityError("OBSERVER_CATALOG_INVALID")
    if len(catalog) != 81:
        raise CatalogIntegrityError("CATALOG_CHECK_COUNT_INVALID")
    if any(type(check) is not CatalogCheck for check in catalog):
        raise CatalogIntegrityError("CATALOG_CHECK_TYPE_INVALID")
    if any(type(check.check_id) is not str for check in catalog):
        raise CatalogIntegrityError("CATALOG_SCALAR_TYPE_INVALID")

    ids = tuple(check.check_id for check in catalog)
    if len(set(ids)) != len(ids):
        raise CatalogIntegrityError("CATALOG_DUPLICATE_CHECK_ID")
    if ids != EXPECTED_CHECK_IDS:
        raise CatalogIntegrityError("CATALOG_ORDER_INVALID")

    counts = {dimension: 0 for dimension in DIMENSION_IDS}
    checks_by_id = {check.check_id: check for check in catalog}
    for check in catalog:
        if (
            type(check.check_id) is not str
            or type(check.dimension_id) is not str
            or type(check.title) is not str
            or type(check.target_requirement) is not str
            or type(check.blocker_code) is not str
            or type(check.status_decision_table) is not str
        ):
            raise CatalogIntegrityError("CATALOG_SCALAR_TYPE_INVALID")
        if (
            type(check.contract_owner) is not ContractOwner
            or type(check.runtime_actor) is not RuntimeActor
            or type(check.authority_effect) is not AuthorityEffect
            or type(check.blocker_type) is not BlockerType
        ):
            raise CatalogIntegrityError("CATALOG_ENUM_TYPE_INVALID")
        if (
            type(check.allowed_evidence_kinds) is not tuple
            or type(check.required_proof_predicates) is not tuple
            or type(check.optional_supporting_evidence) is not tuple
            or type(check.disqualifying_conditions) is not tuple
            or type(check.reason_codes_by_status) is not tuple
            or type(check.dependency_check_ids) is not tuple
        ):
            raise CatalogIntegrityError("CATALOG_CONTAINER_TYPE_INVALID")
        if check.dimension_id not in counts:
            raise CatalogIntegrityError("CATALOG_DIMENSION_INVALID")
        counts[check.dimension_id] += 1
        if (check.contract_owner, check.runtime_actor) not in ALLOWED_OWNER_ACTOR_PAIRS:
            raise CatalogIntegrityError("CATALOG_OWNERSHIP_PAIR_INVALID")
        if check.authority_effect is not AuthorityEffect.NONE:
            raise CatalogIntegrityError("CATALOG_AUTHORITY_EFFECT_INVALID")
        if check.status_decision_table not in REASON_CODES_BY_PROFILE:
            raise CatalogIntegrityError("CATALOG_STATUS_DECISION_TABLE_INVALID")
        if not check.blocker_code == f"LTETF01_{check.check_id.upper()}_BLOCKED":
            raise CatalogIntegrityError("CATALOG_BLOCKER_CODE_INVALID")
        if any(predicate not in PROOF_PREDICATES for predicate in check.required_proof_predicates):
            raise CatalogIntegrityError("CATALOG_PROOF_PREDICATE_INVALID")
        if any(type(predicate) is not str for predicate in check.required_proof_predicates):
            raise CatalogIntegrityError("CATALOG_PROOF_PREDICATE_INVALID")
        if any(type(kind) is not EvidenceKind for kind in check.allowed_evidence_kinds):
            raise CatalogIntegrityError("CATALOG_EVIDENCE_KIND_INVALID")
        if any(type(kind) is not EvidenceKind for kind in check.optional_supporting_evidence):
            raise CatalogIntegrityError("CATALOG_EVIDENCE_KIND_INVALID")
        if any(type(item) is not str for item in check.disqualifying_conditions):
            raise CatalogIntegrityError("CATALOG_DISQUALIFIER_INVALID")
        for reason_entry in check.reason_codes_by_status:
            if type(reason_entry) is not tuple or len(reason_entry) != 2:
                raise CatalogIntegrityError("CATALOG_REASON_CODES_INVALID")
            status, reason_codes = reason_entry
            if (
                type(status) is not str
                or type(reason_codes) is not tuple
                or any(type(code) is not str for code in reason_codes)
            ):
                raise CatalogIntegrityError("CATALOG_REASON_CODES_INVALID")
        if any(type(dependency) is not str for dependency in check.dependency_check_ids):
            raise CatalogIntegrityError("CATALOG_DEPENDENCY_TYPE_INVALID")
        if len(set(check.dependency_check_ids)) != len(check.dependency_check_ids):
            raise CatalogIntegrityError("CATALOG_DUPLICATE_DEPENDENCY")
        if any(dependency not in checks_by_id for dependency in check.dependency_check_ids):
            raise CatalogIntegrityError("CATALOG_UNKNOWN_DEPENDENCY")
        if check.check_id in check.dependency_check_ids:
            raise CatalogIntegrityError("CATALOG_DEPENDENCY_CYCLE")

    if counts != DIMENSION_COUNTS:
        raise CatalogIntegrityError("CATALOG_DIMENSION_COUNT_INVALID")
    ownership_counts = {
        pair: sum(
            1
            for check in catalog
            if (check.contract_owner, check.runtime_actor) == pair
        )
        for pair in ALLOWED_OWNER_ACTOR_PAIRS
    }
    if ownership_counts != OWNERSHIP_PAIR_COUNTS:
        raise CatalogIntegrityError("CATALOG_OWNERSHIP_COUNT_INVALID")
    if {
        check.check_id for check in catalog if check.runtime_actor is RuntimeActor.LLM
    } != LLM_RUNTIME_ACTOR_CHECK_IDS:
        raise CatalogIntegrityError("CATALOG_LLM_RUNTIME_ACTOR_INVALID")
    if {
        check.check_id for check in catalog if check.runtime_actor is RuntimeActor.NOT_APPLICABLE
    } != NOT_APPLICABLE_RUNTIME_ACTOR_CHECK_IDS:
        raise CatalogIntegrityError("CATALOG_NOT_APPLICABLE_RUNTIME_ACTOR_INVALID")
    _validate_dependency_cycles(checks_by_id)


def canonical_catalog_bytes(catalog: tuple[CatalogCheck, ...] = CATALOG) -> bytes:
    """Serialize the validated catalog deterministically for its identity."""
    validate_catalog(catalog)
    record = {
        "catalog_version": CATALOG_VERSION,
        "dimension_ids": list(DIMENSION_IDS),
        "dimension_counts": DIMENSION_COUNTS,
        "status_rollup_precedence": [status.value for status in STATUS_ROLLUP_PRECEDENCE],
        "proof_predicates": PROOF_PREDICATES,
        "proof_profiles": {
            profile_name: list(predicate_ids)
            for profile_name, predicate_ids in PROOF_PROFILES.items()
        },
        "allowed_owner_actor_pairs": [
            {"contract_owner": owner.value, "runtime_actor": actor.value}
            for owner, actor in sorted(
                ALLOWED_OWNER_ACTOR_PAIRS,
                key=lambda pair: (pair[0].value, pair[1].value),
            )
        ],
        "checks": [catalog_check_to_dict(check) for check in catalog],
    }
    encoded = json.dumps(record, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode("utf-8")
    if len(encoded) > MAX_CATALOG_CANONICAL_BYTES:
        raise CatalogIntegrityError("CANONICAL_BOUND_EXCEEDED")
    return encoded


def catalog_identity_sha256(catalog: tuple[CatalogCheck, ...] = CATALOG) -> str:
    """Return the domain-separated SHA-256 identity of the frozen catalog."""
    return hashlib.sha256(CATALOG_IDENTITY_DOMAIN + canonical_catalog_bytes(catalog)).hexdigest()


validate_catalog()
CATALOG_IDENTITY_SHA256: Final = catalog_identity_sha256()
