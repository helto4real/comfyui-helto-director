"""Atomic same-directory file replacement helpers."""

from __future__ import annotations

from collections.abc import Callable
import os
from pathlib import Path
import tempfile


def atomic_write(
    path: Path,
    writer: Callable[[Path], object],
    *,
    mode: int | None = None,
) -> None:
    """Write to a unique sibling file and atomically replace ``path``."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    file_descriptor, temp_name = tempfile.mkstemp(
        dir=path.parent,
        prefix=f".{path.name}.",
        suffix=".tmp",
    )
    os.close(file_descriptor)
    temp_path = Path(temp_name)
    try:
        writer(temp_path)
        if mode is not None:
            _chmod(temp_path, mode)
        os.replace(temp_path, path)
        if mode is not None:
            _chmod(path, mode)
    finally:
        temp_path.unlink(missing_ok=True)


def _chmod(path: Path, mode: int) -> None:
    try:
        os.chmod(path, mode)
    except OSError:
        pass
