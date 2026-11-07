"""Shared strict JSON loading and atomic writing."""

from __future__ import annotations

import json
import os
import tempfile
from collections.abc import Callable, Generator
from contextlib import contextmanager, suppress
from pathlib import Path
from typing import Any


def load_json(path: Path) -> Any:
    """Load JSON strictly; propagate exceptions for callers to handle according to business rules."""
    with open(path, encoding="utf-8") as handle:
        return json.load(handle)


@contextmanager
def domain_error_on_value_error(
    factory: Callable[[ValueError], BaseException],
    *,
    extra_passthrough: tuple[type[BaseException], ...] = (),
) -> Generator[None]:
    """Convert business-parser ``ValueError`` to the caller-specified domain error.

    ``json.JSONDecodeError`` subclasses ``ValueError``; pass it through before generic ``ValueError`` because it indicates
    corrupt JSON syntax, not a business-invalid value such as path traversal or an
    invalid project name. Misclassification would return a misleading 4xx. The
    outer caller handles passed-through exceptions. `extra_passthrough` adds other
    ``ValueError`` subclasses for the same reason, such as ``UnicodeDecodeError``
    from non-UTF-8 scripts or domain errors with their own caller branches.
    """
    try:
        yield
    except (json.JSONDecodeError, *extra_passthrough):
        raise
    except ValueError as exc:
        raise factory(exc) from exc


def load_json_or_none(path: Path) -> Any | None:
    """Load JSON tolerantly, returning None on read or parse failure."""
    try:
        return load_json(path)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return None


def atomic_write_json(path: Path, data: Any) -> None:
    """Write JSON atomically using a same-directory tempfile and os.replace.

    fsync the temporary file before replacement. Otherwise power loss could publish
    an empty or partial file not yet on disk; unreadable project, script or version
    records reject the entire project (``docs/adr/0098``).
    """
    tmp_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=str(path.parent),
            prefix=".project.",
            suffix=".tmp",
            delete=False,
        ) as tmp:
            tmp_path = Path(tmp.name)
            json.dump(data, tmp, ensure_ascii=False, indent=2)
            tmp.flush()
            os.fsync(tmp.fileno())
        os.replace(tmp_path, path)
        tmp_path = None
    finally:
        if tmp_path is not None:
            with suppress(OSError):
                tmp_path.unlink()  # hard-delete: temp -- temporary file for atomic writing


def atomic_write_bytes(path: Path, data: bytes) -> None:
    """Restore file bytes atomically with a same-directory tempfile and ``os.replace``.

    Compensating multi-file transactions must restore replaced files byte for byte
    if later writes fail. Reserializing JSON changes whitespace/key order and cannot
    prove restoration of the pre-commit durable state, so rollback uses byte snapshots.
    """
    tmp_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb",
            dir=str(path.parent),
            prefix=".rollback.",
            suffix=".tmp",
            delete=False,
        ) as tmp:
            tmp_path = Path(tmp.name)
            tmp.write(data)
            tmp.flush()
            os.fsync(tmp.fileno())
        os.replace(tmp_path, path)
        tmp_path = None
    finally:
        if tmp_path is not None:
            with suppress(OSError):
                tmp_path.unlink()  # hard-delete: temp -- temporary file for atomic writing
