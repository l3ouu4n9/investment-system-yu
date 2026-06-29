"""Basic file I/O helpers."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any


def ensure_dir(path: str | Path) -> Path:
    """Create a directory if it does not already exist."""
    directory = Path(path)
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def atomic_write_text(path: str | Path, text: str) -> Path:
    """Atomically write UTF-8 text: write a temp file in the same dir, then replace.

    The bytes are written to a temporary file in the *same directory* as the
    target (so ``os.replace`` is a same-filesystem, atomic rename — never a
    cross-device copy), flushed and ``fsync``-ed, then ``os.replace``-d onto the
    target. A reader therefore only ever sees the complete old file or the
    complete new file — never partially written content — even if the process
    crashes mid-write.

    Encoding / newline behavior matches :func:`write_text` (UTF-8, default
    newline handling). On any failure the temp file is removed best-effort and
    the original exception propagates; the target is left untouched (absent or
    its prior content), never partial.

    Note: atomicity is **per file**. Publishing several files is still a
    sequence of independent atomic replaces — see callers for the group-level
    (multi-file) limitation.
    """
    output_path = Path(path)
    ensure_dir(output_path.parent)
    tmp_path = output_path.with_name(f".{output_path.name}.tmp.{os.getpid()}")
    try:
        with open(tmp_path, "w", encoding="utf-8") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_path, output_path)
    except BaseException:
        # Best-effort cleanup; never mask the original error.
        try:
            tmp_path.unlink()
        except OSError:
            pass
        raise
    return output_path


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
