from __future__ import annotations

import ast
from collections.abc import Iterator, Mapping
from copy import deepcopy
import hashlib
import json
import re
import struct
from types import MappingProxyType

from jsonschema import Draft202012Validator
import pytest

from investment_orchestrator.common.paths import repo_root
from investment_orchestrator.common.schema_validation import (
    ArtifactSchemaError,
    validate_artifact_schema,
)
from investment_orchestrator.mmi import canonical, contracts
from investment_orchestrator.mmi.canonical import (
    MAXIMUM_CANONICAL_DEPTH,
    MAXIMUM_CANONICAL_JSON_BYTES,
    MAXIMUM_CANONICAL_NODES,
    _MMI_ANALYST_VISIBLE_EVIDENCE_VIEW_IDENTITY_DOMAIN,
    _MMI_ANALYST_VISIBLE_EVIDENCE_VIEW_V2_IDENTITY_DOMAIN,
    _MMI_GROUNDED_PROMPT_ARTIFACT_IDENTITY_DOMAIN,
    _MMI_GROUNDED_PROMPT_CONTEXT_BINDING_DOMAIN,
    _MMI_RAW_RESPONSE_ENVELOPE_IDENTITY_DOMAIN,
    _MMI_VALIDATED_GROUNDED_ANALYSIS_RESPONSE_IDENTITY_DOMAIN,
    MMI_AUTHENTICATED_EVIDENCE_BUNDLE_IDENTITY_DOMAIN,
    MMI_POLICY_PROJECTION_IDENTITY_DOMAIN,
    MMI_PORTFOLIO_SNAPSHOT_PROJECTION_IDENTITY_DOMAIN,
    MMI_SOURCE_RECORD_IDENTITY_DOMAIN,
    MMI_UNIVERSE_PROJECTION_IDENTITY_DOMAIN,
    MmiCanonicalizationError,
)
from investment_orchestrator.mmi.contracts import (
    MMI_GROUNDED_PROMPT_EXPECTED_RESPONSE_SCHEMA_VERSION,
    MMI_VALIDATED_GROUNDED_ANALYSIS_RESPONSE_ARTIFACT_KIND,
    MMI_VALIDATED_GROUNDED_ANALYSIS_RESPONSE_SCHEMA_VERSION,
    mmi_validated_grounded_analysis_response_identity_sha256,
)


SCHEMA_NAME = (
    "mmi_validated_grounded_analysis_response_v1.schema.json"
)
SCHEMA_PATH = repo_root() / "schemas" / SCHEMA_NAME
IDENTITY_DOMAIN = b"mmi_validated_grounded_analysis_response_v1\0"
IDENTITY_FIELD = (
    "validated_grounded_analysis_response_identity_sha256"
)
R1_IDENTITY_FIELD = "raw_response_envelope_identity_sha256"
PAYLOAD_FIELD = "response_payload"
CONTEXT_FIELD = "prompt_context_binding_sha256"
EXPECTED_FIELDS = frozenset(
    {
        "schema_version",
        "artifact_kind",
        "report_only",
        "authority_effect",
        "manual_handoff_required",
        R1_IDENTITY_FIELD,
        PAYLOAD_FIELD,
        IDENTITY_FIELD,
    }
)
EXPECTED_PREIMAGE_FIELDS = EXPECTED_FIELDS - {IDENTITY_FIELD}
EXPECTED_PAYLOAD_FIELDS = frozenset(
    {
        "response_schema_version",
        CONTEXT_FIELD,
        "analysis_status",
        "evidence_observations",
        "risks",
        "uncertainties",
        "contradictions",
        "research_questions",
        "summary",
    }
)
EXPECTED_ITEM_FIELDS = frozenset(
    {
        "text",
        "references",
        "hypothesis",
    }
)
EXPECTED_STATUSES = frozenset(
    {
        "QUALITATIVE_ANALYSIS_PROVIDED",
        "INSUFFICIENT_EVIDENCE",
        "EVIDENCE_CONTRADICTIONS_IDENTIFIED",
    }
)
ARRAY_LIMITS = {
    "evidence_observations": 12,
    "risks": 12,
    "uncertainties": 12,
    "contradictions": 8,
    "research_questions": 12,
}
SCALAR_REFERENCES = frozenset(
    {
        "VIEW.EVALUATION_TIMESTAMP",
        "VIEW.COMPLETENESS_STATUS",
        "POLICY.AS_OF_DATE",
        "POLICY.METHOD",
        "POLICY.BENCHMARK.0001",
        "POLICY.EXTENDED_ACTIVATION_STATUS",
        "POLICY.INSTRUMENT_AVAILABILITY_STATUS",
        "POLICY.TARGET_WEIGHTS_ABSENCE_REASON",
        "PORTFOLIO.PRESENCE_STATUS",
        "PORTFOLIO.SOURCE_DATE",
        "PORTFOLIO.OPEN_BUY_STATUS",
        "PORTFOLIO.COVERAGE.HOLDINGS",
        "PORTFOLIO.COVERAGE.CASH",
        "PORTFOLIO.COVERAGE.DEPLOYABLE_CASH",
        "PORTFOLIO.COVERAGE.OPEN_SELLS",
        "PORTFOLIO.COVERAGE.TAX_LOTS",
        "PORTFOLIO.COVERAGE.HOLDING_DATES",
        "PORTFOLIO.COVERAGE.GAINS_LOSSES",
        "PORTFOLIO.COVERAGE.WEIGHTS",
        "PORTFOLIO.COVERAGE.NAV_CONCENTRATION",
        "PORTFOLIO.COVERAGE.LOOK_THROUGH_EXPOSURE",
    }
)
MAXIMUM_SEMANTIC_TEXT_BYTES = 116_000
MAXIMUM_RESPONSE_PAYLOAD_BYTES = 716_216
MAXIMUM_COMPLETE_ARTIFACT_BYTES = 716_664
MAXIMUM_IDENTITY_PREIMAGE_BYTES = 716_542
MAXIMUM_FRAMED_IDENTITY_BYTES = 716_594
MAXIMUM_ARTIFACT_NODES = 701
MAXIMUM_ARTIFACT_DEPTH = 5


