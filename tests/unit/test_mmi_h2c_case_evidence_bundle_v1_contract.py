from __future__ import annotations

import ast
from copy import deepcopy
import hashlib
import inspect
import json
from pathlib import Path

import pytest

import investment_orchestrator as package
import investment_orchestrator.mmi as mmi
from investment_orchestrator.common.paths import repo_root
from investment_orchestrator.mmi import canonical
from investment_orchestrator.offline import (
    mmi_h2c_case_bundle_v1 as owner,
)


SCHEMA_NAME = "mmi_h2c_case_evidence_bundle_v1.schema.json"
IDENTITY_FIELD = "case_evidence_bundle_identity_sha256"
EXPECTED_FIELDS = {
    "schema_version",
    "artifact_kind",
    "report_only",
    "authority_effect",
    "grounded_prompt",
    "raw_response_envelope",
    "validated_grounded_analysis_response",
    "legacy_step1_compatibility_candidate",
    "strategy_settings_source_record",
    "portfolio_snapshot_source_record",
    IDENTITY_FIELD,
}
SLOT_DISCRIMINATORS = {
    "grounded_prompt": "mmi_grounded_prompt_v2",
    "raw_response_envelope": "mmi_raw_response_envelope_v2",
    "validated_grounded_analysis_response": (
        "mmi_validated_grounded_analysis_response_v2"
    ),
    "legacy_step1_compatibility_candidate": (
        "mmi_legacy_step1_compatibility_candidate_v1"
    ),
    "strategy_settings_source_record": "mmi_source_record_v1",
    "portfolio_snapshot_source_record": "mmi_source_record_v1",
}


def _schema() -> dict[str, object]:
    value = json.loads(
        (repo_root() / "schemas" / SCHEMA_NAME).read_text(encoding="utf-8")
    )
    assert type(value) is dict
    return value


def _owner_source() -> str:
    return Path(owner.__file__).read_text(encoding="utf-8")


def _independent_canonical_json_bytes(value: object) -> bytes:
    """Reimplements the canonical encoding without ``canonical_json_bytes``."""
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def _independent_bundle_identity_sha256(bundle: dict[str, object]) -> str:
    """Independent oracle: reimplements domain-separated framing and hashing.

    Deliberately calls neither ``record_identity_sha256``,
    ``domain_separated_sha256``, ``canonical_json_bytes``, nor any bundle-owner
    helper.
    """
    preimage = deepcopy(bundle)
    preimage.pop(IDENTITY_FIELD, None)
    preimage_bytes = _independent_canonical_json_bytes(preimage)
    length_frame = len(preimage_bytes).to_bytes(
        8, byteorder="big", signed=False
    )
    framed = (
        b"mmi_h2c_case_evidence_bundle_v1\x00"
        + length_frame
        + preimage_bytes
    )
    return hashlib.sha256(framed).hexdigest()


def _member(schema_version: str, **extra: object) -> dict[str, object]:
    return {"schema_version": schema_version, **extra}


def _inputs() -> dict[str, dict[str, object]]:
    return {
        "grounded_prompt": _member(
            "mmi_grounded_prompt_v2",
            prompt_text="P",
            nested={"depth": ["a", "b"]},
        ),
        "raw_response_envelope": _member(
            "mmi_raw_response_envelope_v2",
            raw_response_byte_length=2,
        ),
        "validated_grounded_analysis_response": _member(
            "mmi_validated_grounded_analysis_response_v2",
            response_payload={"instrument_views": []},
        ),
        "legacy_step1_compatibility_candidate": _member(
            "mmi_legacy_step1_compatibility_candidate_v1",
            provenance={"x": "y"},
        ),
        "strategy_settings_source_record": _member(
            "mmi_source_record_v1",
            source_role="STRATEGY_SETTINGS",
        ),
        "portfolio_snapshot_source_record": _member(
            "mmi_source_record_v1",
            source_role="PORTFOLIO_SNAPSHOT",
        ),
    }


@pytest.fixture()
def bundle() -> dict[str, object]:
    return owner.build_mmi_h2c_case_evidence_bundle_v1(**_inputs())


