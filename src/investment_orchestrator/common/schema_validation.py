"""Artifact schema validation helpers."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator

from investment_orchestrator.common.io import read_json, write_json
from investment_orchestrator.common.paths import schema_path


class ArtifactSchemaError(ValueError):
    """Raised when an artifact does not match its declared schema."""


@lru_cache(maxsize=None)
def load_artifact_schema(schema_name: str) -> dict[str, Any]:
    payload = read_json(schema_path(schema_name))
    if not isinstance(payload, dict):
        raise ArtifactSchemaError(f"Schema {schema_name} must be a JSON object.")
    return payload


def _format_validation_error(error: Any) -> str:
    path = "$"
    if getattr(error, "absolute_path", None):
        path += "".join(f"[{part!r}]" for part in error.absolute_path)
    return f"{path}: {error.message}"


def validate_artifact_schema(payload: Any, *, schema_name: str) -> None:
    schema = load_artifact_schema(schema_name)
    validator = Draft202012Validator(schema)
    errors = sorted(validator.iter_errors(payload), key=lambda err: list(err.absolute_path))
    if errors:
        raise ArtifactSchemaError(
            f"Artifact failed schema validation for {schema_name}: {_format_validation_error(errors[0])}"
        )


def write_validated_json(path: str | Path, data: Any, *, schema_name: str) -> Path:
    validate_artifact_schema(data, schema_name=schema_name)
    return write_json(path, data)
