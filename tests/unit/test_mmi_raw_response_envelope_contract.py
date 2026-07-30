from __future__ import annotations

import ast
import base64
from collections.abc import Iterator, Mapping
from copy import deepcopy
import hashlib
import json
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
    MAXIMUM_CANONICAL_JSON_BYTES,
    MAXIMUM_MMI_RAW_RESPONSE_BYTES,
    _MMI_ANALYST_VISIBLE_EVIDENCE_VIEW_IDENTITY_DOMAIN,
    _MMI_GROUNDED_PROMPT_ARTIFACT_IDENTITY_DOMAIN,
    _MMI_GROUNDED_PROMPT_CONTEXT_BINDING_DOMAIN,
    _MMI_RAW_RESPONSE_ENVELOPE_IDENTITY_DOMAIN,
    MMI_AUTHENTICATED_EVIDENCE_BUNDLE_IDENTITY_DOMAIN,
    MMI_POLICY_PROJECTION_IDENTITY_DOMAIN,
    MMI_PORTFOLIO_SNAPSHOT_PROJECTION_IDENTITY_DOMAIN,
    MMI_SOURCE_RECORD_IDENTITY_DOMAIN,
    MMI_UNIVERSE_PROJECTION_IDENTITY_DOMAIN,
    MmiCanonicalizationError,
)
from investment_orchestrator.mmi.contracts import (
    MMI_RAW_RESPONSE_ENVELOPE_ARTIFACT_KIND,
    MMI_RAW_RESPONSE_ENVELOPE_SCHEMA_VERSION,
    mmi_raw_response_envelope_identity_sha256,
)


SCHEMA_NAME = "mmi_raw_response_envelope_v1.schema.json"
SCHEMA_PATH = repo_root() / "schemas" / SCHEMA_NAME
IDENTITY_DOMAIN = b"mmi_raw_response_envelope_v1\0"
IDENTITY_FIELD = "raw_response_envelope_identity_sha256"
PROMPT_IDENTITY_FIELD = "grounded_prompt_artifact_identity_sha256"
RAW_DIGEST_FIELD = "raw_response_sha256"
RAW_LENGTH_FIELD = "raw_response_byte_length"
RAW_BASE64_FIELD = "raw_response_base64"
MAXIMUM_RAW_BYTES = 262_144
MAXIMUM_BASE64_CHARACTERS = 349_528
MAXIMUM_CANONICAL_ENVELOPE_BYTES = 350_062
MAXIMUM_IDENTITY_PREIMAGE_BYTES = 349_955
MAXIMUM_FRAMED_PREIMAGE_BYTES = 349_992
EXPECTED_FIELDS = frozenset(
    {
        "schema_version",
        "artifact_kind",
        "report_only",
        "authority_effect",
        "manual_handoff_required",
        PROMPT_IDENTITY_FIELD,
        RAW_LENGTH_FIELD,
        RAW_DIGEST_FIELD,
        RAW_BASE64_FIELD,
        IDENTITY_FIELD,
    }
)
EXPECTED_PREIMAGE_FIELDS = EXPECTED_FIELDS - {IDENTITY_FIELD}


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


def _artifact(
    raw_bytes: bytes = b'{"analysis":"manual"}\n',
    *,
    prompt_identity: str = "1" * 64,
) -> dict[str, object]:
    value: dict[str, object] = {
        "schema_version": "mmi_raw_response_envelope_v1",
        "artifact_kind": "MMI_RAW_RESPONSE_ENVELOPE",
        "report_only": True,
        "authority_effect": "NONE",
        "manual_handoff_required": True,
        PROMPT_IDENTITY_FIELD: prompt_identity,
        RAW_LENGTH_FIELD: len(raw_bytes),
        RAW_DIGEST_FIELD: hashlib.sha256(raw_bytes).hexdigest(),
        RAW_BASE64_FIELD: base64.b64encode(raw_bytes).decode("ascii"),
        IDENTITY_FIELD: "0" * 64,
    }
    value[IDENTITY_FIELD] = _independent_identity(value)
    return value


def _reseal(value: dict[str, object]) -> dict[str, object]:
    candidate = deepcopy(value)
    candidate[IDENTITY_FIELD] = _independent_identity(candidate)
    assert candidate[IDENTITY_FIELD] == _independent_identity(candidate)
    return candidate


def _assert_structural_rejection(value: object) -> None:
    with pytest.raises(MmiCanonicalizationError):
        mmi_raw_response_envelope_identity_sha256(  # type: ignore[arg-type]
            value
        )