def _canonical(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def _independent_identity(value: Mapping[str, object]) -> str:
    preimage = deepcopy(dict(value))
    preimage.pop(IDENTITY_FIELD, None)
    canonical_preimage = _canonical(preimage)
    return hashlib.sha256(
        IDENTITY_DOMAIN
        + struct.pack(">Q", len(canonical_preimage))
        + canonical_preimage
    ).hexdigest()


def _item(
    text: str = "Evidence-linked observation.",
    *,
    references: list[str] | None = None,
    hypothesis: bool = False,
) -> dict[str, object]:
    return {
        "text": text,
        "references": (
            ["VIEW.EVALUATION_TIMESTAMP"]
            if references is None
            else list(references)
        ),
        "hypothesis": hypothesis,
    }


def _payload() -> dict[str, object]:
    return {
        "response_schema_version": (
            "mmi_grounded_analysis_response_v1"
        ),
        CONTEXT_FIELD: "2" * 64,
        "analysis_status": "QUALITATIVE_ANALYSIS_PROVIDED",
        "evidence_observations": [_item()],
        "risks": [],
        "uncertainties": [],
        "contradictions": [],
        "research_questions": [],
        "summary": _item("Research-only synthesis."),
    }


def _artifact() -> dict[str, object]:
    value: dict[str, object] = {
        "schema_version": (
            "mmi_validated_grounded_analysis_response_v1"
        ),
        "artifact_kind": "MMI_VALIDATED_GROUNDED_ANALYSIS_RESPONSE",
        "report_only": True,
        "authority_effect": "NONE",
        "manual_handoff_required": True,
        R1_IDENTITY_FIELD: "1" * 64,
        PAYLOAD_FIELD: _payload(),
        IDENTITY_FIELD: "0" * 64,
    }
    value[IDENTITY_FIELD] = _independent_identity(value)
    return value


def _reseal(value: dict[str, object]) -> dict[str, object]:
    candidate = deepcopy(value)
    candidate[IDENTITY_FIELD] = _independent_identity(candidate)
    assert candidate[IDENTITY_FIELD] == _independent_identity(
        candidate
    )
    return candidate


def _schema() -> dict[str, object]:
    value = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    assert type(value) is dict
    return value


def _assert_schema_rejected(value: object) -> None:
    with pytest.raises(ArtifactSchemaError):
        validate_artifact_schema(value, schema_name=SCHEMA_NAME)


def _assert_structural_rejected(value: object) -> None:
    with pytest.raises(MmiCanonicalizationError):
        mmi_validated_grounded_analysis_response_identity_sha256(
            value  # type: ignore[arg-type]
        )


def _assert_resealed_structural_rejection(
    candidate: dict[str, object],
) -> None:
    resealed = _reseal(candidate)
    assert resealed[IDENTITY_FIELD] == _independent_identity(
        resealed
    )
    _assert_structural_rejected(resealed)


def _maximum_references() -> list[str]:
    return sorted(
        SCALAR_REFERENCES,
        key=lambda value: (len(value), value),
        reverse=True,
    )[:8]


def _maximum_payload() -> dict[str, object]:
    references = _maximum_references()

    def maximum_item(text_bytes: int) -> dict[str, object]:
        return _item(
            "\0" * text_bytes,
            references=references,
            hypothesis=False,
        )

    return {
        "response_schema_version": (
            "mmi_grounded_analysis_response_v1"
        ),
        CONTEXT_FIELD: "f" * 64,
        "analysis_status": "EVIDENCE_CONTRADICTIONS_IDENTIFIED",
        "evidence_observations": [
            maximum_item(2_000) for _ in range(12)
        ],
        "risks": [maximum_item(2_000) for _ in range(12)],
        "uncertainties": [
            maximum_item(2_000) for _ in range(12)
        ],
        "contradictions": [
            maximum_item(2_000) for _ in range(8)
        ],
        "research_questions": [
            maximum_item(2_000) for _ in range(12)
        ],
        "summary": maximum_item(4_000),
    }


def _maximum_artifact() -> dict[str, object]:
    value = _artifact()
    value[PAYLOAD_FIELD] = _maximum_payload()
    return _reseal(value)


def _node_count(value: object) -> int:
    if type(value) is dict:
        return 1 + sum(_node_count(member) for member in value.values())
    if type(value) is list:
        return 1 + sum(_node_count(member) for member in value)
    return 1


def _maximum_depth(value: object, *, depth: int = 0) -> int:
    if type(value) is dict:
        return max(
            [depth]
            + [
                _maximum_depth(member, depth=depth + 1)
                for member in value.values()
            ]
        )
    if type(value) is list:
        return max(
            [depth]
            + [
                _maximum_depth(member, depth=depth + 1)
                for member in value
            ]
        )
    return depth


class _DuplicateKeyMapping(Mapping[str, object]):
    def __init__(self, value: Mapping[str, object]) -> None:
        self._value = dict(value)
        self._keys = (*self._value, "schema_version")

    def __getitem__(self, key: str) -> object:
        return self._value[key]

    def __iter__(self) -> Iterator[str]:
        return iter(self._keys)

    def __len__(self) -> int:
        return len(self._keys)

    def keys(self) -> tuple[str, ...]:
        return self._keys


def test_schema_is_closed_draft_2020_12_with_exact_wrapper() -> None:
    schema = _schema()
    assert schema["$schema"] == (
        "https://json-schema.org/draft/2020-12/schema"
    )
    assert schema["$id"] == (
        "https://investment-system.local/schemas/"
        "mmi_validated_grounded_analysis_response_v1.schema.json"
    )
    assert schema["type"] == "object"
    assert schema["additionalProperties"] is False
    assert set(schema["required"]) == EXPECTED_FIELDS
    assert set(schema["properties"]) == EXPECTED_FIELDS
    properties = schema["properties"]
    assert properties["schema_version"] == {
        "const": "mmi_validated_grounded_analysis_response_v1"
    }
    assert properties["artifact_kind"] == {
        "const": "MMI_VALIDATED_GROUNDED_ANALYSIS_RESPONSE"
    }
    assert properties["report_only"] == {"const": True}
    assert properties["authority_effect"] == {"const": "NONE"}
    assert properties["manual_handoff_required"] == {"const": True}
    assert properties[R1_IDENTITY_FIELD] == {
        "$ref": "#/$defs/sha256"
    }
    assert properties[PAYLOAD_FIELD] == {
        "$ref": "#/$defs/response_payload"
    }
    assert properties[IDENTITY_FIELD] == {
        "$ref": "#/$defs/sha256"
    }
    Draft202012Validator.check_schema(schema)


def test_nested_response_payload_schema_is_exact_and_closed() -> None:
    payload = _schema()["$defs"]["response_payload"]
    assert payload["type"] == "object"
    assert payload["additionalProperties"] is False
    assert set(payload["required"]) == EXPECTED_PAYLOAD_FIELDS
    assert set(payload["properties"]) == EXPECTED_PAYLOAD_FIELDS
    properties = payload["properties"]
    assert properties["response_schema_version"] == {
        "const": "mmi_grounded_analysis_response_v1"
    }
    assert properties[CONTEXT_FIELD] == {
        "$ref": "#/$defs/sha256"
    }
    assert frozenset(properties["analysis_status"]["enum"]) == (
        EXPECTED_STATUSES
    )


def test_analysis_item_summary_and_reference_shapes_are_exact() -> None:
    definitions = _schema()["$defs"]
    for name, maximum in (
        ("analysis_item", 2_000),
        ("summary", 4_000),
    ):
        item = definitions[name]
        assert item["type"] == "object"
        assert item["additionalProperties"] is False
        assert set(item["required"]) == EXPECTED_ITEM_FIELDS
        assert set(item["properties"]) == EXPECTED_ITEM_FIELDS
        assert item["properties"]["text"] == {
            "type": "string",
            "minLength": 1,
            "maxLength": maximum,
        }
        assert item["properties"]["references"] == {
            "$ref": "#/$defs/references"
        }
        assert item["properties"]["hypothesis"] == {
            "type": "boolean"
        }
    assert definitions["references"] == {
        "type": "array",
        "minItems": 1,
        "maxItems": 8,
        "uniqueItems": True,
        "items": {"$ref": "#/$defs/evidence_reference"},
    }


def test_task_array_bounds_are_exact_without_semantic_cross_rules() -> None:
    definitions = _schema()["$defs"]
    payload_properties = definitions["response_payload"]["properties"]
    for field, maximum in ARRAY_LIMITS.items():
        definition_name = (
            "analysis_items_8"
            if maximum == 8
            else "analysis_items_12"
        )
        assert payload_properties[field] == {
            "$ref": f"#/$defs/{definition_name}"
        }
        assert definitions[definition_name]["minItems"] == 0
        assert definitions[definition_name]["maxItems"] == maximum
        assert "uniqueItems" not in definitions[definition_name]
    assert "allOf" not in definitions["response_payload"]
    assert "if" not in definitions["response_payload"]


def test_static_reference_schema_has_exact_scalar_catalog() -> None:
    alternatives = _schema()["$defs"]["evidence_reference"]["oneOf"]
    assert frozenset(alternatives[0]["enum"]) == SCALAR_REFERENCES
    assert len(SCALAR_REFERENCES) == 21
    assert len(alternatives) == 4
    assert all(
        alternative.get("type") == "string"
        for alternative in alternatives[1:]
    )


def test_fixed_public_contract_surface_is_exact() -> None:
    assert (
        MMI_VALIDATED_GROUNDED_ANALYSIS_RESPONSE_SCHEMA_VERSION
        == "mmi_validated_grounded_analysis_response_v1"
    )
    assert (
        MMI_VALIDATED_GROUNDED_ANALYSIS_RESPONSE_ARTIFACT_KIND
        == "MMI_VALIDATED_GROUNDED_ANALYSIS_RESPONSE"
    )
    assert (
        MMI_GROUNDED_PROMPT_EXPECTED_RESPONSE_SCHEMA_VERSION
        == "mmi_grounded_analysis_response_v1"
    )
    assert {
        name
        for name in contracts.__dict__
        if "VALIDATED_GROUNDED_ANALYSIS_RESPONSE" in name
        and not name.startswith("_")
    } == {
        "MMI_VALIDATED_GROUNDED_ANALYSIS_RESPONSE_SCHEMA_VERSION",
        "MMI_VALIDATED_GROUNDED_ANALYSIS_RESPONSE_ARTIFACT_KIND",
    }
    assert {
        name
        for name in contracts.__dict__
        if name.startswith(
            "mmi_validated_grounded_analysis_response"
        )
    } == {
        "mmi_validated_grounded_analysis_response_identity_sha256"
    }
    assert not {
        name
        for name in canonical.__dict__
        if "VALIDATED_GROUNDED_ANALYSIS_RESPONSE" in name
        and not name.startswith("_")
    }


def test_valid_artifact_passes_schema_and_structural_oracles() -> None:
    value = _artifact()
    validate_artifact_schema(value, schema_name=SCHEMA_NAME)
    assert tuple(
        Draft202012Validator(_schema()).iter_errors(value)
    ) == ()
    assert (
        mmi_validated_grounded_analysis_response_identity_sha256(
            value
        )
        == value[IDENTITY_FIELD]
        == _independent_identity(value)
    )


def test_identity_is_mapping_insertion_order_independent() -> None:
    value = _artifact()
    reversed_value = dict(reversed(tuple(value.items())))
    assert tuple(reversed_value) != tuple(value)
    assert _independent_identity(reversed_value) == (
        value[IDENTITY_FIELD]
    )
    assert (
        mmi_validated_grounded_analysis_response_identity_sha256(
            reversed_value
        )
        == value[IDENTITY_FIELD]
    )


def test_identity_preimage_binds_every_nonself_field() -> None:
    baseline = _artifact()
    mutations: dict[str, object] = {
        "schema_version": "mmi_validated_grounded_analysis_response_v2",
        "artifact_kind": "MMI_OTHER",
        "report_only": False,
        "authority_effect": "OTHER",
        "manual_handoff_required": False,
        R1_IDENTITY_FIELD: "3" * 64,
        PAYLOAD_FIELD: {
            **_payload(),
            CONTEXT_FIELD: "4" * 64,
        },
    }
    assert set(mutations) == EXPECTED_PREIMAGE_FIELDS
    identities = {
        field: _independent_identity(
            {
                **baseline,
                field: replacement,
            }
        )
        for field, replacement in mutations.items()
    }
    assert all(
        identity != baseline[IDENTITY_FIELD]
        for identity in identities.values()
    )
    assert len(set(identities.values())) == len(mutations)


def test_top_level_identity_only_mutation_fails() -> None:
    value = _artifact()
    value[IDENTITY_FIELD] = "f" * 64
    _assert_structural_rejected(value)


def test_existing_ten_domains_are_unchanged_and_v2_is_unique() -> None:
    first_nine = (
        MMI_SOURCE_RECORD_IDENTITY_DOMAIN,
        MMI_UNIVERSE_PROJECTION_IDENTITY_DOMAIN,
        MMI_POLICY_PROJECTION_IDENTITY_DOMAIN,
        MMI_PORTFOLIO_SNAPSHOT_PROJECTION_IDENTITY_DOMAIN,
        MMI_AUTHENTICATED_EVIDENCE_BUNDLE_IDENTITY_DOMAIN,
        _MMI_ANALYST_VISIBLE_EVIDENCE_VIEW_IDENTITY_DOMAIN,
        _MMI_GROUNDED_PROMPT_CONTEXT_BINDING_DOMAIN,
        _MMI_GROUNDED_PROMPT_ARTIFACT_IDENTITY_DOMAIN,
        _MMI_RAW_RESPONSE_ENVELOPE_IDENTITY_DOMAIN,
    )
    assert first_nine == (
        b"mmi_source_record_v1\0",
        b"mmi_universe_projection_v1\0",
        b"mmi_policy_projection_v1\0",
        b"mmi_portfolio_snapshot_projection_v1\0",
        b"mmi_authenticated_evidence_bundle_v1\0",
        b"mmi_analyst_visible_evidence_view_v1\0",
        b"mmi_grounded_prompt_context_binding_v1\0",
        b"mmi_grounded_prompt_artifact_v1\0",
        b"mmi_raw_response_envelope_v1\0",
    )
    assert (
        _MMI_VALIDATED_GROUNDED_ANALYSIS_RESPONSE_IDENTITY_DOMAIN
        == IDENTITY_DOMAIN
    )
    all_domains = (
        *first_nine,
        _MMI_VALIDATED_GROUNDED_ANALYSIS_RESPONSE_IDENTITY_DOMAIN,
        _MMI_ANALYST_VISIBLE_EVIDENCE_VIEW_V2_IDENTITY_DOMAIN,
    )
    assert _MMI_ANALYST_VISIBLE_EVIDENCE_VIEW_V2_IDENTITY_DOMAIN == (
        b"mmi_analyst_visible_evidence_view_v2\0"
    )
    assert len(all_domains) == len(set(all_domains)) == 11
    assert all(
        domain.endswith(b"\0")
        and b"\0" not in domain[:-1]
        and domain.decode("ascii")
        for domain in all_domains
    )


@pytest.mark.parametrize(
    "reference",
    (
        "POLICY.INSTRUMENT.0001",
        "POLICY.INSTRUMENT.0099",
        "POLICY.INSTRUMENT.0100",
        "POLICY.INSTRUMENT.0256",
        "PORTFOLIO.OBSERVATION.0001",
        "PORTFOLIO.OBSERVATION.0256",
        "LIMITATION.0001",
        "LIMITATION.0014",
    ),
)
def test_numbered_reference_boundaries_are_accepted(
    reference: str,
) -> None:
    value = _artifact()
    value[PAYLOAD_FIELD]["summary"]["references"] = [  # type: ignore[index]
        reference
    ]
    value = _reseal(value)
    validate_artifact_schema(value, schema_name=SCHEMA_NAME)
    assert (
        mmi_validated_grounded_analysis_response_identity_sha256(
            value
        )
        == value[IDENTITY_FIELD]
    )


def test_every_scalar_reference_is_accepted() -> None:
    for reference in sorted(SCALAR_REFERENCES):
        value = _artifact()
        value[PAYLOAD_FIELD]["summary"]["references"] = [  # type: ignore[index]
            reference
        ]
        value = _reseal(value)
        validate_artifact_schema(value, schema_name=SCHEMA_NAME)
        assert (
            mmi_validated_grounded_analysis_response_identity_sha256(
                value
            )
            == value[IDENTITY_FIELD]
        )


@pytest.mark.parametrize(
    "reference",
    (
        "POLICY.INSTRUMENT.0000",
        "POLICY.INSTRUMENT.0257",
        "PORTFOLIO.OBSERVATION.0000",
        "PORTFOLIO.OBSERVATION.0257",
        "LIMITATION.0000",
        "LIMITATION.0015",
        "POLICY.INSTRUMENT.1",
        "policy.instrument.0001",
        " SOURCE.PATH",
        "SOURCE.IDENTITY",
        "PORTFOLIO/OBSERVATION/0001",
        "POLICY.INSTRUMENT.0001.extra",
    ),
)
def test_invalid_static_references_are_rejected(
    reference: str,
) -> None:
    value = _artifact()
    value[PAYLOAD_FIELD]["summary"]["references"] = [  # type: ignore[index]
        reference
    ]
    value = _reseal(value)
    _assert_schema_rejected(value)
    _assert_structural_rejected(value)


def test_reference_order_is_preserved_and_identity_bound() -> None:
    value = _artifact()
    first = [
        "VIEW.EVALUATION_TIMESTAMP",
        "POLICY.METHOD",
    ]
    second = list(reversed(first))
    value[PAYLOAD_FIELD]["summary"]["references"] = first  # type: ignore[index]
    first_value = _reseal(value)
    value[PAYLOAD_FIELD]["summary"]["references"] = second  # type: ignore[index]
    second_value = _reseal(value)
    assert first_value[PAYLOAD_FIELD]["summary"]["references"] == first  # type: ignore[index]
    assert second_value[PAYLOAD_FIELD]["summary"]["references"] == second  # type: ignore[index]
    assert first_value[IDENTITY_FIELD] != second_value[IDENTITY_FIELD]


@pytest.mark.parametrize(
    ("text", "summary", "accepted"),
    (
        ("a" * 2_000, False, True),
        ("é" * 1_000, False, True),
        ("é" * 1_001, False, False),
        ("a" * 4_000, True, True),
        ("é" * 2_000, True, True),
        ("é" * 2_001, True, False),
    ),
)
def test_utf8_text_byte_boundaries_are_structural(
    text: str,
    summary: bool,
    accepted: bool,
) -> None:
    value = _artifact()
    if summary:
        value[PAYLOAD_FIELD]["summary"]["text"] = text  # type: ignore[index]
    else:
        value[PAYLOAD_FIELD]["evidence_observations"][0]["text"] = text  # type: ignore[index]
    value = _reseal(value)
    assert len(text.encode("utf-8")) == (
        (4_000 if summary else 2_000)
        + (2 if not accepted else 0)
    )
    if accepted:
        validate_artifact_schema(value, schema_name=SCHEMA_NAME)
        assert (
            mmi_validated_grounded_analysis_response_identity_sha256(
                value
            )
            == value[IDENTITY_FIELD]
        )
    else:
        assert tuple(
            Draft202012Validator(_schema()).iter_errors(value)
        ) == ()
        _assert_structural_rejected(value)


def test_empty_and_unpaired_surrogate_text_fail_closed() -> None:
    empty = _artifact()
    empty[PAYLOAD_FIELD]["summary"]["text"] = ""  # type: ignore[index]
    empty = _reseal(empty)
    _assert_structural_rejected(empty)
    _assert_schema_rejected(empty)

    surrogate = _artifact()
    surrogate[PAYLOAD_FIELD]["summary"]["text"] = "\ud800"  # type: ignore[index]
    _assert_structural_rejected(surrogate)


def test_all_statuses_are_structurally_valid_without_cross_rules() -> None:
    for status in sorted(EXPECTED_STATUSES):
        value = _artifact()
        payload = value[PAYLOAD_FIELD]
        payload["analysis_status"] = status  # type: ignore[index]
        for field in ARRAY_LIMITS:
            payload[field] = []  # type: ignore[index]
        value = _reseal(value)
        validate_artifact_schema(value, schema_name=SCHEMA_NAME)
        assert (
            mmi_validated_grounded_analysis_response_identity_sha256(
                value
            )
            == value[IDENTITY_FIELD]
        )


def test_each_task_array_accepts_its_maximum_and_rejects_one_over() -> None:
    for field, maximum in ARRAY_LIMITS.items():
        value = _artifact()
        value[PAYLOAD_FIELD][field] = [  # type: ignore[index]
            _item(f"{field}-{index}") for index in range(maximum)
        ]
        maximum_value = _reseal(value)
        validate_artifact_schema(maximum_value, schema_name=SCHEMA_NAME)
        assert (
            mmi_validated_grounded_analysis_response_identity_sha256(
                maximum_value
            )
            == maximum_value[IDENTITY_FIELD]
        )
        value[PAYLOAD_FIELD][field].append(_item("one-over"))  # type: ignore[index,union-attr]
        over = _reseal(value)
        _assert_schema_rejected(over)
        _assert_structural_rejected(over)


def test_references_accept_one_to_eight_and_reject_zero_or_nine() -> None:
    available = sorted(SCALAR_REFERENCES)
    for count in (1, 8):
        value = _artifact()
        value[PAYLOAD_FIELD]["summary"]["references"] = available[:count]  # type: ignore[index]
        value = _reseal(value)
        validate_artifact_schema(value, schema_name=SCHEMA_NAME)
        assert (
            mmi_validated_grounded_analysis_response_identity_sha256(
                value
            )
            == value[IDENTITY_FIELD]
        )
    for references in ([], available[:9]):
        value = _artifact()
        value[PAYLOAD_FIELD]["summary"]["references"] = references  # type: ignore[index]
        value = _reseal(value)
        _assert_schema_rejected(value)
        _assert_structural_rejected(value)


def test_duplicate_references_are_rejected_when_resealed() -> None:
    value = _artifact()
    value[PAYLOAD_FIELD]["summary"]["references"] = [  # type: ignore[index]
        "POLICY.METHOD",
        "POLICY.METHOD",
    ]
    _assert_resealed_structural_rejection(value)
    _assert_schema_rejected(_reseal(value))


@pytest.mark.parametrize(
    ("field", "replacement"),
    (
        (R1_IDENTITY_FIELD, "not-a-hash"),
        ("report_only", False),
        ("authority_effect", "READ_ONLY"),
        ("manual_handoff_required", False),
    ),
)
def test_resealed_wrapper_contradictions_reach_wrapper_validation(
    field: str,
    replacement: object,
) -> None:
    value = _artifact()
    value[field] = replacement
    resealed = _reseal(value)
    assert resealed[IDENTITY_FIELD] == _independent_identity(resealed)
    _assert_schema_rejected(resealed)
    _assert_structural_rejected(resealed)


def test_resealed_payload_constant_and_hash_contradictions_are_rejected() -> None:
    mutations = (
        ("response_schema_version", "mmi_grounded_analysis_response_v2"),
        (CONTEXT_FIELD, "NOT_A_HASH"),
        ("analysis_status", "BUY"),
    )
    for field, replacement in mutations:
        value = _artifact()
        value[PAYLOAD_FIELD][field] = replacement  # type: ignore[index]
        resealed = _reseal(value)
        assert resealed[IDENTITY_FIELD] == _independent_identity(
            resealed
        )
        _assert_schema_rejected(resealed)
        _assert_structural_rejected(resealed)


def test_other_valid_r1_and_context_identities_are_structurally_valid() -> None:
    value = _artifact()
    value[R1_IDENTITY_FIELD] = "a" * 64
    value[PAYLOAD_FIELD][CONTEXT_FIELD] = "b" * 64  # type: ignore[index]
    value = _reseal(value)
    validate_artifact_schema(value, schema_name=SCHEMA_NAME)
    assert (
        mmi_validated_grounded_analysis_response_identity_sha256(
            value
        )
        == value[IDENTITY_FIELD]
    )


@pytest.mark.parametrize(
    ("location", "mutation"),
    (
        ("wrapper", "missing"),
        ("wrapper", "extra"),
        ("payload", "missing"),
        ("payload", "extra"),
        ("item", "missing"),
        ("item", "extra"),
    ),
)
def test_representative_closed_shape_mutations_are_rejected(
    location: str,
    mutation: str,
) -> None:
    value = _artifact()
    if location == "wrapper":
        target = value
        field = "report_only"
    elif location == "payload":
        target = value[PAYLOAD_FIELD]  # type: ignore[assignment]
        field = "analysis_status"
    else:
        target = value[PAYLOAD_FIELD]["summary"]  # type: ignore[index,assignment]
        field = "hypothesis"
    if mutation == "missing":
        target.pop(field)  # type: ignore[union-attr]
    else:
        target["notes"] = "forbidden"  # type: ignore[index]
    resealed = _reseal(value)
    _assert_schema_rejected(resealed)
    _assert_structural_rejected(resealed)


@pytest.mark.parametrize(
    "value",
    (
        [],
        {"not": "the contract"},
    ),
    ids=("non-mapping", "wrong-mapping"),
)
def test_unsupported_top_level_values_fail_closed(value: object) -> None:
    _assert_structural_rejected(value)


def test_duplicate_mapping_keys_and_nested_nonbuiltin_containers_fail() -> None:
    _assert_structural_rejected(_DuplicateKeyMapping(_artifact()))

    value = _artifact()
    value[PAYLOAD_FIELD] = MappingProxyType(
        dict(value[PAYLOAD_FIELD])  # type: ignore[arg-type]
    )
    _assert_structural_rejected(value)

    value = _artifact()
    value[PAYLOAD_FIELD]["risks"] = ()  # type: ignore[index]
    value = _reseal(value)
    _assert_structural_rejected(value)

    value = _artifact()
    value[PAYLOAD_FIELD]["summary"]["hypothesis"] = 1  # type: ignore[index]
    value = _reseal(value)
    _assert_schema_rejected(value)
    _assert_structural_rejected(value)

    value = _artifact()
    value[PAYLOAD_FIELD]["analysis_status"] = []  # type: ignore[index]
    value = _reseal(value)
    _assert_schema_rejected(value)
    _assert_structural_rejected(value)


def test_maximum_structural_size_arithmetic_is_exact() -> None:
    value = _maximum_artifact()
    payload = value[PAYLOAD_FIELD]
    preimage = dict(value)
    preimage.pop(IDENTITY_FIELD)
    framed = (
        IDENTITY_DOMAIN
        + struct.pack(">Q", len(_canonical(preimage)))
        + _canonical(preimage)
    )
    assert sum(
        len(item["text"].encode("utf-8"))
        for field in ARRAY_LIMITS
        for item in payload[field]  # type: ignore[index,union-attr]
    ) + len(payload["summary"]["text"].encode("utf-8")) == (  # type: ignore[index,union-attr]
        MAXIMUM_SEMANTIC_TEXT_BYTES
    )
    assert len(_canonical(payload)) == MAXIMUM_RESPONSE_PAYLOAD_BYTES
    assert len(_canonical(value)) == MAXIMUM_COMPLETE_ARTIFACT_BYTES
    assert len(_canonical(preimage)) == (
        MAXIMUM_IDENTITY_PREIMAGE_BYTES
    )
    assert len(framed) == MAXIMUM_FRAMED_IDENTITY_BYTES
    assert _node_count(value) == MAXIMUM_ARTIFACT_NODES
    assert _maximum_depth(value) == MAXIMUM_ARTIFACT_DEPTH
    assert MAXIMUM_COMPLETE_ARTIFACT_BYTES < (
        MAXIMUM_CANONICAL_JSON_BYTES
    )
    assert MAXIMUM_ARTIFACT_NODES < MAXIMUM_CANONICAL_NODES
    assert MAXIMUM_ARTIFACT_DEPTH < MAXIMUM_CANONICAL_DEPTH
    validate_artifact_schema(value, schema_name=SCHEMA_NAME)
    assert (
        mmi_validated_grounded_analysis_response_identity_sha256(
            value
        )
        == value[IDENTITY_FIELD]
    )


def test_raw_action_shaped_prose_remains_inert_report_only_data() -> None:
    value = _artifact()
    value[PAYLOAD_FIELD]["summary"]["text"] = (  # type: ignore[index]
        "BUY SELL HOLD NO_TRADE NEW_BUY ORDER_COMPILATION "
        "permission gate budget quantity execution"
    )
    value = _reseal(value)
    validate_artifact_schema(value, schema_name=SCHEMA_NAME)
    assert value["report_only"] is True
    assert value["authority_effect"] == "NONE"
    assert value["manual_handoff_required"] is True
    assert (
        mmi_validated_grounded_analysis_response_identity_sha256(
            value
        )
        == value[IDENTITY_FIELD]
    )


def test_contract_and_runtime_have_exact_phase_and_inventory() -> None:
    root = repo_root()
    production_paths = tuple(
        sorted((root / "src/investment_orchestrator").rglob("*.py"))
    )
    assert len(production_paths) == 148
    runtime_path = (
        root
        / "src/investment_orchestrator/mmi/"
        "validated_grounded_analysis_response.py"
    )
    assert runtime_path.is_file()
    module_name = (
        "investment_orchestrator.mmi."
        "validated_grounded_analysis_response"
    )
    importers: list[str] = []
    for path in production_paths:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        imported = {
            node.module
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom)
            and node.module is not None
        } | {
            alias.name
            for node in ast.walk(tree)
            if isinstance(node, ast.Import)
            for alias in node.names
        }
        if module_name in imported:
            importers.append(path.relative_to(root).as_posix())
    assert importers == []
    raw_module = "investment_orchestrator.mmi.raw_response_envelope"
    raw_importers = tuple(
        path.relative_to(root).as_posix()
        for path in production_paths
        if raw_module
        in {
            node.module
            for node in ast.walk(
                ast.parse(path.read_text(encoding="utf-8"))
            )
            if isinstance(node, ast.ImportFrom)
            and node.module is not None
        }
    )
    assert raw_importers == (
        "src/investment_orchestrator/mmi/"
        "validated_grounded_analysis_response.py",
    )
    assert (
        root / "src/investment_orchestrator/mmi/__init__.py"
    ).read_text(encoding="utf-8") == (
        '"""Manual-model-interface report-only deterministic '
        'projection contracts."""\n\n__all__ = ()\n'
    )
    assert not any(
        name.startswith(("build_mmi_validated", "validate_mmi_validated"))
        for name in contracts.__dict__
    )
