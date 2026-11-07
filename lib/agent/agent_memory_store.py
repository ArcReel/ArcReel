"""Single storage implementation for listing/reading/upserting/deleting/clearing Agent-memory directories.

Parameterize by directory so project/user memory share one class: filename rules, size limits,
index overflow rules, and frontmatter parsing are identical. Separate implementations would
make both REST route groups diverge over time. Index line/byte counts and overflow judgments
come from ``lib.agent.agent_memory_index``, sharing session-assembly user-index truncation rules.

Absent directories are empty: Agents create memory on demand, so creators opening memory pages
before their first Agent memory write must see no errors.

No locks/conflict detection: same-directory temporary files + rename atomically write each file.
Concurrent PUTs use last-writer-wins; readers always receive one complete write, never partial
files. Project-memory delete/clear holds project locks and moves files into the project recycle
bin (``docs/adr/0107``); user memory is deleted directly.
"""

from __future__ import annotations

import os
import re
import shutil
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml

from lib.agent.agent_memory_index import INDEX_FILENAME, memory_index_stats
from lib.agent.agent_memory_paths import project_memory_dir
from lib.artifacts.formal_write import project_metadata_lock
from lib.infra.api_errors import BadRequestError, NotFoundError
from lib.infra.json_io import atomic_write_bytes
from lib.infra.path_safety import PathTraversalError, safe_join, try_safe_join
from lib.project import project_trash
from lib.project.project_activity import record_project_activity

#: Per-file memory body limit. Reject oversized writes so creators split files instead of Agents reading huge files every turn.
MAX_FILE_BYTES = 256 * 1024

#: Legal frontmatter ``type`` values; others produce unlabeled files, never errors.
MEMORY_TYPES = frozenset({"user", "feedback", "project", "reference"})

#: Filename limit, sharing visibility/read/write criteria with ``_FILENAME_PATTERN``.
FILENAME_MAX_LENGTH = 100

# Allow spaces/parentheses: recycle-bin restoration uses ``name (n).md`` when original names are occupied.
_FILENAME_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9._() -]*\.md$"


def is_valid_memory_filename(filename: str) -> bool:
    """Whether ``filename`` is a valid top-level memory filename.

    Initial characters must be alphanumeric, excluding ``.``-prefixed hidden/atomic-write temporary
    files. Temporary files neither appear in lists nor participate in memory reads/writes.
    """
    return len(filename) <= FILENAME_MAX_LENGTH and re.fullmatch(_FILENAME_PATTERN, filename) is not None


def parse_memory_frontmatter(raw: bytes) -> dict[str, Any] | None:
    """Parse memory YAML frontmatter into ``{name, description, type}``.

    The sole YAML parsing point for memory: parse server-side; frontend consumes only validated
    results. Missing frontmatter, syntax errors, nonobjects, or ``type`` outside :data:`MEMORY_TYPES`
    all return ``None``. Agents freely write memory, so incomplete metadata is normal, not an
    error; leave files unlabeled without failing entire list requests.
    """
    try:
        content = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        return None

    lines = content.splitlines()
    if not lines or lines[0].strip() != "---":
        return None
    try:
        end = lines.index("---", 1)
    except ValueError:
        return None
    try:
        loaded: Any = yaml.safe_load("\n".join(lines[1:end]))
    except yaml.YAMLError:
        return None
    if not isinstance(loaded, dict):
        return None

    memory_type = loaded.get("type")
    if not isinstance(memory_type, str) or memory_type not in MEMORY_TYPES:
        return None
    return {
        "name": _optional_text(loaded.get("name")),
        "description": _optional_text(loaded.get("description")),
        "type": memory_type,
    }


def _optional_text(value: object) -> str | None:
    """Optional frontmatter text fields: nonstrings/whitespace count as omitted."""
    if not isinstance(value, str) or not value.strip():
        return None
    return value.strip()