def _schema() -> dict[str, object]:
    value = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    assert type(value) is dict
    return value


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


def test_schema_is_closed_draft_2020_12_with_exact_ten_fields() -> None:
    schema = _schema()
    assert schema["$schema"] == (
        "https://json-schema.org/draft/2020-12/schema"
    )
    assert schema["additionalProperties"] is False
    assert set(schema["required"]) == EXPECTED_FIELDS
    assert set(schema["properties"]) == EXPECTED_FIELDS
    assert schema["properties"][RAW_LENGTH_FIELD] == {
        "type": "integer",
        "minimum": 1,
        "maximum": 262_144,
    }
    assert schema["properties"][RAW_BASE64_FIELD] == {
        "type": "string",
        "minLength": 4,
        "maxLength": 349_528,
        "pattern": (
            "^(?:[A-Za-z0-9+/]{4})*"
            "(?:[A-Za-z0-9+/]{2}==|[A-Za-z0-9+/]{3}=)?$"
        ),
    }
    assert schema["$defs"]["sha256"] == {
        "type": "string",
        "pattern": "^[0-9a-f]{64}$",
    }
    Draft202012Validator.check_schema(schema)


def test_fixed_schema_and_artifact_constants_are_exact() -> None:
    schema = _schema()
    properties = schema["properties"]
    assert MMI_RAW_RESPONSE_ENVELOPE_SCHEMA_VERSION == (
        "mmi_raw_response_envelope_v1"
    )
    assert MMI_RAW_RESPONSE_ENVELOPE_ARTIFACT_KIND == (
        "MMI_RAW_RESPONSE_ENVELOPE"
    )
    assert properties["schema_version"]["const"] == (
        MMI_RAW_RESPONSE_ENVELOPE_SCHEMA_VERSION
    )
    assert properties["artifact_kind"]["const"] == (
        MMI_RAW_RESPONSE_ENVELOPE_ARTIFACT_KIND
    )
    assert properties["report_only"]["const"] is True
    assert properties["authority_effect"]["const"] == "NONE"
    assert properties["manual_handoff_required"]["const"] is True


def test_schema_validator_is_an_independent_oracle() -> None:
    value = _artifact()
    validator = Draft202012Validator(_schema())
    assert tuple(validator.iter_errors(value)) == ()
    validate_artifact_schema(value, schema_name=SCHEMA_NAME)
    assert (
        mmi_raw_response_envelope_identity_sha256(value)
        == value[IDENTITY_FIELD]
    )


@pytest.mark.parametrize(
    "raw_bytes",
    (
        b"one exact response\n",
        b"\xff\xfe\x80not-utf8",
        b"\xef\xbb\xbf\x00\r\n\t\x01\x1f\x7f",
        bytes(range(256)),
    ),
    ids=("ascii", "non-utf8", "bom-nul-controls", "all-byte-values"),
)
def test_arbitrary_nonempty_bytes_round_trip_exactly(
    raw_bytes: bytes,
) -> None:
    value = _artifact(raw_bytes)
    encoded = value[RAW_BASE64_FIELD]
    assert type(encoded) is str
    decoded = base64.b64decode(
        encoded.encode("ascii"),
        validate=True,
    )
    assert decoded == raw_bytes
    assert base64.b64encode(decoded).decode("ascii") == encoded
    assert value[RAW_LENGTH_FIELD] == len(raw_bytes)
    assert value[RAW_DIGEST_FIELD] == hashlib.sha256(
        raw_bytes
    ).hexdigest()
    validate_artifact_schema(value, schema_name=SCHEMA_NAME)
    assert (
        mmi_raw_response_envelope_identity_sha256(value)
        == value[IDENTITY_FIELD]
    )


@pytest.mark.parametrize("kind", ("missing", "extra"))
def test_representative_missing_and_extra_fields_fail_closed(
    kind: str,
) -> None:
    value = _artifact()
    if kind == "missing":
        value.pop(RAW_BASE64_FIELD)
    else:
        value["metadata"] = {}
    with pytest.raises(ArtifactSchemaError):
        validate_artifact_schema(value, schema_name=SCHEMA_NAME)
    _assert_structural_rejection(value)