def test_schema_is_closed_exact_and_constants_are_report_only() -> None:
    from jsonschema import Draft202012Validator

    schema = _schema()
    Draft202012Validator.check_schema(schema)
    assert schema["type"] == "object"
    assert schema["additionalProperties"] is False
    assert set(schema["required"]) == EXPECTED_FIELDS
    properties = schema["properties"]
    assert type(properties) is dict
    assert set(properties) == set(schema["required"]) == EXPECTED_FIELDS
    assert properties["schema_version"] == {
        "const": "mmi_h2c_case_evidence_bundle_v1"
    }
    assert properties["artifact_kind"] == {
        "const": "MMI_H2C_CASE_EVIDENCE_BUNDLE"
    }
    assert properties["report_only"] == {"const": True}
    assert properties["authority_effect"] == {"const": "NONE"}
    assert "report_only" in schema["required"]
    assert "authority_effect" in schema["required"]
    assert schema["$defs"]["sha256"] == {
        "type": "string",
        "pattern": "^[0-9a-f]{64}$",
    }
    prohibited = {
        "provider_origin_authentication",
        "provider_authorship",
        "submission_proof",
        "causality_proof",
        "availability",
        "permission",
        "gate_result",
        "publication_eligibility",
        "order_readiness",
        "execution_authority",
        "replacement_readiness",
        "operator_notes",
        "quality_score",
    }
    assert not EXPECTED_FIELDS.intersection(prohibited)


def test_schema_uses_same_document_refs_only() -> None:
    found: list[str] = []

    def walk(node: object) -> None:
        if type(node) is dict:
            for key, value in node.items():
                if key == "$ref":
                    assert type(value) is str
                    found.append(value)
                else:
                    walk(value)
        elif type(node) is list:
            for item in node:
                walk(item)

    walk(_schema())
    assert found
    assert all(ref.startswith("#/") for ref in found), found


def test_slot_defs_do_not_close_nested_artifacts() -> None:
    defs = _schema()["$defs"]
    assert type(defs) is dict
    slots = {name: body for name, body in defs.items() if name != "sha256"}
    assert len(slots) == 6
    for name, body in slots.items():
        assert type(body) is dict
        assert body["type"] == "object"
        assert "additionalProperties" not in body, name
        assert "schema_version" in body["required"], name


def test_source_record_slots_pin_their_roles() -> None:
    defs = _schema()["$defs"]
    settings = defs["slot_source_record_strategy_settings"]
    portfolio = defs["slot_source_record_portfolio_snapshot"]
    assert settings["properties"]["source_role"] == {
        "const": "STRATEGY_SETTINGS"
    }
    assert portfolio["properties"]["source_role"] == {
        "const": "PORTFOLIO_SNAPSHOT"
    }
    assert set(settings["required"]) == {"schema_version", "source_role"}
    assert set(portfolio["required"]) == {"schema_version", "source_role"}


def test_valid_bundle_validates_and_has_exact_shape(
    bundle: dict[str, object],
) -> None:
    assert owner.validate_mmi_h2c_case_evidence_bundle_v1(
        bundle=bundle
    ) is None
    assert set(bundle) == EXPECTED_FIELDS
    assert bundle["report_only"] is True
    assert bundle["authority_effect"] == "NONE"
    assert bundle["schema_version"] == "mmi_h2c_case_evidence_bundle_v1"
    assert bundle["artifact_kind"] == "MMI_H2C_CASE_EVIDENCE_BUNDLE"


@pytest.mark.parametrize("slot", sorted(SLOT_DISCRIMINATORS))
def test_wrong_slot_schema_version_is_rejected(
    bundle: dict[str, object],
    slot: str,
) -> None:
    mutated = deepcopy(bundle)
    member = mutated[slot]
    assert type(member) is dict
    member["schema_version"] = "mmi_wrong_artifact_v9"
    mutated[IDENTITY_FIELD] = _independent_bundle_identity_sha256(mutated)
    with pytest.raises(
        owner.MmiH2cCaseEvidenceBundleV1Error,
        match="^MMI_H2C_CASE_EVIDENCE_BUNDLE_V1_INVALID$",
    ):
        owner.validate_mmi_h2c_case_evidence_bundle_v1(bundle=mutated)


