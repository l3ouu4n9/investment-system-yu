from __future__ import annotations

import ast
from collections.abc import Iterator, Mapping
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import inspect
import json
from pathlib import Path
import struct

from jsonschema import Draft202012Validator
import pytest

from investment_orchestrator.common.paths import repo_root
from investment_orchestrator.common.schema_validation import (
    validate_artifact_schema,
)
from investment_orchestrator.mmi import (
    analyst_visible_evidence_view as v1,
    analyst_visible_evidence_view_v2 as v2,
    canonical,
)
from investment_orchestrator.mmi.analyst_visible_evidence_view_v2 import (
    MMI_ANALYST_VISIBLE_EVIDENCE_VIEW_V2_ARTIFACT_KIND,
    MMI_ANALYST_VISIBLE_EVIDENCE_VIEW_V2_RESEARCH_COMPONENT_STATUSES,
    MMI_ANALYST_VISIBLE_EVIDENCE_VIEW_V2_SCHEMA_VERSION,
    build_mmi_analyst_visible_evidence_view_v2,
    validate_mmi_analyst_visible_evidence_view_v2,
)
from investment_orchestrator.mmi.canonical import (
    _MMI_ANALYST_VISIBLE_EVIDENCE_VIEW_V2_IDENTITY_DOMAIN,
    canonical_json_bytes,
)
from investment_orchestrator.mmi.contracts import (
    MmiCapturedSource,
    MmiPolicyProjectionBuildResult,
    MmiProjectionResultCategory,
    MmiProjectionRunContext,
    MmiSourceRole,
    _begin_mmi_projection_run_with_clock,
    mmi_analyst_visible_evidence_view_identity_sha256,
)
from investment_orchestrator.mmi.evidence_bundle import (
    build_mmi_authenticated_evidence_bundle,
)
from investment_orchestrator.mmi.policy_projection import (
    build_mmi_policy_projection,
)
from investment_orchestrator.mmi.source_capture import (
    capture_current_mmi_source,
)
from investment_orchestrator.observability import (
    ltetf_target_architecture_gap_report as ltetf,
)


SCHEMA_NAME = "mmi_analyst_visible_evidence_view_v2.schema.json"
IDENTITY_FIELD = "analyst_visible_evidence_view_identity_sha256"
IDENTITY_DOMAIN = b"mmi_analyst_visible_evidence_view_v2\0"
EVALUATION_TIME = datetime(
    2026,
    6,
    29,
    12,
    tzinfo=timezone.utc,
)
EXPECTED_STATUSES = {
    "per_instrument_research": "LIMITED_TO_VISIBLE_EVIDENCE",
    "anchor_associations": "UNAVAILABLE",
    "scheduled_events": "UNAVAILABLE",
    "regime_inputs": "UNAVAILABLE",
}


class _FixedClock:
    def now_utc(self) -> datetime:
        return EVALUATION_TIME


class _OneSnapshotMapping(Mapping[str, object]):
    def __init__(self, value: Mapping[str, object]) -> None:
        self._value = dict(value)
        self.iterations = 0
        self.lookups: dict[str, int] = {}

    def __iter__(self) -> Iterator[str]:
        self.iterations += 1
        if self.iterations > 1:
            raise AssertionError("mapping read more than once")
        return iter(self._value)

    def __len__(self) -> int:
        return len(self._value)

    def __getitem__(self, key: str) -> object:
        self.lookups[key] = self.lookups.get(key, 0) + 1
        if self.lookups[key] > 1:
            raise AssertionError("key read more than once")
        return self._value[key]

    def assert_once(self) -> None:
        assert self.iterations == 1
        assert self.lookups == dict.fromkeys(self._value, 1)


class _Inputs:
    def __init__(
        self,
        *,
        policy: dict[str, object],
        policy_source: MmiCapturedSource,
        evidence: dict[str, object],
        run_context: MmiProjectionRunContext,
    ) -> None:
        self.policy = policy
        self.policy_source = policy_source
        self.evidence = evidence
        self.run_context = run_context