@pytest.mark.parametrize(
    ("field", "replacement"),
    (
        ("schema_version", "mmi_raw_response_envelope_v2"),
        ("artifact_kind", "RAW_RESPONSE"),
        ("report_only", False),
        ("authority_effect", "TRADE"),
        ("manual_handoff_required", False),
    ),
)
def test_wrong_fixed_constants_fail_even_when_independently_resealed(
    field: str,
    replacement: object,
) -> None:
    candidate = _artifact()
    candidate[field] = replacement
    candidate = _reseal(candidate)
    assert candidate[IDENTITY_FIELD] == _independent_identity(candidate)
    with pytest.raises(ArtifactSchemaError):
        validate_artifact_schema(candidate, schema_name=SCHEMA_NAME)
    _assert_structural_rejection(candidate)


@pytest.mark.parametrize(
    ("field", "replacement"),
    (
        (PROMPT_IDENTITY_FIELD, "A" * 64),
        (PROMPT_IDENTITY_FIELD, "a" * 63),
        (RAW_DIGEST_FIELD, "g" * 64),
        (RAW_DIGEST_FIELD, "0" * 65),
        (IDENTITY_FIELD, "F" * 64),
        (IDENTITY_FIELD, "f" * 63),
    ),
)
def test_hash_fields_require_exact_lowercase_sha256(
    field: str,
    replacement: str,
) -> None:
    candidate = _artifact()
    candidate[field] = replacement
    with pytest.raises(ArtifactSchemaError):
        validate_artifact_schema(candidate, schema_name=SCHEMA_NAME)
    _assert_structural_rejection(candidate)


@pytest.mark.parametrize("replacement", (0, 262_145, True))
def test_raw_length_requires_exact_bounded_integer(
    replacement: object,
) -> None:
    candidate = _artifact()
    candidate[RAW_LENGTH_FIELD] = replacement
    candidate = _reseal(candidate)
    with pytest.raises(ArtifactSchemaError):
        validate_artifact_schema(candidate, schema_name=SCHEMA_NAME)
    _assert_structural_rejection(candidate)


def test_public_contract_surface_is_exact_and_not_reexported() -> None:
    assert {
        name
        for name in canonical.__dict__
        if "RAW_RESPONSE" in name and not name.startswith("_")
    } == {"MAXIMUM_MMI_RAW_RESPONSE_BYTES"}
    assert {
        name
        for name in contracts.__dict__
        if "RAW_RESPONSE" in name and not name.startswith("_")
    } == {
        "MAXIMUM_MMI_RAW_RESPONSE_BYTES",
        "MMI_RAW_RESPONSE_ENVELOPE_SCHEMA_VERSION",
        "MMI_RAW_RESPONSE_ENVELOPE_ARTIFACT_KIND",
    }
    assert {
        name
        for name in contracts.__dict__
        if name.startswith("mmi_raw_response")
    } == {"mmi_raw_response_envelope_identity_sha256"}
    import investment_orchestrator.mmi as mmi

    assert mmi.__all__ == ()
    assert not hasattr(mmi, "mmi_raw_response_envelope_identity_sha256")


def test_production_and_independent_identity_oracles_are_exact() -> None:
    value = _artifact()
    preimage = deepcopy(value)
    preimage.pop(IDENTITY_FIELD)
    assert set(preimage) == EXPECTED_PREIMAGE_FIELDS
    independent = _independent_identity(value)
    assert independent == value[IDENTITY_FIELD]
    assert (
        mmi_raw_response_envelope_identity_sha256(value)
        == independent
    )
    canonical_preimage = _canonical(preimage)
    material = (
        IDENTITY_DOMAIN
        + struct.pack(">Q", len(canonical_preimage))
        + canonical_preimage
    )
    assert hashlib.sha256(material).hexdigest() == independent


def test_complete_ten_field_mapping_is_the_only_helper_contract() -> None:
    value = _artifact()
    preimage = deepcopy(value)
    preimage.pop(IDENTITY_FIELD)
    _assert_structural_rejection(preimage)
    assert (
        mmi_raw_response_envelope_identity_sha256(
            MappingProxyType(value)
        )
        == value[IDENTITY_FIELD]
    )


def test_mapping_insertion_order_does_not_change_identity() -> None:
    value = _artifact()
    reversed_value = {
        key: deepcopy(value[key]) for key in reversed(tuple(value))
    }
    assert tuple(reversed_value) != tuple(value)
    assert _independent_identity(reversed_value) == value[IDENTITY_FIELD]
    assert (
        mmi_raw_response_envelope_identity_sha256(reversed_value)
        == value[IDENTITY_FIELD]
    )


