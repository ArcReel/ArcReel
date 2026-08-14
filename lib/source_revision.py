"""Deterministic revisions for the source text covered by asset analysis."""

from __future__ import annotations

import hashlib
import json
import re
import stat
import unicodedata
from collections.abc import Mapping
from pathlib import Path, PurePosixPath
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from lib.episode_ledger import SOURCE_TEXT_SUFFIXES

_DERIVED_EPISODE_RE = re.compile(r"episode_[0-9]+\.txt")

#: 源文修订号载荷里的源文件性质键名冻结在旧名上：它参与 sha256，算出的修订号写在
#: ``workflow.asset_inventory.source_revision`` 上并被逐字比对。改名会让既有资产盘点
#: 的修订号全部对不上、被判过期，把用户重新赶回资产分析。取值仍来自已更名的 source_file_type。
_SOURCE_FILE_TYPE_REVISION_KEY = "source_kind"


class SourceScope(BaseModel):
    """The source files included in one asset-inventory analysis."""

    model_config = ConfigDict(extra="forbid")

    kind: Literal["all", "files"]
    files: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _validate_shape(self) -> SourceScope:
        if self.kind == "all" and self.files:
            raise ValueError("all scope must not name individual files")
        if self.kind == "files" and not self.files:
            raise ValueError("files scope must name at least one file")
        return self


class SourceRevisionBlocker(BaseModel):
    """A source condition that prevents a trustworthy revision."""

    code: str
    path: str
    reason: str


class SourceRevisionResult(BaseModel):
    """Revision calculation result; blockers and a revision are mutually exclusive."""

    scope: SourceScope | None
    revision: str | None
    files: list[str] = Field(default_factory=list)
    blockers: list[SourceRevisionBlocker] = Field(default_factory=list)


def _blocked(
    scope: SourceScope | None,
    code: str,
    path: str,
    reason: str,
    *,
    files: list[str] | None = None,
) -> SourceRevisionResult:
    return SourceRevisionResult(
        scope=scope,
        revision=None,
        files=files or [],
        blockers=[SourceRevisionBlocker(code=code, path=path, reason=reason)],
    )


def _canonical_relative_path(value: str) -> str | None:
    if not value or "\\" in value or "\x00" in value:
        return None
    pure = PurePosixPath(value)
    if pure.is_absolute() or ".." in pure.parts:
        return None
    normalized = unicodedata.normalize("NFC", pure.as_posix())
    if normalized in {"", "."}:
        return None
    return normalized


def _all_source_paths(
    project_dir: Path, scope: SourceScope
) -> tuple[list[tuple[str, Path]], SourceRevisionResult | None]:
    source_dir = project_dir / "source"
    if source_dir.is_symlink():
        return [], _blocked(scope, "source_symlink", "source", "source directory must not be a symbolic link")
    if not source_dir.exists():
        return [], None
    if not source_dir.is_dir():
        return [], _blocked(scope, "source_not_directory", "source", "source path is not a directory")
    try:
        entries = list(source_dir.iterdir())
    except OSError as exc:
        return [], _blocked(scope, "source_unreadable", "source", f"source directory cannot be read: {exc}")

    paths: list[tuple[str, Path]] = []
    for path in entries:
        name = path.name
        if name.startswith((".", "_")) or path.suffix.lower() not in SOURCE_TEXT_SUFFIXES:
            continue
        if _DERIVED_EPISODE_RE.fullmatch(name):
            continue
        rel = unicodedata.normalize("NFC", f"source/{name}")
        paths.append((rel, path))
    paths.sort(key=lambda item: item[1])
    return paths, None


def _scoped_source_paths(
    project_dir: Path, scope: SourceScope
) -> tuple[list[tuple[str, Path]], SourceRevisionResult | None]:
    source_dir = project_dir / "source"
    if source_dir.is_symlink():
        return [], _blocked(scope, "source_symlink", "source", "source directory must not be a symbolic link")
    if source_dir.exists() and not source_dir.is_dir():
        return [], _blocked(scope, "source_not_directory", "source", "source path is not a directory")
    try:
        entries = list(source_dir.iterdir()) if source_dir.exists() else []
    except OSError as exc:
        return [], _blocked(scope, "source_unreadable", "source", f"source directory cannot be read: {exc}")
    by_canonical_path: dict[str, list[Path]] = {}
    for entry in entries:
        rel = unicodedata.normalize("NFC", f"source/{entry.name}")
        by_canonical_path.setdefault(rel, []).append(entry)

    paths: list[tuple[str, Path]] = []
    seen: set[str] = set()
    for requested in scope.files:
        rel = _canonical_relative_path(requested)
        if rel is None:
            return [], _blocked(scope, "source_path_escape", str(requested), "source path must be project-relative")
        pure = PurePosixPath(rel)
        if len(pure.parts) != 2 or pure.parts[0] != "source" or pure.suffix.lower() not in SOURCE_TEXT_SUFFIXES:
            return [], _blocked(scope, "invalid_source_scope", rel, "scoped files must be source text files")
        if _DERIVED_EPISODE_RE.fullmatch(pure.name):
            return [], _blocked(scope, "invalid_source_scope", rel, "derived episode files are not source text")
        if rel in seen:
            continue
        seen.add(rel)
        candidates = by_canonical_path.get(rel, [])
        if len(candidates) > 1:
            return [], _blocked(scope, "source_path_collision", rel, "source paths collide after Unicode normalization")
        path = candidates[0] if candidates else project_dir.joinpath(*pure.parts)
        paths.append((rel, path))
    paths.sort(key=lambda item: item[0])
    return paths, None


