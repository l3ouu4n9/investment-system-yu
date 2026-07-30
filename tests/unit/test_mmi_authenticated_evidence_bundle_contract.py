from __future__ import annotations

import ast
from copy import deepcopy
import hashlib
import inspect
import json
from pathlib import Path
import struct

from jsonschema import Draft202012Validator
import pytest

from investment_orchestrator.common.paths import repo_root
from investment_orchestrator.common.schema_validation import (
    ArtifactSchemaError,
    validate_artifact_schema,
)
from investment_orchestrator.mmi import canonical
from investment_orchestrator.mmi.canonical import (
    MAXIMUM_AUTHENTICATED_EVIDENCE_BUNDLE_CANONICAL_BYTES,
    MMI_AUTHENTICATED_EVIDENCE_BUNDLE_IDENTITY_DOMAIN,
    MMI_POLICY_PROJECTION_IDENTITY_DOMAIN,
    MMI_PORTFOLIO_SNAPSHOT_PROJECTION_IDENTITY_DOMAIN,
    MMI_SOURCE_RECORD_IDENTITY_DOMAIN,
    MMI_UNIVERSE_PROJECTION_IDENTITY_DOMAIN,
    MmiCanonicalizationError,
    canonical_json_bytes,
    record_identity_sha256,
)
from investment_orchestrator.mmi import contracts
from investment_orchestrator.mmi.contracts import (
    AUTHORITY_EFFECT_NONE,
    MMI_AUTHENTICATED_EVIDENCE_BUNDLE_ARTIFACT_KIND,
    MMI_AUTHENTICATED_EVIDENCE_BUNDLE_SCHEMA_VERSION,
    MMI_EVIDENCE_ASSEMBLY_GAP_SCOPE,
    MMI_EVIDENCE_POLICY_COMPONENT_PRESENCE_STATUS,
    MMI_EVIDENCE_PORTFOLIO_GAP_COMPONENT,
    MMI_EVIDENCE_PORTFOLIO_NOT_SUPPLIED_GAP_CODE,
    MMI_EVIDENCE_PORTFOLIO_NOT_SUPPLIED_STATUS,
    MMI_EVIDENCE_PORTFOLIO_SOURCE_ABSENT_STATUS,
    MMI_EVIDENCE_PORTFOLIO_SOURCE_BOUND_STATUS,
    MmiProjectionResultCategory,
    mmi_authenticated_evidence_bundle_identity_sha256,
)


SCHEMA_NAME = "mmi_authenticated_evidence_bundle_v1.schema.json"
SCHEMA_PATH = (
    Path("schemas") / "mmi_authenticated_evidence_bundle_v1.schema.json"
)
TIMESTAMP = "2026-07-26T12:34:56.123456Z"
SHA_A = "1" * 64
SHA_B = "2" * 64
SHA_C = "3" * 64
SHA_D = "4" * 64
SHA_E = "5" * 64
SHA_F = "6" * 64

NOT_SUPPLIED = "NOT_SUPPLIED"
SOURCE_ABSENT = "PRESENT_VALIDATED_SOURCE_ABSENT"
SOURCE_BOUND = "PRESENT_SOURCE_BOUND_VALIDATED"
PORTFOLIO_BRANCHES = (NOT_SUPPLIED, SOURCE_ABSENT, SOURCE_BOUND)


def _policy_component() -> dict[str, object]:
    return {
        "presence_status": "PRESENT_SOURCE_BOUND_VALIDATED",
        "strategy_source_schema_version": "mmi_source_record_v1",
        "strategy_source_role": "STRATEGY_SETTINGS",
        "strategy_source_record_identity_sha256": SHA_A,
        "universe_schema_version": "mmi_universe_projection_v1",
        "universe_artifact_kind": "MMI_UNIVERSE_PROJECTION",
        "universe_projection_identity_sha256": SHA_B,
        "policy_schema_version": "mmi_policy_projection_v1",
        "policy_artifact_kind": "MMI_POLICY_PROJECTION",
        "policy_projection_identity_sha256": SHA_C,
        "validation_result_category": "PROJECTION_VALID_WITH_GAPS",
    }


def _portfolio_component(branch: str) -> dict[str, object]:
    if branch == NOT_SUPPLIED:
        return {"presence_status": "NOT_SUPPLIED"}
    base: dict[str, object] = {
        "presence_status": branch,
        "portfolio_schema_version": (
            "mmi_portfolio_snapshot_projection_v1"
        ),
        "portfolio_artifact_kind": (
            "MMI_PORTFOLIO_SNAPSHOT_PROJECTION"
        ),
        "portfolio_projection_identity_sha256": SHA_D,
        "policy_projection_identity_sha256": SHA_C,
        "portfolio_source_status": (
            "SOURCE_ABSENT"
            if branch == SOURCE_ABSENT
            else "SOURCE_PRESENT_CONTENT_BOUND"
        ),
        "validation_result_category": "PROJECTION_VALID_WITH_GAPS",
    }
    if branch == SOURCE_BOUND:
        base.update(
            {
                "portfolio_source_schema_version": (
                    "mmi_source_record_v1"
                ),
                "portfolio_source_role": "PORTFOLIO_SNAPSHOT",
                "portfolio_source_record_identity_sha256": SHA_E,
            }
        )
    return base


