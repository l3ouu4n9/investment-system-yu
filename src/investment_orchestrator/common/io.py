"""Basic file I/O helpers."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def ensure_dir(path: str | Path) -> Path:
    """Create a directory if it does not already exist."""
    directory = Path(path)
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def read_text(path: str | Path) -> str:
    """Read UTF-8 text from disk."""
    return Path(path).read_text(encoding="utf-8")


def write_text(path: str | Path, text: str) -> Path:
    """Write UTF-8 text to disk, creating parent directories as needed."""
    output_path = Path(path)
    ensure_dir(output_path.parent)
    output_path.write_text(text, encoding="utf-8")
    return output_path


def read_json(path: str | Path) -> Any:
    """Read JSON from disk."""
    return json.loads(read_text(path))


def write_json(path: str | Path, data: Any) -> Path:
    """Write pretty-printed JSON to disk."""
    output_path = Path(path)
    ensure_dir(output_path.parent)
    output_path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return output_path


def file_exists(path: str | Path) -> bool:
    """Return whether a path exists."""
    return Path(path).exists()