def test_source_records_cannot_be_swapped(
    bundle: dict[str, object],
) -> None:
    swapped = deepcopy(bundle)
    settings = swapped["strategy_settings_source_record"]
    portfolio = swapped["portfolio_snapshot_source_record"]
    swapped["strategy_settings_source_record"] = portfolio
    swapped["portfolio_snapshot_source_record"] = settings
    swapped[IDENTITY_FIELD] = _independent_bundle_identity_sha256(swapped)
    with pytest.raises(owner.MmiH2cCaseEvidenceBundleV1Error):
        owner.validate_mmi_h2c_case_evidence_bundle_v1(bundle=swapped)


def test_unknown_top_level_field_is_rejected_even_with_matching_identity(
    bundle: dict[str, object],
) -> None:
    mutated = deepcopy(bundle)
    mutated["context_proven"] = True
    mutated[IDENTITY_FIELD] = _independent_bundle_identity_sha256(mutated)
    with pytest.raises(owner.MmiH2cCaseEvidenceBundleV1Error):
        owner.validate_mmi_h2c_case_evidence_bundle_v1(bundle=mutated)


@pytest.mark.parametrize(
    ("field", "replacement"),
    (
        ("report_only", False),
        ("authority_effect", "ADVISORY"),
        ("schema_version", "mmi_h2c_case_evidence_bundle_v2"),
        ("artifact_kind", "MMI_SOMETHING_ELSE"),
    ),
)
def test_constants_cannot_be_weakened(
    bundle: dict[str, object],
    field: str,
    replacement: object,
) -> None:
    mutated = deepcopy(bundle)
    mutated[field] = replacement
    mutated[IDENTITY_FIELD] = _independent_bundle_identity_sha256(mutated)
    with pytest.raises(owner.MmiH2cCaseEvidenceBundleV1Error):
        owner.validate_mmi_h2c_case_evidence_bundle_v1(bundle=mutated)


def test_correct_discriminator_with_incomplete_body_passes_the_envelope(
    bundle: dict[str, object],
) -> None:
    """Responsibility boundary: the envelope owner is not a portable validator.

    The nested bodies in this fixture are deliberately incomplete artifacts.
    They carry the right discriminator, so the envelope accepts them.  Only
    ``validate_..._portable_evidence`` rejects an invalid nested body, and that
    ownership is not duplicated here.
    """
    grounded_prompt = bundle["grounded_prompt"]
    assert type(grounded_prompt) is dict
    assert "grounded_prompt_artifact_identity_sha256" not in grounded_prompt
    assert owner.validate_mmi_h2c_case_evidence_bundle_v1(
        bundle=bundle
    ) is None


def test_builder_is_keyword_only_and_public() -> None:
    assert owner.__all__ == (
        "MmiH2cCaseEvidenceBundleV1Error",
        "build_mmi_h2c_case_evidence_bundle_v1",
        "validate_mmi_h2c_case_evidence_bundle_v1",
    )
    signature = inspect.signature(
        owner.build_mmi_h2c_case_evidence_bundle_v1
    )
    assert tuple(signature.parameters) == (
        "grounded_prompt",
        "raw_response_envelope",
        "validated_grounded_analysis_response",
        "legacy_step1_compatibility_candidate",
        "strategy_settings_source_record",
        "portfolio_snapshot_source_record",
    )
    assert all(
        parameter.kind is inspect.Parameter.KEYWORD_ONLY
        for parameter in signature.parameters.values()
    )
    validator_signature = inspect.signature(
        owner.validate_mmi_h2c_case_evidence_bundle_v1
    )
    assert tuple(validator_signature.parameters) == ("bundle",)
    assert all(
        parameter.kind is inspect.Parameter.KEYWORD_ONLY
        for parameter in validator_signature.parameters.values()
    )


def test_builder_detaches_every_member_and_never_mutates_callers() -> None:
    supplied = _inputs()
    before = deepcopy(supplied)
    built = owner.build_mmi_h2c_case_evidence_bundle_v1(**supplied)
    assert type(built) is dict
    assert supplied == before

    for slot in SLOT_DISCRIMINATORS:
        assert built[slot] is not supplied[slot]
    nested = supplied["grounded_prompt"]["nested"]
    assert built["grounded_prompt"]["nested"] is not nested

    baseline = deepcopy(built)
    supplied["grounded_prompt"]["prompt_text"] = "MUTATED"
    supplied["grounded_prompt"]["nested"]["depth"].append("c")
    supplied["strategy_settings_source_record"]["source_role"] = "WRONG"
    assert built == baseline
    assert owner.validate_mmi_h2c_case_evidence_bundle_v1(
        bundle=built
    ) is None