@pytest.fixture(scope="module")
def inputs() -> _Inputs:
    raw = (
        repo_root() / "inputs/current/strategy_settings.yaml"
    ).read_bytes()
    capture = capture_current_mmi_source(
        MmiSourceRole.STRATEGY_SETTINGS,
        expected_source_sha256=hashlib.sha256(raw).hexdigest(),
    )
    assert capture.valid, capture.reason_codes
    assert capture.source is not None
    run_context = _begin_mmi_projection_run_with_clock(_FixedClock())
    policy_result = build_mmi_policy_projection(
        capture.source,
        run_context=run_context,
    )
    assert policy_result.valid, policy_result.reason_codes
    assert policy_result.projection is not None
    policy = dict(policy_result.projection)
    evidence_result = build_mmi_authenticated_evidence_bundle(
        policy_projection=deepcopy(policy),
        policy_source=capture.source,
        portfolio_projection=None,
        portfolio_source=None,
        run_context=run_context,
    )
    assert evidence_result.valid, evidence_result.reason_codes
    assert evidence_result.projection is not None
    return _Inputs(
        policy=policy,
        policy_source=capture.source,
        evidence=dict(evidence_result.projection),
        run_context=run_context,
    )


def _build(inputs: _Inputs) -> MmiPolicyProjectionBuildResult:
    return build_mmi_analyst_visible_evidence_view_v2(
        evidence_bundle=deepcopy(inputs.evidence),
        policy_projection=deepcopy(inputs.policy),
        policy_source=inputs.policy_source,
        portfolio_projection=None,
        portfolio_source=None,
        run_context=inputs.run_context,
    )


def _valid(inputs: _Inputs) -> dict[str, object]:
    result = _build(inputs)
    assert result.valid, result.reason_codes
    assert result.projection is not None
    return dict(result.projection)


def _validate(
    candidate: Mapping[str, object],
    inputs: _Inputs,
):
    return validate_mmi_analyst_visible_evidence_view_v2(
        value=candidate,
        evidence_bundle=deepcopy(inputs.evidence),
        policy_projection=deepcopy(inputs.policy),
        policy_source=inputs.policy_source,
        portfolio_projection=None,
        portfolio_source=None,
        run_context=inputs.run_context,
    )