def test_valid_prompt_identity_change_is_structural_not_authentication() -> None:
    original = _artifact()
    changed = _artifact(prompt_identity="2" * 64)
    assert changed[PROMPT_IDENTITY_FIELD] != (
        original[PROMPT_IDENTITY_FIELD]
    )
    assert changed[IDENTITY_FIELD] != original[IDENTITY_FIELD]
    assert (
        mmi_raw_response_envelope_identity_sha256(changed)
        == changed[IDENTITY_FIELD]
    )


@pytest.mark.parametrize(
    "encoded",
    (
        "!!!!",
        "AAA",
        "AA=A",
        "AAAA\n",
        "====",
    ),
    ids=(
        "alphabet",
        "wrong-length",
        "interior-padding",
        "whitespace",
        "padding-only",
    ),
)
def test_representative_invalid_base64_fails_closed(
    encoded: str,
) -> None:
    candidate = _artifact()
    candidate[RAW_BASE64_FIELD] = encoded
    candidate = _reseal(candidate)
    _assert_structural_rejection(candidate)


def test_schema_valid_but_noncanonical_base64_is_rejected() -> None:
    candidate = _artifact(b"\x00")
    candidate[RAW_BASE64_FIELD] = "AB=="
    assert base64.b64decode("AB==", validate=True) == b"\x00"
    assert base64.b64encode(b"\x00").decode("ascii") == "AA=="
    candidate = _reseal(candidate)
    validate_artifact_schema(candidate, schema_name=SCHEMA_NAME)
    assert candidate[IDENTITY_FIELD] == _independent_identity(candidate)
    _assert_structural_rejection(candidate)


@pytest.mark.parametrize(
    "kind",
    (
        "length",
        "digest",
        "prompt-hash",
        "authority",
        "manual-handoff",
        "base64-canonicality",
    ),
)
def test_independently_resealed_contradictions_reach_owning_layer(
    kind: str,
) -> None:
    candidate = _artifact(b"\x00")
    if kind == "length":
        candidate[RAW_LENGTH_FIELD] = 2
    elif kind == "digest":
        candidate[RAW_DIGEST_FIELD] = "f" * 64
    elif kind == "prompt-hash":
        candidate[PROMPT_IDENTITY_FIELD] = "F" * 64
    elif kind == "authority":
        candidate["authority_effect"] = "TRADE"
    elif kind == "manual-handoff":
        candidate["manual_handoff_required"] = False
    elif kind == "base64-canonicality":
        candidate[RAW_BASE64_FIELD] = "AB=="
    else:
        raise AssertionError(kind)
    candidate = _reseal(candidate)
    assert candidate[IDENTITY_FIELD] == _independent_identity(candidate)
    _assert_structural_rejection(candidate)


def test_top_level_identity_only_mutation_is_rejected() -> None:
    value = _artifact()
    candidate = deepcopy(value)
    candidate[IDENTITY_FIELD] = "f" * 64
    assert _independent_identity(candidate) == value[IDENTITY_FIELD]
    _assert_structural_rejection(candidate)