def test_identity_excludes_only_the_self_field(
    bundle: dict[str, object],
) -> None:
    preimage = deepcopy(bundle)
    preimage.pop(IDENTITY_FIELD)
    assert IDENTITY_FIELD not in preimage
    assert len(preimage) == 10
    assert set(preimage) == set(bundle) - {IDENTITY_FIELD}


def test_independent_oracle_reproduces_the_stored_identity(
    bundle: dict[str, object],
) -> None:
    domain = b"mmi_h2c_case_evidence_bundle_v1\x00"
    assert len(domain) == 32
    assert domain.decode("ascii")
    assert domain.endswith(b"\x00")
    assert b"\x00" not in domain[:-1]

    preimage = deepcopy(bundle)
    preimage.pop(IDENTITY_FIELD)
    preimage_bytes = _independent_canonical_json_bytes(preimage)
    length_frame = len(preimage_bytes).to_bytes(
        8, byteorder="big", signed=False
    )
    assert len(length_frame) == 8
    assert length_frame == len(preimage_bytes).to_bytes(
        8, byteorder="big", signed=False
    )
    framed = domain + length_frame + preimage_bytes
    assert len(framed) == len(domain) + len(length_frame) + len(
        preimage_bytes
    )

    expected_identity = hashlib.sha256(framed).hexdigest()
    assert expected_identity == bundle[IDENTITY_FIELD]
    assert expected_identity == _independent_bundle_identity_sha256(bundle)


def test_independent_oracle_detects_a_mutated_identity_covered_field(
    bundle: dict[str, object],
) -> None:
    mutated = deepcopy(bundle)
    stored_identity = mutated[IDENTITY_FIELD]
    member = mutated["legacy_step1_compatibility_candidate"]
    assert type(member) is dict
    member["provenance"] = {"x": "changed"}
    assert (
        member["provenance"]
        != bundle["legacy_step1_compatibility_candidate"]["provenance"]
    )
    recomputed = _independent_bundle_identity_sha256(mutated)
    assert recomputed != stored_identity
    with pytest.raises(owner.MmiH2cCaseEvidenceBundleV1Error):
        owner.validate_mmi_h2c_case_evidence_bundle_v1(bundle=mutated)


def test_identity_domain_inventory_increases_exactly_once() -> None:
    domains = tuple(
        value
        for value in vars(canonical).values()
        if (
            type(value) is bytes
            and value.startswith(b"mmi_")
            and value.endswith(b"\0")
        )
    )
    assert len(domains) == len(set(domains)) == 19
    assert domains.count(b"mmi_h2c_case_evidence_bundle_v1\0") == 1
    assert (
        canonical._MMI_H2C_CASE_EVIDENCE_BUNDLE_V1_IDENTITY_DOMAIN
        == b"mmi_h2c_case_evidence_bundle_v1\0"
    )
    assert (
        canonical.MAX_MMI_H2C_DUAL_SIDE_MANUAL_HANDOFF_CONTEXT_RECEIPT_V1_CANONICAL_BYTES
        == 1114
    )