def _independent_identity(value: dict[str, object]) -> str:
    preimage = deepcopy(value)
    preimage.pop(IDENTITY_FIELD, None)
    canonical_bytes = json.dumps(
        preimage,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(
        IDENTITY_DOMAIN
        + struct.pack(">Q", len(canonical_bytes))
        + canonical_bytes
    ).hexdigest()


def _reseal(value: dict[str, object]) -> None:
    value[IDENTITY_FIELD] = _independent_identity(value)


def test_public_api_is_version_specific_keyword_only_and_not_exported() -> None:
    expected_build = (
        "evidence_bundle",
        "policy_projection",
        "policy_source",
        "portfolio_projection",
        "portfolio_source",
        "run_context",
    )
    expected_validate = ("value", *expected_build)
    assert tuple(
        inspect.signature(
            build_mmi_analyst_visible_evidence_view_v2
        ).parameters
    ) == expected_build
    assert tuple(
        inspect.signature(
            validate_mmi_analyst_visible_evidence_view_v2
        ).parameters
    ) == expected_validate
    for function in (
        build_mmi_analyst_visible_evidence_view_v2,
        validate_mmi_analyst_visible_evidence_view_v2,
    ):
        assert all(
            parameter.kind is inspect.Parameter.KEYWORD_ONLY
            for parameter in inspect.signature(
                function
            ).parameters.values()
        )
    assert v2.__all__ == (
        "build_mmi_analyst_visible_evidence_view_v2",
        "validate_mmi_analyst_visible_evidence_view_v2",
    )
    import investment_orchestrator.mmi as mmi

    assert mmi.__all__ == ()
    assert not hasattr(
        mmi,
        "build_mmi_analyst_visible_evidence_view_v2",
    )


def test_exact_closed_schema_and_fixed_contract(inputs: _Inputs) -> None:
    schema = json.loads(
        (
            repo_root()
            / "schemas"
            / SCHEMA_NAME
        ).read_text(encoding="utf-8")
    )
    Draft202012Validator.check_schema(schema)
    assert schema["additionalProperties"] is False
    status_schema = schema["$defs"]["research_component_statuses"]
    assert status_schema["additionalProperties"] is False
    assert set(status_schema["required"]) == set(EXPECTED_STATUSES)
    assert {
        field: definition["const"]
        for field, definition in status_schema["properties"].items()
    } == EXPECTED_STATUSES

    view = _valid(inputs)
    assert (
        view["schema_version"]
        == MMI_ANALYST_VISIBLE_EVIDENCE_VIEW_V2_SCHEMA_VERSION
        == "mmi_analyst_visible_evidence_view_v2"
    )
    assert (
        view["artifact_kind"]
        ==
        MMI_ANALYST_VISIBLE_EVIDENCE_VIEW_V2_ARTIFACT_KIND
        == "MMI_ANALYST_VISIBLE_EVIDENCE_VIEW"
    )
    assert (
        view["research_component_statuses"]
        ==
        MMI_ANALYST_VISIBLE_EVIDENCE_VIEW_V2_RESEARCH_COMPONENT_STATUSES
        == EXPECTED_STATUSES
    )
    assert view["report_only"] is True
    assert view["authority_effect"] == "NONE"
    assert view["view_completeness_status"] == (
        "PROJECTION_VALID_WITH_GAPS"
    )
    validate_artifact_schema(view, schema_name=SCHEMA_NAME)


def test_v2_preserves_every_v1_field_and_canonical_v1_output(
    inputs: _Inputs,
) -> None:
    v1_before = v1.build_mmi_analyst_visible_evidence_view(
        evidence_bundle=deepcopy(inputs.evidence),
        policy_projection=deepcopy(inputs.policy),
        policy_source=inputs.policy_source,
        portfolio_projection=None,
        portfolio_source=None,
        run_context=inputs.run_context,
    )
    assert v1_before.valid and v1_before.projection is not None
    before = dict(v1_before.projection)
    before_bytes = canonical_json_bytes(before)
    before_identity = before[IDENTITY_FIELD]

    view = _valid(inputs)
    inherited = {
        key: deepcopy(value)
        for key, value in view.items()
        if key not in {
            "schema_version",
            "research_component_statuses",
            IDENTITY_FIELD,
        }
    }
    expected_inherited = {
        key: deepcopy(value)
        for key, value in before.items()
        if key not in {"schema_version", IDENTITY_FIELD}
    }
    assert inherited == expected_inherited

    v1_after = v1.build_mmi_analyst_visible_evidence_view(
        evidence_bundle=deepcopy(inputs.evidence),
        policy_projection=deepcopy(inputs.policy),
        policy_source=inputs.policy_source,
        portfolio_projection=None,
        portfolio_source=None,
        run_context=inputs.run_context,
    )
    assert v1_after.projection == before
    assert canonical_json_bytes(dict(v1_after.projection)) == before_bytes
    assert (
        mmi_analyst_visible_evidence_view_identity_sha256(before)
        == before_identity
    )


@pytest.mark.parametrize(
    "mutation",
    ("missing", "altered", "additional", "top-level-additional"),
)
def test_component_status_contract_rejects_every_mutation(
    inputs: _Inputs,
    mutation: str,
) -> None:
    candidate = _valid(inputs)
    statuses = candidate["research_component_statuses"]
    assert type(statuses) is dict
    if mutation == "missing":
        statuses.pop("anchor_associations")
    elif mutation == "altered":
        statuses["scheduled_events"] = "AVAILABLE"
    elif mutation == "additional":
        statuses["future_component"] = "UNAVAILABLE"
    else:
        candidate["future"] = "closed"
    _reseal(candidate)
    result = _validate(candidate, inputs)
    assert result.status is MmiProjectionResultCategory.PROJECTION_BLOCKED
    assert result.reason_codes == (
        "MMI_ANALYST_VIEW_V2_CANDIDATE_SCHEMA_INVALID",
    )


@pytest.mark.parametrize(
    "mutation",
    ("missing", "duplicate", "foreign", "reordered"),
)
def test_complete_p1_membership_and_order_are_expected_exactly(
    inputs: _Inputs,
    mutation: str,
) -> None:
    candidate = _valid(inputs)
    policy = candidate["policy_view"]
    assert type(policy) is dict
    instruments = policy["analysis_instruments"]
    assert type(instruments) is list and len(instruments) >= 4
    if mutation == "missing":
        instruments.pop(1)
    elif mutation == "duplicate":
        satellite = next(
            item
            for item in instruments
            if type(item) is dict
            and item.get("policy_role") == "SATELLITE"
        )
        satellite["ticker"] = instruments[0]["ticker"]
    elif mutation == "foreign":
        instruments[1]["ticker"] = "SPY"
    else:
        instruments[0], instruments[1] = (
            instruments[1],
            instruments[0],
        )
    _reseal(candidate)
    validate_artifact_schema(candidate, schema_name=SCHEMA_NAME)
    result = _validate(candidate, inputs)
    assert result.status is (
        MmiProjectionResultCategory.PROJECTION_CONTRACT_FAILURE
    )
    assert result.reason_codes == (
        "MMI_ANALYST_VIEW_V2_SOURCE_FIDELITY_MISMATCH",
    )


def test_v2_identity_uses_exact_domain_and_binds_every_nonself_field(
    inputs: _Inputs,
) -> None:
    view = _valid(inputs)
    assert _MMI_ANALYST_VISIBLE_EVIDENCE_VIEW_V2_IDENTITY_DOMAIN == (
        IDENTITY_DOMAIN
    )
    assert view[IDENTITY_FIELD] == _independent_identity(view)
    for field in set(view) - {IDENTITY_FIELD}:
        changed = deepcopy(view)
        if field == "report_only":
            changed[field] = False
        elif field == "research_component_statuses":
            statuses = changed[field]
            assert type(statuses) is dict
            statuses["regime_inputs"] = "CHANGED"
        elif field in {"policy_view", "portfolio_view"}:
            area = changed[field]
            assert type(area) is dict
            area["identity_probe"] = True
        elif field == "known_view_limitations":
            limitations = changed[field]
            assert type(limitations) is list
            limitations.append({"identity_probe": True})
        else:
            changed[field] = f"{changed[field]}-changed"
        assert _independent_identity(changed) != view[IDENTITY_FIELD]


def test_correctly_resealed_nonexpected_candidate_is_rejected(
    inputs: _Inputs,
) -> None:
    candidate = _valid(inputs)
    candidate["evidence_bundle_identity_sha256"] = "f" * 64
    _reseal(candidate)
    validate_artifact_schema(candidate, schema_name=SCHEMA_NAME)
    assert candidate[IDENTITY_FIELD] == _independent_identity(candidate)
    result = _validate(candidate, inputs)
    assert result.status is (
        MmiProjectionResultCategory.PROJECTION_CONTRACT_FAILURE
    )
    assert result.reason_codes == (
        "MMI_ANALYST_VIEW_V2_SOURCE_FIDELITY_MISMATCH",
    )


def test_source_bound_p1_and_e1_validators_are_invoked(
    inputs: _Inputs,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[str] = []
    policy_validator = v2.validate_mmi_policy_projection
    evidence_validator = (
        v2._evidence_bundle.validate_mmi_authenticated_evidence_bundle
    )

    def validate_policy(*args: object, **kwargs: object):
        events.append("P1")
        return policy_validator(*args, **kwargs)

    def validate_evidence(*args: object, **kwargs: object):
        events.append("E1")
        return evidence_validator(*args, **kwargs)

    monkeypatch.setattr(
        v2,
        "validate_mmi_policy_projection",
        validate_policy,
    )
    monkeypatch.setattr(
        v2._evidence_bundle,
        "validate_mmi_authenticated_evidence_bundle",
        validate_evidence,
    )
    result = _build(inputs)
    assert result.valid, result.reason_codes
    assert events == ["P1", "E1"]


def test_each_input_and_candidate_is_snapshotted_once(
    inputs: _Inputs,
) -> None:
    evidence = _OneSnapshotMapping(inputs.evidence)
    policy = _OneSnapshotMapping(inputs.policy)
    result = build_mmi_analyst_visible_evidence_view_v2(
        evidence_bundle=evidence,
        policy_projection=policy,
        policy_source=inputs.policy_source,
        portfolio_projection=None,
        portfolio_source=None,
        run_context=inputs.run_context,
    )
    assert result.valid, result.reason_codes
    evidence.assert_once()
    policy.assert_once()

    assert result.projection is not None
    candidate = _OneSnapshotMapping(result.projection)
    evidence = _OneSnapshotMapping(inputs.evidence)
    policy = _OneSnapshotMapping(inputs.policy)
    validation = validate_mmi_analyst_visible_evidence_view_v2(
        value=candidate,
        evidence_bundle=evidence,
        policy_projection=policy,
        policy_source=inputs.policy_source,
        portfolio_projection=None,
        portfolio_source=None,
        run_context=inputs.run_context,
    )
    assert validation.valid, validation.reason_codes
    candidate.assert_once()
    evidence.assert_once()
    policy.assert_once()


def test_cross_version_inputs_fail_closed(inputs: _Inputs) -> None:
    v1_result = v1.build_mmi_analyst_visible_evidence_view(
        evidence_bundle=deepcopy(inputs.evidence),
        policy_projection=deepcopy(inputs.policy),
        policy_source=inputs.policy_source,
        portfolio_projection=None,
        portfolio_source=None,
        run_context=inputs.run_context,
    )
    assert v1_result.valid and v1_result.projection is not None
    v2_validation = _validate(v1_result.projection, inputs)
    assert v2_validation.status is (
        MmiProjectionResultCategory.PROJECTION_BLOCKED
    )
    assert v2_validation.reason_codes == (
        "MMI_ANALYST_VIEW_V2_CANDIDATE_SCHEMA_INVALID",
    )

    v2_view = _valid(inputs)
    v1_validation = v1.validate_mmi_analyst_visible_evidence_view(
        value=v2_view,
        evidence_bundle=deepcopy(inputs.evidence),
        policy_projection=deepcopy(inputs.policy),
        policy_source=inputs.policy_source,
        portfolio_projection=None,
        portfolio_source=None,
        run_context=inputs.run_context,
    )
    assert v1_validation.status is (
        MmiProjectionResultCategory.PROJECTION_BLOCKED
    )
    assert v1_validation.reason_codes == (
        "MMI_ANALYST_VIEW_CANDIDATE_SCHEMA_INVALID",
    )


def test_invalid_upstream_and_diagnostics_fail_closed_without_values(
    inputs: _Inputs,
) -> None:
    result = build_mmi_analyst_visible_evidence_view_v2(
        evidence_bundle={"private": "PRIVATE_EVIDENCE"},
        policy_projection={"ticker": "PRIVATE_TICKER"},
        policy_source=inputs.policy_source,
        portfolio_projection=None,
        portfolio_source=None,
        run_context=inputs.run_context,
    )
    assert not result.valid
    assert result.projection is None
    assert result.reason_codes == (
        "MMI_ANALYST_VIEW_V2_UPSTREAM_COMPONENT_BLOCKED",
    )
    diagnostic = repr(result)
    for forbidden in (
        "PRIVATE_EVIDENCE",
        "PRIVATE_TICKER",
        "inputs/current",
        "sha256",
    ):
        assert forbidden not in diagnostic


def test_no_authority_or_future_research_fields_enter_v2(
    inputs: _Inputs,
) -> None:
    serialized = json.dumps(_valid(inputs)).casefold()
    for forbidden in (
        "instrument_views",
        "rationale",
        "stance",
        "confidence",
        "priority",
        "ranking",
        "recommendation",
        "actionability",
        "anchor_id",
        "event_record",
        "regime_prose",
        "allowed_actions",
        "blocked_actions",
        "order_compilation",
        "order_readiness",
        "execution_authority",
        "publication_authority",
    ):
        assert forbidden not in serialized


def test_v2_has_exact_g2_consumer_and_no_side_effect_surface() -> None:
    root = repo_root()
    module_path = (
        root
        / "src/investment_orchestrator/mmi/"
        "analyst_visible_evidence_view_v2.py"
    )
    module_name = (
        "investment_orchestrator.mmi."
        "analyst_visible_evidence_view_v2"
    )
    source = module_path.read_text(encoding="utf-8")
    tree = ast.parse(source)
    imported = {
        node.module
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module is not None
    } | {
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    }
    assert not {
        "os",
        "pathlib",
        "subprocess",
        "socket",
        "requests",
        "httpx",
        "openai",
        "anthropic",
        "investment_orchestrator.orders",
        "investment_orchestrator.broker",
        "investment_orchestrator.workflow",
    } & imported
    for forbidden in (
        "write_",
        "publish",
        "pointer",
        "write_validated_json",
        "write_json",
        "publish",
        "pointer",
        "capture_current_mmi_source",
    ):
        assert forbidden not in source.casefold()

    importers: list[str] = []
    for path in sorted(
        (root / "src/investment_orchestrator").rglob("*.py")
    ):
        if path == module_path:
            continue
        candidate_tree = ast.parse(path.read_text(encoding="utf-8"))
        if any(
            (
                isinstance(node, ast.ImportFrom)
                and node.module is not None
                and (
                    node.module == module_name
                    or node.module.startswith(f"{module_name}.")
                )
            )
            or (
                isinstance(node, ast.Import)
                and any(
                    alias.name == module_name
                    or alias.name.startswith(f"{module_name}.")
                    for alias in node.names
                )
            )
            for node in ast.walk(candidate_tree)
        ):
            importers.append(path.relative_to(root).as_posix())
    assert importers == [
        "src/investment_orchestrator/mmi/grounded_prompt_v2.py",
        (
            "src/investment_orchestrator/mmi/"
            "legacy_step1_compatibility_candidate_v1.py"
        ),
        (
            "src/investment_orchestrator/offline/"
            "mmi_h2c_manual_capture_session.py"
        ),
    ]


def test_inventory_and_persistent_identity_domain_counts_are_exact() -> None:
    inventory = ltetf._scan_production_inventory(repo_root())
    assert len(inventory.production_paths) == 144
    domains = {
        name: value
        for name, value in vars(canonical).items()
        if name.endswith("_DOMAIN")
        and type(value) is bytes
    }
    assert len(domains) == len(set(domains.values())) == 18
    assert domains[
        "_MMI_ANALYST_VISIBLE_EVIDENCE_VIEW_V2_IDENTITY_DOMAIN"
    ] == IDENTITY_DOMAIN
    assert all(
        domain.endswith(b"\0")
        and b"\0" not in domain[:-1]
        and domain.decode("ascii")
        for domain in domains.values()
    )