def _read_sources(
    project_dir: Path,
    scope: SourceScope,
    paths: list[tuple[str, Path]],
) -> tuple[list[tuple[str, str]], SourceRevisionResult | None]:
    project_real = project_dir.resolve()
    fingerprints: list[tuple[str, str]] = []
    canonical_paths: set[str] = set()
    for rel, path in paths:
        if rel in canonical_paths:
            return [], _blocked(scope, "source_path_collision", rel, "source paths collide after Unicode normalization")
        canonical_paths.add(rel)
        if path.is_symlink():
            return [], _blocked(scope, "source_symlink", rel, "source file must not be a symbolic link")
        try:
            resolved = path.resolve(strict=True)
            resolved.relative_to(project_real)
        except FileNotFoundError:
            return [], _blocked(scope, "source_missing", rel, "source file does not exist")
        except (OSError, ValueError):
            return [], _blocked(scope, "source_path_escape", rel, "source file resolves outside the project")
        try:
            mode = path.stat().st_mode
        except OSError as exc:
            return [], _blocked(scope, "source_unreadable", rel, f"source metadata cannot be read: {exc}")
        if not stat.S_ISREG(mode):
            return [], _blocked(scope, "source_not_file", rel, "source path is not a regular file")
        if mode & (stat.S_IRUSR | stat.S_IRGRP | stat.S_IROTH) == 0:
            return [], _blocked(scope, "source_unreadable", rel, "source file has no read permission")
        try:
            content = path.read_bytes()
        except OSError as exc:
            return [], _blocked(scope, "source_unreadable", rel, f"source file cannot be read: {exc}")
        try:
            content.decode("utf-8")
        except UnicodeDecodeError:
            return [], _blocked(scope, "source_unreadable", rel, "source file is not valid UTF-8")
        fingerprints.append((rel, hashlib.sha256(content).hexdigest()))
    return fingerprints, None


def compute_source_revision(
    project_dir: Path,
    project: Mapping[str, Any],
    scope: SourceScope | Mapping[str, Any],
) -> SourceRevisionResult:
    """Compute a revision from canonical paths, raw bytes, and source semantics.

    Invalid or unsafe input is represented as a blocker so status consumers can
    remain available and explain how to recover instead of silently omitting it.
    """

    try:
        parsed_scope = scope if isinstance(scope, SourceScope) else SourceScope.model_validate(scope)
    except ValidationError as exc:
        return _blocked(None, "invalid_source_scope", "workflow.asset_inventory.scope", str(exc))

    if parsed_scope.kind == "all":
        paths, error = _all_source_paths(project_dir, parsed_scope)
    else:
        paths, error = _scoped_source_paths(project_dir, parsed_scope)
    if error is not None:
        return error

    fingerprints, error = _read_sources(project_dir, parsed_scope, paths)
    if error is not None:
        return error

    canonical_fingerprints = sorted(fingerprints, key=lambda item: item[0])
    payload = {
        "files": [{"path": rel, "sha256": digest} for rel, digest in canonical_fingerprints],
        _SOURCE_FILE_TYPE_REVISION_KEY: project.get("source_file_type", "novel"),
        "source_language": project.get("source_language"),
    }
    serialized = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    revision = f"sha256-v1:{hashlib.sha256(serialized).hexdigest()}"
    canonical_scope = (
        parsed_scope if parsed_scope.kind == "all" else SourceScope(kind="files", files=[rel for rel, _path in paths])
    )
    return SourceRevisionResult(
        scope=canonical_scope,
        revision=revision,
        files=[rel for rel, _path in paths],
    )


__all__ = [
    "SourceRevisionBlocker",
    "SourceRevisionResult",
    "SourceScope",
    "compute_source_revision",
]