def test_maximum_raw_response_and_exact_size_arithmetic() -> None:
    raw_bytes = b"\xff" * MAXIMUM_RAW_BYTES
    value = _artifact(raw_bytes)
    encoded = value[RAW_BASE64_FIELD]
    assert type(encoded) is str
    assert MAXIMUM_MMI_RAW_RESPONSE_BYTES == MAXIMUM_RAW_BYTES
    assert len(encoded) == MAXIMUM_BASE64_CHARACTERS
    assert len(encoded) == 4 * ((len(raw_bytes) + 2) // 3)
    preimage = deepcopy(value)
    preimage.pop(IDENTITY_FIELD)
    canonical_preimage = _canonical(preimage)
    framed = (
        IDENTITY_DOMAIN
        + struct.pack(">Q", len(canonical_preimage))
        + canonical_preimage
    )
    assert len(_canonical(value)) == MAXIMUM_CANONICAL_ENVELOPE_BYTES
    assert len(canonical_preimage) == MAXIMUM_IDENTITY_PREIMAGE_BYTES
    assert len(framed) == MAXIMUM_FRAMED_PREIMAGE_BYTES
    assert len(_canonical(value)) < MAXIMUM_CANONICAL_JSON_BYTES
    validate_artifact_schema(value, schema_name=SCHEMA_NAME)
    assert (
        mmi_raw_response_envelope_identity_sha256(value)
        == value[IDENTITY_FIELD]
    )


def test_one_decoded_byte_over_maximum_fails_when_independently_resealed() -> None:
    raw_bytes = b"\xff" * (MAXIMUM_RAW_BYTES + 1)
    candidate = _artifact(raw_bytes)
    assert len(
        candidate[RAW_BASE64_FIELD]  # type: ignore[arg-type]
    ) == MAXIMUM_BASE64_CHARACTERS
    assert candidate[RAW_LENGTH_FIELD] == MAXIMUM_RAW_BYTES + 1
    assert candidate[IDENTITY_FIELD] == _independent_identity(candidate)
    with pytest.raises(ArtifactSchemaError):
        validate_artifact_schema(candidate, schema_name=SCHEMA_NAME)
    _assert_structural_rejection(candidate)


def test_first_eight_domains_are_unchanged_and_ninth_is_unique() -> None:
    first_eight = (
        MMI_SOURCE_RECORD_IDENTITY_DOMAIN,
        MMI_UNIVERSE_PROJECTION_IDENTITY_DOMAIN,
        MMI_POLICY_PROJECTION_IDENTITY_DOMAIN,
        MMI_PORTFOLIO_SNAPSHOT_PROJECTION_IDENTITY_DOMAIN,
        MMI_AUTHENTICATED_EVIDENCE_BUNDLE_IDENTITY_DOMAIN,
        _MMI_ANALYST_VISIBLE_EVIDENCE_VIEW_IDENTITY_DOMAIN,
        _MMI_GROUNDED_PROMPT_CONTEXT_BINDING_DOMAIN,
        _MMI_GROUNDED_PROMPT_ARTIFACT_IDENTITY_DOMAIN,
    )
    assert first_eight == (
        b"mmi_source_record_v1\0",
        b"mmi_universe_projection_v1\0",
        b"mmi_policy_projection_v1\0",
        b"mmi_portfolio_snapshot_projection_v1\0",
        b"mmi_authenticated_evidence_bundle_v1\0",
        b"mmi_analyst_visible_evidence_view_v1\0",
        b"mmi_grounded_prompt_context_binding_v1\0",
        b"mmi_grounded_prompt_artifact_v1\0",
    )
    assert _MMI_RAW_RESPONSE_ENVELOPE_IDENTITY_DOMAIN == IDENTITY_DOMAIN
    all_domains = (*first_eight, IDENTITY_DOMAIN)
    assert len(all_domains) == len(set(all_domains)) == 9


@pytest.mark.parametrize(
    "value",
    (
        [],
        {"not": "an envelope"},
    ),
    ids=("non-mapping", "wrong-mapping"),
)
def test_unsupported_input_forms_fail_closed(value: object) -> None:
    _assert_structural_rejection(value)


def test_duplicate_mapping_keys_fail_closed() -> None:
    value = _DuplicateKeyMapping(_artifact())
    assert tuple(value).count("schema_version") == 2
    _assert_structural_rejection(value)


def test_diagnostics_do_not_include_response_or_rejected_values() -> None:
    raw_bytes = b"PRIVATE RESPONSE BUY SELL\x00\xff"
    candidate = _artifact(raw_bytes)
    candidate[RAW_DIGEST_FIELD] = "f" * 64
    candidate = _reseal(candidate)
    with pytest.raises(MmiCanonicalizationError) as caught:
        mmi_raw_response_envelope_identity_sha256(candidate)
    diagnostic = str(caught.value)
    assert raw_bytes.hex() not in diagnostic
    assert candidate[RAW_BASE64_FIELD] not in diagnostic
    assert candidate[PROMPT_IDENTITY_FIELD] not in diagnostic
    assert "PRIVATE" not in diagnostic


def test_raw_response_data_is_inert_and_creates_no_authority() -> None:
    raw_bytes = (
        b'{"decision":"BUY","permission":true,'
        b'"order":"ORDER_COMPILATION","quantity":999}'
    )
    value = _artifact(raw_bytes)
    assert value["report_only"] is True
    assert value["authority_effect"] == "NONE"
    assert value["manual_handoff_required"] is True
    assert (
        mmi_raw_response_envelope_identity_sha256(value)
        == value[IDENTITY_FIELD]
    )
    assert set(value) == EXPECTED_FIELDS


def test_r1b_contract_and_r1c_runtime_have_exact_phase_ownership() -> None:
    root = repo_root()
    production_paths = tuple(
        sorted((root / "src/investment_orchestrator").rglob("*.py"))
    )
    assert len(production_paths) == 133
    relative = {
        path: path.relative_to(root).as_posix()
        for path in production_paths
    }
    runtime_relative = (
        "src/investment_orchestrator/mmi/raw_response_envelope.py"
    )
    runtime_path = root / runtime_relative
    assert tuple(
        relative[path]
        for path in production_paths
        if path.name == "raw_response_envelope.py"
    ) == (runtime_relative,)
    assert runtime_path.is_file()

    def imported_modules(tree: ast.AST) -> set[str]:
        return {
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

    trees = {
        path: ast.parse(path.read_text(encoding="utf-8"))
        for path in production_paths
    }
    grounded_prompt_module = (
        "investment_orchestrator.mmi.grounded_prompt"
    )
    raw_response_module = (
        "investment_orchestrator.mmi.raw_response_envelope"
    )
    assert tuple(
        relative[path]
        for path, tree in trees.items()
        if grounded_prompt_module in imported_modules(tree)
    ) == (runtime_relative,)
    assert tuple(
        relative[path]
        for path, tree in trees.items()
        if raw_response_module in imported_modules(tree)
    ) == ()

    contracts_path = (
        root / "src/investment_orchestrator/mmi/contracts.py"
    )
    helper_name = "mmi_raw_response_envelope_identity_sha256"
    consumers: list[str] = []
    for path in production_paths:
        if path == contracts_path:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        if any(
            (
                isinstance(node, ast.Name)
                and isinstance(node.ctx, ast.Load)
                and node.id == helper_name
            )
            or (
                isinstance(node, ast.Attribute)
                and isinstance(node.ctx, ast.Load)
                and node.attr == helper_name
            )
            for node in ast.walk(tree)
        ):
            consumers.append(path.relative_to(root).as_posix())
    assert consumers == [runtime_relative]


def test_no_parser_writer_transport_or_action_surface_is_added() -> None:
    root = repo_root()
    contracts_source = (
        root / "src/investment_orchestrator/mmi/contracts.py"
    ).read_text(encoding="utf-8")
    contracts_tree = ast.parse(contracts_source)
    imported = {
        node.module
        for node in ast.walk(contracts_tree)
        if isinstance(node, ast.ImportFrom) and node.module is not None
    } | {
        alias.name
        for node in ast.walk(contracts_tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    }
    assert "base64" in imported
    prohibited = {
        "openai",
        "anthropic",
        "requests",
        "httpx",
        "urllib",
        "socket",
        "subprocess",
        "tempfile",
        "investment_orchestrator.workflow",
        "investment_orchestrator.state",
        "investment_orchestrator.permissions",
        "investment_orchestrator.orders",
        "investment_orchestrator.broker",
    }
    assert not any(
        imported_name == prefix
        or imported_name.startswith(f"{prefix}.")
        for imported_name in imported
        for prefix in prohibited
    )
    runtime_path = (
        root
        / "src/investment_orchestrator/mmi/"
        "raw_response_envelope.py"
    )
    assert tuple(
        path.name
        for path in sorted(
            (root / "src/investment_orchestrator/mmi").glob("*.py")
        )
        if path.name == "raw_response_envelope.py"
    ) == ("raw_response_envelope.py",)
    runtime_tree = ast.parse(runtime_path.read_text(encoding="utf-8"))
    assert tuple(
        node.name
        for node in runtime_tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and not node.name.startswith("_")
    ) == (
        "build_mmi_raw_response_envelope",
        "validate_mmi_raw_response_envelope",
    )
    runtime_imports = {
        node.module
        for node in ast.walk(runtime_tree)
        if isinstance(node, ast.ImportFrom) and node.module is not None
    } | {
        alias.name
        for node in ast.walk(runtime_tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    }
    assert {"base64", "hashlib"} <= runtime_imports
    assert not {
        "json",
        "codecs",
        "os",
        "pathlib",
        "pickle",
        "subprocess",
        "socket",
        "requests",
        "httpx",
        "openai",
        "anthropic",
        "investment_orchestrator.cli",
        "investment_orchestrator.workflow",
        "investment_orchestrator.state",
        "investment_orchestrator.permissions",
        "investment_orchestrator.orders",
        "investment_orchestrator.broker",
    } & runtime_imports
    calls = {
        node.func.attr
        for node in ast.walk(runtime_tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
    }
    assert "b64decode" not in calls
    assert not {
        "open",
        "read_bytes",
        "read_text",
        "write_bytes",
        "write_text",
    } & calls
    assert (
        root / "src/investment_orchestrator/mmi/__init__.py"
    ).read_text(encoding="utf-8") == (
        '"""Manual-model-interface report-only deterministic '
        'projection contracts."""\n\n__all__ = ()\n'
    )