@dataclass(frozen=True)
class AgentMemoryStore:
    """Read/write facade for one memory directory, with ``directory`` derived by ``lib.agent.agent_memory_paths``.

    Only project memory supplies ``project_dir``; successful write/delete/clear advances that
    project’s recent-activity timestamp.
    """

    directory: Path
    project_dir: Path | None = None

    @classmethod
    def for_project(cls, project_dir: Path) -> AgentMemoryStore:
        """Project-memory read/write facade."""
        return cls(project_memory_dir(project_dir), project_dir=project_dir)

    def overview(self) -> dict[str, Any]:
        """List response: directory path, index statistics, and all visible memory entries.

        ``path`` is an absolute server path for creators opening the directory in file managers;
        paths are not user-facing copy and do not enter i18n.

        Entries can disappear between enumeration and reading (another tab deletes/clears); skip
        those and list the rest normally. One disappearing file must not fail the whole memory page.
        """
        index = {"exists": False, "line_count": 0, "byte_size": 0, "over_limit": False}
        files: list[dict[str, Any]] = []
        for path in self._visible_paths():
            if path.name == INDEX_FILENAME:
                index = _index_stats(path)
                continue
            stat = _stat_or_none(path)
            if stat is None:
                continue
            files.append(
                {
                    "name": path.name,
                    "size": stat.st_size,
                    "modified_at": datetime.fromtimestamp(stat.st_mtime, UTC).isoformat(),
                    "frontmatter": parse_memory_frontmatter(_read_bytes_or_empty(path)),
                }
            )
        return {"path": str(self.directory), "index": index, "files": files}

    def read(self, filename: str) -> bytes:
        """Read raw bytes from one memory file.

        Return unchanged bytes without decoding first: Agent-written files may contain non-UTF-8
        bytes; creators must still receive them for repair, rather than the read path returning 500.
        """
        path = self._resolve(filename)
        if not path.is_file():
            raise NotFoundError("memory_file_not_found", filename=filename)
        try:
            return path.read_bytes()
        except OSError as exc:
            raise NotFoundError("memory_file_not_found", filename=filename) from exc

    def write(self, filename: str, content: bytes) -> None:
        """Idempotent upsert creates absent directories and rejects bodies exceeding :data:`MAX_FILE_BYTES`.

        Reject directory-occupied names as invalid filenames, matching read/delete/list handling:
        Agent-writable memory can contain ``notes.md/``, making ``os.replace`` raise
        ``IsADirectoryError``. Otherwise creators could neither save nor delete it through the
        file cabinet, leaving only full-directory clear.
        """
        path = self._resolve(filename, ensure_directory=True)
        if path.is_dir():
            raise BadRequestError("memory_invalid_filename", filename=filename)
        if len(content) > MAX_FILE_BYTES:
            raise BadRequestError("memory_file_too_large", filename=filename, limit_kib=MAX_FILE_BYTES // 1024)
        atomic_write_bytes(path, content)
        self._record_activity()

    def delete(self, filename: str) -> None:
        """Delete one memory file, including the deletable ``MEMORY.md`` index."""
        path = self._resolve(filename)
        if not path.is_file():
            raise NotFoundError("memory_file_not_found", filename=filename)
        try:
            if self.project_dir is None:
                path.unlink()  # hard-delete: out-of-scope -- User memory belongs to no project
            else:
                rel = self._project_rel(self.project_dir, filename)
                with project_metadata_lock(self.project_dir):
                    project_trash.record_deletion(self.project_dir, {"kind": "memory", "path": rel}, [rel])
        except FileNotFoundError as exc:
            raise NotFoundError("memory_file_not_found", filename=filename) from exc
        self._record_activity()

    def clear(self) -> None:
        """Clear the entire memory directory and recreate it empty, without creating an empty index.

        Agents rebuild indexes with the first memory write; precreating empty ``MEMORY.md`` would
        only display a zero-line index indistinguishable from no memory.
        """
        if self.project_dir is not None and self.directory.is_dir() and not self.directory.is_symlink():
            with project_metadata_lock(self.project_dir):
                files = [
                    self._project_rel(self.project_dir, path.relative_to(self.directory).as_posix())
                    for path in sorted(self.directory.rglob("*"))
                    if path.is_file() and not path.is_symlink()
                ]
                if files:
                    obj = {"kind": "memory", "path": self.directory.relative_to(self.project_dir).as_posix()}
                    project_trash.record_deletion(self.project_dir, obj, files)
        if self.project_dir is None:
            # hard-delete: out-of-scope -- User memory belongs to no project
            shutil.rmtree(self.directory, ignore_errors=True)
        # Agents may write project memory concurrently; retain directories after recycling to avoid hard-deleting new content written after enumeration.
        self.directory.mkdir(parents=True, exist_ok=True)
        self._record_activity()

    def _project_rel(self, project_dir: Path, name: str) -> str:
        return f"{self.directory.relative_to(project_dir).as_posix()}/{name}"

    def _record_activity(self) -> None:
        if self.project_dir is not None:
            record_project_activity(self.project_dir)

    def _visible_paths(self) -> list[Path]:
        """Visible memory files sorted by name: top-level, valid filenames, real files remaining inside the directory.

        Apply ``try_safe_join`` realpath semantics per entry, not merely ``is_file()``: external
        symlinks satisfy ``is_file()`` but cannot be read, exposing entries guaranteed to return 404.
        Filename rules exclude subdirectories and non-``.md`` entries.
        """
        try:
            names = sorted(entry.name for entry in self.directory.iterdir())
        except OSError:
            return []
        resolved: list[Path] = []
        for name in names:
            if not is_valid_memory_filename(name):
                continue
            path = try_safe_join(self.directory, name, require_file=True)
            if path is not None:
                resolved.append(path)
        return resolved

    def _resolve(self, filename: str, *, ensure_directory: bool = False) -> Path:
        """Resolve untrusted ``filename`` to an absolute in-directory path.

        Check filename rules first, then ``safe_join``: top-level ``*.md`` is a product rule, while
        safe joining guards escapes. Both map to ``memory_invalid_filename`` because creators
        experience the same unusable-filename condition.
        """
        if not is_valid_memory_filename(filename):
            raise BadRequestError("memory_invalid_filename", filename=filename)
        if ensure_directory:
            self.directory.mkdir(parents=True, exist_ok=True)
        try:
            return safe_join(self.directory, filename)
        except PathTraversalError as exc:
            raise BadRequestError("memory_invalid_filename", filename=filename) from exc


def _index_stats(path: Path) -> dict[str, Any]:
    """Index line/byte counts and overflow, matching session-assembly truncation.

    Unreadable/non-UTF-8 indexes count as zero: assembly cannot read them either, so reporting
    disk sizes would show creators an index unavailable to Agents. Overflow is advisory only,
    affecting neither writes nor reads.
    """
    stats = memory_index_stats(_read_text_or_empty(path))
    return {
        "exists": True,
        "line_count": stats.line_count,
        "byte_size": stats.byte_size,
        "over_limit": stats.over_limit,
    }


def _stat_or_none(path: Path) -> os.stat_result | None:
    """Stat entries, returning ``None`` if gone or unreadable after enumeration."""
    try:
        return path.stat()
    except OSError:
        return None


def _read_bytes_or_empty(path: Path) -> bytes:
    """Unreadable files count as empty; one file’s I/O failure must not fail entire list requests."""
    try:
        return path.read_bytes()
    except OSError:
        return b""


def _read_text_or_empty(path: Path) -> str:
    """Read UTF-8 text; unreadability/decoding failure both count as empty content."""
    try:
        return _read_bytes_or_empty(path).decode("utf-8")
    except UnicodeDecodeError:
        return ""