def _independent_identity(value: dict[str, object]) -> str:
    preimage = deepcopy(value)
    preimage.pop("evidence_bundle_identity_sha256", None)
    canonical_bytes = json.dumps(
        preimage,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(
        b"mmi_authenticated_evidence_bundle_v1\0"
        + struct.pack(">Q", len(canonical_bytes))
        + canonical_bytes
    ).hexdigest()


def _manifest(branch: str = SOURCE_BOUND) -> dict[str, object]:
    gaps: list[dict[str, object]] = []
    if branch == NOT_SUPPLIED:
        gaps.append(
            {
                "code": "EVIDENCE_PORTFOLIO_COMPONENT_NOT_SUPPLIED",
                "scope": "EVIDENCE_ASSEMBLY",
                "component": "PORTFOLIO_PROJECTION",
            }
        )
    value: dict[str, object] = {
        "schema_version": "mmi_authenticated_evidence_bundle_v1",
        "artifact_kind": "MMI_AUTHENTICATED_EVIDENCE_BUNDLE",
        "report_only": True,
        "authority_effect": "NONE",
        "evaluation_timestamp_utc": TIMESTAMP,
        "policy_component": _policy_component(),
        "portfolio_component": _portfolio_component(branch),
        "known_evidence_gaps": gaps,
        "evidence_completeness_status": (
            "PROJECTION_VALID_WITH_GAPS"
        ),
        "evidence_bundle_identity_sha256": "0" * 64,
    }
    value["evidence_bundle_identity_sha256"] = _independent_identity(
        value
    )
    return value


def _assert_schema_rejected(value: object) -> None:
    with pytest.raises(ArtifactSchemaError):
        validate_artifact_schema(value, schema_name=SCHEMA_NAME)


def _assert_identity_rejected(value: object) -> None:
    with pytest.raises(
        MmiCanonicalizationError,
        match="MMI_AUTHENTICATED_EVIDENCE_BUNDLE_CONTRACT_INVALID",
    ):
        mmi_authenticated_evidence_bundle_identity_sha256(value)  # type: ignore[arg-type]


def _schema() -> dict[str, object]:
    value = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    assert type(value) is dict
    return value


def _object_schemas(value: object):
    if type(value) is dict:
        if value.get("type") == "object":
            yield value
        for child in value.values():
            yield from _object_schemas(child)
    elif type(value) is list:
        for child in value:
            yield from _object_schemas(child)


def _typed_string_schemas(value: object):
    if type(value) is dict:
        if value.get("type") == "string":
            yield value
        for child in value.values():
            yield from _typed_string_schemas(child)
    elif type(value) is list:
        for child in value:
            yield from _typed_string_schemas(child)


def _reverse_mapping_order(value: object) -> object:
    if type(value) is dict:
        return {
            key: _reverse_mapping_order(child)
            for key, child in reversed(tuple(value.items()))
        }
    if type(value) is list:
        return [_reverse_mapping_order(child) for child in value]
    return value


def _leaf_paths(
    value: object,
    prefix: tuple[object, ...] = (),
) -> list[tuple[object, ...]]:
    if type(value) is dict:
        paths: list[tuple[object, ...]] = []
        for key, child in value.items():
            paths.extend(_leaf_paths(child, (*prefix, key)))
        return paths
    if type(value) is list:
        paths = []
        for index, child in enumerate(value):
            paths.extend(_leaf_paths(child, (*prefix, index)))
        return paths
    return [prefix]


def _value_at_path(value: object, path: tuple[object, ...]) -> object:
    current = value
    for part in path:
        current = current[part]  # type: ignore[index]
    return current


def _set_path(
    value: object,
    path: tuple[object, ...],
    replacement: object,
) -> None:
    current = value
    for part in path[:-1]:
        current = current[part]  # type: ignore[index]
    current[path[-1]] = replacement  # type: ignore[index]


def test_schema_is_closed_draft_2020_12_and_has_exact_top_level() -> None:
    schema = _schema()
    assert schema["$schema"] == (
        "https://json-schema.org/draft/2020-12/schema"
    )
    assert schema["additionalProperties"] is False
    assert set(schema["required"]) == {
        "schema_version",
        "artifact_kind",
        "report_only",
        "authority_effect",
        "evaluation_timestamp_utc",
        "policy_component",
        "portfolio_component",
        "known_evidence_gaps",
        "evidence_completeness_status",
        "evidence_bundle_identity_sha256",
    }
    assert set(schema["properties"]) == set(schema["required"])
    assert all(
        item.get("additionalProperties") is False
        for item in _object_schemas(schema)
    )
    assert all(
        "const" in item or "pattern" in item
        for item in _typed_string_schemas(schema)
    )
    Draft202012Validator.check_schema(schema)


@pytest.mark.parametrize("branch", PORTFOLIO_BRANCHES)
def test_each_exact_portfolio_branch_is_formally_valid(
    branch: str,
) -> None:
    value = _manifest(branch)
    validate_artifact_schema(value, schema_name=SCHEMA_NAME)
    assert (
        mmi_authenticated_evidence_bundle_identity_sha256(value)
        == value["evidence_bundle_identity_sha256"]
        == _independent_identity(value)
    )


@pytest.mark.parametrize(
    "field",
    (
        "schema_version",
        "artifact_kind",
        "report_only",
        "authority_effect",
        "evaluation_timestamp_utc",
        "policy_component",
        "portfolio_component",
        "known_evidence_gaps",
        "evidence_completeness_status",
        "evidence_bundle_identity_sha256",
    ),
)
def test_every_top_level_field_is_required(field: str) -> None:
    value = _manifest()
    value.pop(field)
    _assert_schema_rejected(value)
    _assert_identity_rejected(value)


def test_top_level_and_every_nested_object_are_closed() -> None:
    candidates = []
    top = _manifest()
    top["unexpected"] = "closed"
    candidates.append(top)

    policy = _manifest()
    policy_component = policy["policy_component"]
    assert type(policy_component) is dict
    policy_component["unexpected"] = "closed"
    candidates.append(policy)

    portfolio = _manifest()
    portfolio_component = portfolio["portfolio_component"]
    assert type(portfolio_component) is dict
    portfolio_component["unexpected"] = "closed"
    candidates.append(portfolio)

    gap_value = _manifest(NOT_SUPPLIED)
    gaps = gap_value["known_evidence_gaps"]
    assert type(gaps) is list and type(gaps[0]) is dict
    gaps[0]["unexpected"] = "closed"
    candidates.append(gap_value)

    for candidate in candidates:
        _assert_schema_rejected(candidate)
        _assert_identity_rejected(candidate)


@pytest.mark.parametrize(
    ("path", "replacement"),
    (
        (("schema_version",), "mmi_authenticated_evidence_bundle_v2"),
        (("artifact_kind",), "MMI_EVIDENCE_PACKAGE"),
        (("report_only",), False),
        (("authority_effect",), "READY"),
        (("evaluation_timestamp_utc",), "2026-07-26"),
        (
            ("evidence_completeness_status",),
            "PROJECTION_VALID_COMPLETE",
        ),
        (
            ("policy_component", "presence_status"),
            "PRESENT",
        ),
        (
            ("policy_component", "strategy_source_schema_version"),
            "mmi_source_record_v2",
        ),
        (
            ("policy_component", "strategy_source_role"),
            "PORTFOLIO_SNAPSHOT",
        ),
        (
            ("policy_component", "universe_schema_version"),
            "mmi_universe_projection_v2",
        ),
        (
            ("policy_component", "universe_artifact_kind"),
            "MMI_POLICY_PROJECTION",
        ),
        (
            ("policy_component", "policy_schema_version"),
            "mmi_policy_projection_v2",
        ),
        (
            ("policy_component", "policy_artifact_kind"),
            "MMI_UNIVERSE_PROJECTION",
        ),
        (
            ("policy_component", "validation_result_category"),
            "PROJECTION_VALID_COMPLETE",
        ),
        (
            ("portfolio_component", "portfolio_schema_version"),
            "mmi_portfolio_snapshot_projection_v2",
        ),
        (
            ("portfolio_component", "portfolio_artifact_kind"),
            "MMI_PORTFOLIO_PROJECTION",
        ),
        (
            ("portfolio_component", "portfolio_source_status"),
            "SOURCE_ABSENT",
        ),
        (
            (
                "portfolio_component",
                "portfolio_source_schema_version",
            ),
            "mmi_source_record_v2",
        ),
        (
            ("portfolio_component", "portfolio_source_role"),
            "STRATEGY_SETTINGS",
        ),
        (
            ("portfolio_component", "validation_result_category"),
            "PROJECTION_VALID_COMPLETE",
        ),
    ),
)
def test_fixed_contract_values_cannot_be_changed(
    path: tuple[object, ...],
    replacement: object,
) -> None:
    value = _manifest()
    _set_path(value, path, replacement)
    _assert_schema_rejected(value)
    _assert_identity_rejected(value)


@pytest.mark.parametrize(
    "timestamp",
    (
        "2026-07-26T12:34:56Z",
        "2026-07-26T12:34:56.12345Z",
        "2026-07-26T12:34:56.123456+00:00",
        "not-a-timestamp",
    ),
)
def test_timestamp_contract_is_exact(timestamp: str) -> None:
    value = _manifest()
    value["evaluation_timestamp_utc"] = timestamp
    _assert_schema_rejected(value)
    _assert_identity_rejected(value)


def test_identity_contract_rejects_lexical_but_calendar_invalid_timestamp() -> None:
    value = _manifest()
    value["evaluation_timestamp_utc"] = (
        "2026-13-26T12:34:56.123456Z"
    )
    validate_artifact_schema(value, schema_name=SCHEMA_NAME)
    _assert_identity_rejected(value)


@pytest.mark.parametrize("branch", PORTFOLIO_BRANCHES)
def test_every_hash_field_requires_lowercase_sha256(branch: str) -> None:
    value = _manifest(branch)
    hash_paths = [
        path
        for path in _leaf_paths(value)
        if str(path[-1]).endswith("_identity_sha256")
    ]
    assert hash_paths
    for path in hash_paths:
        for malformed in ("a" * 63, "A" * 64, "g" * 64):
            candidate = deepcopy(value)
            _set_path(candidate, path, malformed)
            _assert_schema_rejected(candidate)
            _assert_identity_rejected(candidate)


@pytest.mark.parametrize(
    "field",
    tuple(_policy_component()),
)
def test_every_policy_component_field_is_required(field: str) -> None:
    value = _manifest()
    component = value["policy_component"]
    assert type(component) is dict
    component.pop(field)
    _assert_schema_rejected(value)
    _assert_identity_rejected(value)


def test_not_supplied_branch_contains_only_omission_status() -> None:
    union_fields = set(_portfolio_component(SOURCE_BOUND))
    for field in union_fields - {"presence_status"}:
        value = _manifest(NOT_SUPPLIED)
        component = value["portfolio_component"]
        assert type(component) is dict
        component[field] = (
            SHA_E
            if field.endswith("_identity_sha256")
            else "unexpected"
        )
        _assert_schema_rejected(value)
        _assert_identity_rejected(value)


@pytest.mark.parametrize(
    ("branch", "field"),
    tuple(
        (branch, field)
        for branch in (SOURCE_ABSENT, SOURCE_BOUND)
        for field in _portfolio_component(branch)
    ),
)
def test_every_present_portfolio_field_is_required(
    branch: str,
    field: str,
) -> None:
    value = _manifest(branch)
    component = value["portfolio_component"]
    assert type(component) is dict
    component.pop(field)
    _assert_schema_rejected(value)
    _assert_identity_rejected(value)


@pytest.mark.parametrize(
    ("branch", "field", "replacement"),
    (
        (
            SOURCE_ABSENT,
            "portfolio_source_record_identity_sha256",
            SHA_E,
        ),
        (
            SOURCE_ABSENT,
            "portfolio_source_schema_version",
            "mmi_source_record_v1",
        ),
        (
            SOURCE_ABSENT,
            "portfolio_source_role",
            "PORTFOLIO_SNAPSHOT",
        ),
        (
            SOURCE_BOUND,
            "presence_status",
            "PRESENT_VALIDATED_SOURCE_ABSENT",
        ),
        (
            SOURCE_ABSENT,
            "presence_status",
            "PRESENT_SOURCE_BOUND_VALIDATED",
        ),
        (
            SOURCE_BOUND,
            "portfolio_source_status",
            "SOURCE_ABSENT",
        ),
        (
            SOURCE_ABSENT,
            "portfolio_source_status",
            "SOURCE_PRESENT_CONTENT_BOUND",
        ),
    ),
)
def test_every_portfolio_branch_hybrid_is_rejected(
    branch: str,
    field: str,
    replacement: object,
) -> None:
    value = _manifest(branch)
    component = value["portfolio_component"]
    assert type(component) is dict
    component[field] = replacement
    _assert_schema_rejected(value)
    _assert_identity_rejected(value)


def test_referenced_policy_identity_must_match_policy_component() -> None:
    value = _manifest()
    component = value["portfolio_component"]
    assert type(component) is dict
    component["policy_projection_identity_sha256"] = SHA_F
    validate_artifact_schema(value, schema_name=SCHEMA_NAME)
    _assert_identity_rejected(value)


def test_gap_presence_is_exactly_correlated_with_omission() -> None:
    no_gap = _manifest(NOT_SUPPLIED)
    no_gap["known_evidence_gaps"] = []

    gap_when_present = _manifest(SOURCE_ABSENT)
    gap_when_present["known_evidence_gaps"] = deepcopy(
        _manifest(NOT_SUPPLIED)["known_evidence_gaps"]
    )

    duplicate = _manifest(NOT_SUPPLIED)
    gaps = duplicate["known_evidence_gaps"]
    assert type(gaps) is list
    gaps.append(deepcopy(gaps[0]))

    for candidate in (no_gap, gap_when_present, duplicate):
        _assert_schema_rejected(candidate)
        _assert_identity_rejected(candidate)


@pytest.mark.parametrize(
    ("field", "replacement"),
    (
        ("code", "PORTFOLIO_SOURCE_MISSING"),
        ("scope", "PORTFOLIO_SNAPSHOT"),
        ("component", "POLICY_PROJECTION"),
    ),
)
def test_gap_vocabulary_is_one_exact_closed_record(
    field: str,
    replacement: str,
) -> None:
    value = _manifest(NOT_SUPPLIED)
    gaps = value["known_evidence_gaps"]
    assert type(gaps) is list and type(gaps[0]) is dict
    gaps[0][field] = replacement
    _assert_schema_rejected(value)
    _assert_identity_rejected(value)


def test_contract_constants_are_exact_and_reuse_result_enum() -> None:
    assert MMI_AUTHENTICATED_EVIDENCE_BUNDLE_SCHEMA_VERSION == (
        "mmi_authenticated_evidence_bundle_v1"
    )
    assert MMI_AUTHENTICATED_EVIDENCE_BUNDLE_ARTIFACT_KIND == (
        "MMI_AUTHENTICATED_EVIDENCE_BUNDLE"
    )
    assert MMI_EVIDENCE_POLICY_COMPONENT_PRESENCE_STATUS == (
        "PRESENT_SOURCE_BOUND_VALIDATED"
    )
    assert MMI_EVIDENCE_PORTFOLIO_NOT_SUPPLIED_STATUS == "NOT_SUPPLIED"
    assert MMI_EVIDENCE_PORTFOLIO_SOURCE_ABSENT_STATUS == (
        "PRESENT_VALIDATED_SOURCE_ABSENT"
    )
    assert MMI_EVIDENCE_PORTFOLIO_SOURCE_BOUND_STATUS == (
        "PRESENT_SOURCE_BOUND_VALIDATED"
    )
    assert MMI_EVIDENCE_PORTFOLIO_NOT_SUPPLIED_GAP_CODE == (
        "EVIDENCE_PORTFOLIO_COMPONENT_NOT_SUPPLIED"
    )
    assert MMI_EVIDENCE_ASSEMBLY_GAP_SCOPE == "EVIDENCE_ASSEMBLY"
    assert MMI_EVIDENCE_PORTFOLIO_GAP_COMPONENT == (
        "PORTFOLIO_PROJECTION"
    )
    assert (
        MmiProjectionResultCategory.PROJECTION_VALID_WITH_GAPS.value
        == "PROJECTION_VALID_WITH_GAPS"
    )
    assert not hasattr(contracts, "MmiEvidenceBundleResultCategory")


@pytest.mark.parametrize("branch", PORTFOLIO_BRANCHES)
def test_identity_matches_independent_length_framed_oracle(
    branch: str,
) -> None:
    value = _manifest(branch)
    assert (
        mmi_authenticated_evidence_bundle_identity_sha256(value)
        == _independent_identity(value)
    )


def test_identity_is_stable_under_all_mapping_insertion_order_changes() -> None:
    value = _manifest()
    reordered = _reverse_mapping_order(value)
    assert type(reordered) is dict
    assert reordered == value
    assert (
        mmi_authenticated_evidence_bundle_identity_sha256(reordered)
        == mmi_authenticated_evidence_bundle_identity_sha256(value)
    )


def test_all_three_presence_branches_have_distinct_identities() -> None:
    identities = {
        mmi_authenticated_evidence_bundle_identity_sha256(
            _manifest(branch)
        )
        for branch in PORTFOLIO_BRANCHES
    }
    assert len(identities) == 3


@pytest.mark.parametrize("branch", PORTFOLIO_BRANCHES)
def test_every_persistent_leaf_except_self_enters_identity_preimage(
    branch: str,
) -> None:
    value = _manifest(branch)
    original = _independent_identity(value)
    paths = [
        path
        for path in _leaf_paths(value)
        if path != ("evidence_bundle_identity_sha256",)
    ]
    assert paths
    for path in paths:
        candidate = deepcopy(value)
        current = _value_at_path(candidate, path)
        if type(current) is bool:
            replacement: object = not current
        elif type(current) is str and len(current) == 64:
            replacement = SHA_F if current != SHA_F else SHA_A
        elif path == ("evaluation_timestamp_utc",):
            replacement = "2026-07-26T12:34:56.123457Z"
        elif type(current) is str:
            replacement = f"{current}_MUTATED"
        else:
            raise AssertionError((path, current))
        _set_path(candidate, path, replacement)
        assert _independent_identity(candidate) != original, path
        try:
            calculated = (
                mmi_authenticated_evidence_bundle_identity_sha256(
                    candidate
                )
            )
        except MmiCanonicalizationError:
            continue
        assert calculated != original, path


def test_identity_self_field_is_the_only_excluded_field() -> None:
    value = _manifest()
    expected = mmi_authenticated_evidence_bundle_identity_sha256(value)
    value["evidence_bundle_identity_sha256"] = SHA_F
    assert (
        mmi_authenticated_evidence_bundle_identity_sha256(value)
        == expected
    )


def test_schema_valid_identity_resealing_is_not_source_authentication() -> None:
    value = _manifest()
    policy = value["policy_component"]
    portfolio = value["portfolio_component"]
    assert type(policy) is dict and type(portfolio) is dict
    policy["strategy_source_record_identity_sha256"] = SHA_F
    policy["policy_projection_identity_sha256"] = SHA_A
    portfolio["policy_projection_identity_sha256"] = SHA_A
    value["evidence_bundle_identity_sha256"] = _independent_identity(value)

    validate_artifact_schema(value, schema_name=SCHEMA_NAME)
    assert (
        mmi_authenticated_evidence_bundle_identity_sha256(value)
        == value["evidence_bundle_identity_sha256"]
    )
    assert not hasattr(
        contracts,
        "validate_mmi_authenticated_evidence_bundle",
    )
    assert tuple(
        inspect.signature(
            mmi_authenticated_evidence_bundle_identity_sha256
        ).parameters
    ) == ("value",)


def test_gap_addition_removal_and_any_attempted_reordering_are_invalid() -> None:
    removed = _manifest(NOT_SUPPLIED)
    removed["known_evidence_gaps"] = []

    added = _manifest()
    added["known_evidence_gaps"] = deepcopy(
        _manifest(NOT_SUPPLIED)["known_evidence_gaps"]
    )

    reordered = _manifest(NOT_SUPPLIED)
    gaps = reordered["known_evidence_gaps"]
    assert type(gaps) is list
    gaps.append(
        {
            "code": "EVIDENCE_PORTFOLIO_COMPONENT_NOT_SUPPLIED",
            "scope": "EVIDENCE_ASSEMBLY",
            "component": "PORTFOLIO_PROJECTION",
        }
    )
    gaps.reverse()

    for candidate in (removed, added, reordered):
        _assert_schema_rejected(candidate)
        _assert_identity_rejected(candidate)


def test_referenced_policy_and_source_relationship_mutations_are_bound() -> None:
    original = _manifest()

    changed_policy = deepcopy(original)
    policy = changed_policy["policy_component"]
    portfolio = changed_policy["portfolio_component"]
    assert type(policy) is dict and type(portfolio) is dict
    policy["policy_projection_identity_sha256"] = SHA_F
    portfolio["policy_projection_identity_sha256"] = SHA_F
    assert (
        mmi_authenticated_evidence_bundle_identity_sha256(
            changed_policy
        )
        != mmi_authenticated_evidence_bundle_identity_sha256(original)
    )

    assert (
        mmi_authenticated_evidence_bundle_identity_sha256(
            _manifest(SOURCE_ABSENT)
        )
        != mmi_authenticated_evidence_bundle_identity_sha256(original)
    )


def test_closed_schema_maximum_is_bounded_and_overbound_input_is_rejected() -> None:
    lengths = {
        branch: len(canonical_json_bytes(_manifest(branch)))
        for branch in PORTFOLIO_BRANCHES
    }
    assert lengths == {
        NOT_SUPPLIED: 1318,
        SOURCE_ABSENT: 1659,
        SOURCE_BOUND: 1884,
    }
    assert max(lengths.values()) == 1884
    assert (
        max(lengths.values())
        < MAXIMUM_AUTHENTICATED_EVIDENCE_BUNDLE_CANONICAL_BYTES
        == 2048
        <= 16_384
    )

    overbound = _manifest()
    overbound["unexpected_padding"] = "x" * 4096
    assert (
        len(
            canonical_json_bytes(
                overbound,
                maximum_bytes=16_384,
            )
        )
        > MAXIMUM_AUTHENTICATED_EVIDENCE_BUNDLE_CANONICAL_BYTES
    )
    with pytest.raises(
        MmiCanonicalizationError,
        match="MMI_CANONICAL_SIZE_EXCEEDED",
    ):
        canonical_json_bytes(
            overbound,
            maximum_bytes=(
                MAXIMUM_AUTHENTICATED_EVIDENCE_BUNDLE_CANONICAL_BYTES
            ),
        )
    _assert_schema_rejected(overbound)
    _assert_identity_rejected(overbound)


def test_identity_calculation_enforces_its_code_owned_bound(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    value = _manifest()
    preimage = deepcopy(value)
    preimage.pop("evidence_bundle_identity_sha256")
    preimage_size = len(canonical_json_bytes(preimage))
    monkeypatch.setattr(
        contracts,
        "MAXIMUM_AUTHENTICATED_EVIDENCE_BUNDLE_CANONICAL_BYTES",
        preimage_size - 1,
    )
    with pytest.raises(
        MmiCanonicalizationError,
        match="MMI_CANONICAL_SIZE_EXCEEDED",
    ):
        mmi_authenticated_evidence_bundle_identity_sha256(value)


def test_exactly_five_persistent_mmi_identity_domains_exist() -> None:
    domains_by_name = {
        name: value
        for name, value in canonical.__dict__.items()
        if name.startswith("MMI_")
        and name.endswith("_IDENTITY_DOMAIN")
    }
    assert domains_by_name == {
        "MMI_SOURCE_RECORD_IDENTITY_DOMAIN": (
            b"mmi_source_record_v1\0"
        ),
        "MMI_UNIVERSE_PROJECTION_IDENTITY_DOMAIN": (
            b"mmi_universe_projection_v1\0"
        ),
        "MMI_POLICY_PROJECTION_IDENTITY_DOMAIN": (
            b"mmi_policy_projection_v1\0"
        ),
        "MMI_PORTFOLIO_SNAPSHOT_PROJECTION_IDENTITY_DOMAIN": (
            b"mmi_portfolio_snapshot_projection_v1\0"
        ),
        "MMI_AUTHENTICATED_EVIDENCE_BUNDLE_IDENTITY_DOMAIN": (
            b"mmi_authenticated_evidence_bundle_v1\0"
        ),
    }
    domains = tuple(domains_by_name.values())
    assert len(domains) == len(set(domains)) == 5
    assert all(
        domain.endswith(b"\0")
        and b"\0" not in domain[:-1]
        and domain.decode("ascii")
        for domain in domains
    )


def test_existing_four_identity_domains_and_fixed_hashes_are_unchanged() -> None:
    fixtures = (
        (
            MMI_SOURCE_RECORD_IDENTITY_DOMAIN,
            "source_record_identity_sha256",
            "SOURCE",
            "5b1cc0a5ef02ecc271adcf21bd43db087"
            "e261a2f82f7bc873369d4ff5e1f435d",
        ),
        (
            MMI_UNIVERSE_PROJECTION_IDENTITY_DOMAIN,
            "universe_projection_identity_sha256",
            "UNIVERSE",
            "fbf1729e36c909530cabc60a131d4838"
            "547ef78d8f9bf767c338868d63e7bbf5",
        ),
        (
            MMI_POLICY_PROJECTION_IDENTITY_DOMAIN,
            "policy_projection_identity_sha256",
            "POLICY",
            "cbf39ca850907a4db732a856eb1a1318"
            "b13384c4d6b3af97b935b148158ce233",
        ),
        (
            MMI_PORTFOLIO_SNAPSHOT_PROJECTION_IDENTITY_DOMAIN,
            "portfolio_projection_identity_sha256",
            "PORTFOLIO",
            "371e25402d81be10369eb76b5d860587"
            "819030b912bd03dcef938f67ea66a9c1",
        ),
    )
    for domain, identity_field, kind, expected in fixtures:
        value = {
            "fixture_kind": kind,
            "fixture_version": 1,
            identity_field: "0" * 64,
        }
        assert (
            record_identity_sha256(
                value,
                identity_field=identity_field,
                domain=domain,
            )
            == expected
        )


@pytest.mark.parametrize(
    "forbidden_field",
    (
        "raw_strategy_source",
        "raw_portfolio_source",
        "raw_row",
        "account_id",
        "broker_id",
        "holdings",
        "sells",
        "tax_lots",
        "cost_basis",
        "monetary_total",
        "budget",
        "ticker",
        "universe_member",
        "quantity",
        "price_step",
        "instructions",
        "absolute_path",
        "parser_message",
        "provenance_token",
        "prompt",
        "response",
        "provider",
        "model",
        "permission",
        "gate",
        "readiness",
        "publication",
    ),
)
def test_privacy_or_authority_bearing_fields_cannot_enter_manifest(
    forbidden_field: str,
) -> None:
    value = _manifest()
    value[forbidden_field] = "forbidden"
    _assert_schema_rejected(value)
    _assert_identity_rejected(value)


def test_schema_has_no_privacy_authority_or_later_phase_vocabulary() -> None:
    schema_text = SCHEMA_PATH.read_text(encoding="utf-8").casefold()
    for forbidden in (
        "account",
        "broker",
        "holdings",
        "tax_lot",
        "cost_basis",
        "monetary",
        "budget",
        "ticker",
        "quantity",
        "price_step",
        "instruction",
        "absolute_path",
        "parser",
        "provenance",
        "prompt",
        "response",
        "provider",
        "model",
        "permission",
        "eligibility",
        "activation",
        "readiness",
        "publication",
        "pointer",
    ):
        assert forbidden not in schema_text


def test_new_constants_carry_only_contract_identity_relationships() -> None:
    values = (
        MMI_AUTHENTICATED_EVIDENCE_BUNDLE_SCHEMA_VERSION,
        MMI_AUTHENTICATED_EVIDENCE_BUNDLE_ARTIFACT_KIND,
        MMI_EVIDENCE_POLICY_COMPONENT_PRESENCE_STATUS,
        MMI_EVIDENCE_PORTFOLIO_NOT_SUPPLIED_STATUS,
        MMI_EVIDENCE_PORTFOLIO_SOURCE_ABSENT_STATUS,
        MMI_EVIDENCE_PORTFOLIO_SOURCE_BOUND_STATUS,
        MMI_EVIDENCE_PORTFOLIO_NOT_SUPPLIED_GAP_CODE,
        MMI_EVIDENCE_ASSEMBLY_GAP_SCOPE,
        MMI_EVIDENCE_PORTFOLIO_GAP_COMPONENT,
        AUTHORITY_EFFECT_NONE,
    )
    serialized = json.dumps(values).casefold()
    for forbidden in (
        "account",
        "broker",
        "holding",
        "tax",
        "budget",
        "ticker",
        "quantity",
        "price",
        "prompt",
        "response",
        "provider",
        "model",
        "permission",
        "gate",
        "readiness",
        "publication",
    ):
        assert forbidden not in serialized


def test_e1b_contract_and_e1c_runtime_have_exact_phase_ownership() -> None:
    root = repo_root()
    production_root = root / "src/investment_orchestrator"
    production_paths = tuple(sorted(production_root.rglob("*.py")))
    assert len(production_paths) == 134

    mmi_paths = tuple(
        sorted(
            path.relative_to(root).as_posix()
            for path in (root / "src/investment_orchestrator/mmi").glob(
                "*.py"
            )
        )
    )
    assert mmi_paths == (
        "src/investment_orchestrator/mmi/__init__.py",
        (
            "src/investment_orchestrator/mmi/"
            "analyst_visible_evidence_view.py"
        ),
        "src/investment_orchestrator/mmi/canonical.py",
        "src/investment_orchestrator/mmi/contracts.py",
        "src/investment_orchestrator/mmi/evidence_bundle.py",
        "src/investment_orchestrator/mmi/grounded_prompt.py",
        "src/investment_orchestrator/mmi/policy_projection.py",
        "src/investment_orchestrator/mmi/portfolio_projection.py",
        "src/investment_orchestrator/mmi/raw_response_envelope.py",
        "src/investment_orchestrator/mmi/source_capture.py",
        (
            "src/investment_orchestrator/mmi/"
            "validated_grounded_analysis_response.py"
        ),
    )

    relative_paths = {
        path: path.relative_to(root).as_posix()
        for path in production_paths
    }
    trees = {
        path: ast.parse(path.read_text(encoding="utf-8"))
        for path in production_paths
    }
    evidence_relative_path = (
        "src/investment_orchestrator/mmi/evidence_bundle.py"
    )
    analyst_view_relative_path = (
        "src/investment_orchestrator/mmi/"
        "analyst_visible_evidence_view.py"
    )
    evidence_path = root / evidence_relative_path
    contracts_path = (
        root / "src/investment_orchestrator/mmi/contracts.py"
    )
    canonical_path = (
        root / "src/investment_orchestrator/mmi/canonical.py"
    )
    init_path = root / "src/investment_orchestrator/mmi/__init__.py"

    evidence_named_paths = tuple(
        relative_paths[path]
        for path in production_paths
        if "evidence_bundle" in path.stem
    )
    assert evidence_named_paths == (evidence_relative_path,)

    public_surface_names = (
        "build_mmi_authenticated_evidence_bundle",
        "validate_mmi_authenticated_evidence_bundle",
    )

    def top_level_function_names(path: Path) -> tuple[str, ...]:
        return tuple(
            node.name
            for node in trees[path].body
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        )

    public_surface_owners = {
        name: tuple(
            relative_paths[path]
            for path in production_paths
            if name in top_level_function_names(path)
        )
        for name in public_surface_names
    }
    assert public_surface_owners == {
        name: (evidence_relative_path,)
        for name in public_surface_names
    }
    for contract_path in (contracts_path, canonical_path):
        assert not (
            set(top_level_function_names(contract_path))
            & set(public_surface_names)
        )

    evidence_tree = trees[evidence_path]
    evidence_public_functions = tuple(
        name
        for name in top_level_function_names(evidence_path)
        if not name.startswith("_")
    )
    evidence_public_classes = tuple(
        node.name
        for node in evidence_tree.body
        if isinstance(node, ast.ClassDef)
        and not node.name.startswith("_")
    )
    assert evidence_public_functions == public_surface_names
    assert evidence_public_classes == ()

    all_assignments = tuple(
        node
        for node in evidence_tree.body
        if isinstance(node, (ast.Assign, ast.AnnAssign))
        and any(
            isinstance(target, ast.Name) and target.id == "__all__"
            for target in (
                node.targets
                if isinstance(node, ast.Assign)
                else (node.target,)
            )
        )
    )
    assert len(all_assignments) == 1
    all_value = all_assignments[0].value
    assert ast.literal_eval(all_value) == public_surface_names

    public_assignments = tuple(
        target.id
        for node in evidence_tree.body
        if isinstance(node, (ast.Assign, ast.AnnAssign))
        for target in (
            node.targets
            if isinstance(node, ast.Assign)
            else (node.target,)
        )
        if isinstance(target, ast.Name)
        and not target.id.startswith("_")
    )
    assert public_assignments == ()

    evidence_module_name = (
        "investment_orchestrator.mmi.evidence_bundle"
    )

    def imported_modules(path: Path) -> tuple[str, ...]:
        relative_module_path = path.relative_to(root / "src")
        module_parts = list(relative_module_path.with_suffix("").parts)
        package_parts = module_parts[:-1]
        modules: list[str] = []
        for node in ast.walk(trees[path]):
            if isinstance(node, ast.Import):
                modules.extend(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                if node.level == 0:
                    base_parts = (
                        node.module.split(".")
                        if node.module is not None
                        else []
                    )
                else:
                    retained = len(package_parts) - (node.level - 1)
                    base_parts = package_parts[: max(retained, 0)]
                    if node.module is not None:
                        base_parts.extend(node.module.split("."))
                base = ".".join(base_parts)
                if base:
                    modules.append(base)
                modules.extend(
                    ".".join((*base_parts, alias.name))
                    for alias in node.names
                    if alias.name != "*"
                )
        return tuple(modules)

    evidence_importers = tuple(
        relative_paths[path]
        for path in production_paths
        if path != evidence_path
        and any(
            module == evidence_module_name
            or module.startswith(f"{evidence_module_name}.")
            for module in imported_modules(path)
        )
    )
    assert evidence_importers == (analyst_view_relative_path,)

    init_tree = trees[init_path]
    assert not any(
        isinstance(node, (ast.Import, ast.ImportFrom))
        for node in ast.walk(init_tree)
    )
    assert not any(
        isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and node.name in {"__getattr__", "__dir__"}
        for node in init_tree.body
    )
    init_all = tuple(
        node
        for node in init_tree.body
        if isinstance(node, ast.Assign)
        and any(
            isinstance(target, ast.Name) and target.id == "__all__"
            for target in node.targets
        )
    )
    assert len(init_all) == 1
    assert ast.literal_eval(init_all[0].value) == ()

    identity_helper_name = (
        "mmi_authenticated_evidence_bundle_identity_sha256"
    )
    identity_helper_definitions = tuple(
        relative_paths[path]
        for path in production_paths
        if identity_helper_name in top_level_function_names(path)
    )
    assert identity_helper_definitions == (
        "src/investment_orchestrator/mmi/contracts.py",
    )

    def loads_name(path: Path, name: str) -> bool:
        return any(
            (
                isinstance(node, ast.Name)
                and isinstance(node.ctx, ast.Load)
                and node.id == name
            )
            or (
                isinstance(node, ast.Attribute)
                and isinstance(node.ctx, ast.Load)
                and node.attr == name
            )
            for node in ast.walk(trees[path])
        )

    identity_helper_consumers = tuple(
        relative_paths[path]
        for path in production_paths
        if path != contracts_path
        and loads_name(path, identity_helper_name)
    )
    assert identity_helper_consumers == (evidence_relative_path,)

    schema_name_owners = tuple(
        relative_paths[path]
        for path in production_paths
        if any(
            isinstance(node, ast.Constant)
            and node.value == SCHEMA_NAME
            for node in ast.walk(trees[path])
        )
    )
    assert schema_name_owners == (evidence_relative_path,)