def test_ceiling_equals_the_independently_derived_maximum() -> None:
    sha256_hex = "0" * 64

    def size(value: object) -> int:
        return len(_independent_canonical_json_bytes(value))

    framing = (
        size(
            {
                "schema_version": "mmi_h2c_case_evidence_bundle_v1",
                "artifact_kind": "MMI_H2C_CASE_EVIDENCE_BUNDLE",
                "report_only": True,
                "authority_effect": "NONE",
                "grounded_prompt": {},
                "raw_response_envelope": {},
                "validated_grounded_analysis_response": {},
                "legacy_step1_compatibility_candidate": {},
                "strategy_settings_source_record": {},
                "portfolio_snapshot_source_record": {},
                IDENTITY_FIELD: sha256_hex,
            }
        )
        - 6 * 2
    )
    grounded_prompt_fixed = size(
        {
            "schema_version": "mmi_grounded_prompt_v2",
            "artifact_kind": "MMI_GROUNDED_PROMPT",
            "report_only": True,
            "authority_effect": "NONE",
            "analyst_visible_evidence_view_identity_sha256": sha256_hex,
            "instruction_set_version": (
                "mmi_grounded_prompt_instruction_set_v2"
            ),
            "expected_response_schema_version": (
                "mmi_grounded_analysis_response_v2"
            ),
            "manual_handoff_required": True,
            "prompt_context_binding_sha256": sha256_hex,
            "prompt_text": "",
            "grounded_prompt_artifact_identity_sha256": sha256_hex,
        }
    )
    raw_response_fixed = size(
        {
            "schema_version": "mmi_raw_response_envelope_v2",
            "artifact_kind": "MMI_RAW_RESPONSE_ENVELOPE",
            "report_only": True,
            "authority_effect": "NONE",
            "manual_handoff_required": True,
            "grounded_prompt_artifact_identity_sha256": sha256_hex,
            "raw_response_byte_length": 262_144,
            "raw_response_sha256": sha256_hex,
            "raw_response_base64": "",
            "raw_response_envelope_identity_sha256": sha256_hex,
        }
    )
    validated_response_fixed = size(
        {
            "schema_version": "mmi_validated_grounded_analysis_response_v2",
            "artifact_kind": "MMI_VALIDATED_GROUNDED_ANALYSIS_RESPONSE",
            "report_only": True,
            "authority_effect": "NONE",
            "manual_handoff_required": True,
            "raw_response_envelope_identity_sha256": sha256_hex,
            "response_payload": {},
            "validated_grounded_analysis_response_identity_sha256": (
                sha256_hex
            ),
        }
    )

    # Worst-case JSON string expansion under ensure_ascii=False is six bytes
    # per one-byte control character (U+0000 escapes to a six-byte form).
    assert len(json.dumps("\x00", ensure_ascii=False).encode("utf-8")) - 2 == 6
    prompt_text_maximum = canonical.MAXIMUM_GROUNDED_PROMPT_TEXT_BYTES * 6
    base64_maximum = -(-canonical.MAXIMUM_MMI_RAW_RESPONSE_BYTES // 3) * 4
    assert base64_maximum == 349_528

    grounded_prompt_maximum = grounded_prompt_fixed + prompt_text_maximum
    raw_response_maximum = raw_response_fixed + base64_maximum
    validated_response_maximum = (
        validated_response_fixed
        - 2
        + canonical.MAX_MMI_GROUNDED_ANALYSIS_RESPONSE_V2_CANONICAL_BYTES
    )
    candidate_maximum = (
        canonical.MAX_MMI_LEGACY_STEP1_COMPATIBILITY_CANDIDATE_V1_CANONICAL_BYTES
    )
    source_records_maximum = 2 * owner._SOURCE_RECORD_MAXIMUM_CANONICAL_BYTES

    assert framing == 445
    assert grounded_prompt_maximum == 393_852
    assert raw_response_maximum == 350_062
    assert validated_response_maximum == 246_208
    assert candidate_maximum == 262_144
    assert source_records_maximum == 16_384

    derived = (
        framing
        + grounded_prompt_maximum
        + raw_response_maximum
        + validated_response_maximum
        + candidate_maximum
        + source_records_maximum
    )
    assert derived == 1_269_095
    assert (
        canonical.MAX_MMI_H2C_CASE_EVIDENCE_BUNDLE_V1_CANONICAL_BYTES
        == derived
    )
    assert 32 + 8 + derived == 1_269_135


def test_ceiling_is_a_maximum_not_an_exact_size(
    bundle: dict[str, object],
) -> None:
    # Representative sample only -- NOT the ceiling.  A valid bundle is orders
    # of magnitude smaller than the maximum, proving the bound is `<=`.
    representative = len(_independent_canonical_json_bytes(bundle))
    assert 0 < representative < 4_096
    assert (
        representative
        < canonical.MAX_MMI_H2C_CASE_EVIDENCE_BUNDLE_V1_CANONICAL_BYTES
    )
    assert owner.validate_mmi_h2c_case_evidence_bundle_v1(
        bundle=bundle
    ) is None


def test_error_surface_is_deterministic_and_leaks_nothing(
    bundle: dict[str, object],
) -> None:
    error = owner.MmiH2cCaseEvidenceBundleV1Error(
        "MMI_H2C_CASE_EVIDENCE_BUNDLE_V1_INVALID"
    )
    assert str(error) == "MMI_H2C_CASE_EVIDENCE_BUNDLE_V1_INVALID"
    assert error.args == ("MMI_H2C_CASE_EVIDENCE_BUNDLE_V1_INVALID",)
    assert error.code == "MMI_H2C_CASE_EVIDENCE_BUNDLE_V1_INVALID"
    assert isinstance(error, ValueError)
    with pytest.raises(TypeError):
        owner.MmiH2cCaseEvidenceBundleV1Error("SOMETHING_ELSE")

    secret = "SENTINEL-NESTED-CONTENT"
    mutated = deepcopy(bundle)
    mutated["grounded_prompt"] = {
        "schema_version": "mmi_wrong_artifact_v9",
        "prompt_text": secret,
    }
    mutated[IDENTITY_FIELD] = _independent_bundle_identity_sha256(mutated)
    with pytest.raises(owner.MmiH2cCaseEvidenceBundleV1Error) as captured:
        owner.validate_mmi_h2c_case_evidence_bundle_v1(bundle=mutated)
    assert secret not in str(captured.value)
    assert captured.value.args == (
        "MMI_H2C_CASE_EVIDENCE_BUNDLE_V1_INVALID",
    )


@pytest.mark.parametrize(
    "value",
    (None, "text", 7, [], ("a", "b")),
)
def test_non_mapping_bundles_are_rejected(value: object) -> None:
    with pytest.raises(owner.MmiH2cCaseEvidenceBundleV1Error):
        owner.validate_mmi_h2c_case_evidence_bundle_v1(bundle=value)


def test_unsupported_member_values_are_not_silently_coerced() -> None:
    supplied = _inputs()
    supplied["grounded_prompt"] = {
        "schema_version": "mmi_grounded_prompt_v2",
        "ratio": 1.5,
    }
    with pytest.raises(owner.MmiH2cCaseEvidenceBundleV1Error):
        owner.build_mmi_h2c_case_evidence_bundle_v1(**supplied)


def test_owner_has_no_forbidden_capability_imports() -> None:
    source = _owner_source()
    tree = ast.parse(source)
    imported_modules = {
        node.module
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module
    } | {
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    }
    forbidden_modules = {
        "os",
        "pathlib",
        "socket",
        "subprocess",
        "shutil",
        "tempfile",
        "requests",
        "urllib",
        "urllib.request",
        "http",
        "sched",
        "threading",
        "asyncio",
    }
    assert not forbidden_modules.intersection(imported_modules)
    imported_names = {
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
        for alias in node.names
    }
    assert not {
        "begin_mmi_projection_run",
        "capture_current_mmi_source",
        "MmiCapturedSource",
        "MmiProjectionRunContext",
        "prompt_path",
        "repo_root",
        "domain_separated_sha256",
    }.intersection(imported_names)
    for fragment in (
        "inputs/current",
        "open(",
        "Path(",
        "subprocess",
        "socket",
        "requests",
        "except Exception",
        "api_key",
        "token",
    ):
        assert fragment not in source, fragment
    assert "SLOT_DISCRIMINATOR_VALIDATION" in source
    assert "PORTABLE_STRUCTURAL_VALIDATION" not in source


def test_owner_has_exact_session_consumer_and_no_package_export() -> None:
    production_root = repo_root() / "src/investment_orchestrator"
    owner_module = (
        "investment_orchestrator.offline.mmi_h2c_case_bundle_v1"
    )
    consumers = []
    for path in sorted(production_root.rglob("*.py")):
        if path == Path(owner.__file__):
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module == (
                owner_module
            ):
                consumers.append(path)
            elif isinstance(node, ast.Import) and any(
                alias.name == owner_module for alias in node.names
            ):
                consumers.append(path)
    assert consumers == [
        production_root / "offline/mmi_h2c_consume_persisted_case_v1.py",
        production_root / "offline/mmi_h2c_manual_capture_session.py",
    ]
    # canonical.py names the domain and ceiling constants but imports nothing
    # from the owner, so it is not a consumer.
    assert (
        "mmi_h2c_case_evidence_bundle_v1"
        in (production_root / "mmi/canonical.py").read_text(encoding="utf-8")
    )
    assert mmi.__all__ == ()
    assert not hasattr(package, "__all__")
    assert not hasattr(
        __import__(
            "investment_orchestrator.offline",
            fromlist=["offline"],
        ),
        "mmi_h2c_case_evidence_bundle_v1_export",
    )
