"""Reference index: derive roles for arbitrary project paths from data references, shared by file views, unreferenced checks, and Agent deletion.

Roles follow references, not inferred paths. Sources:

- Manifests (current formal files), history (historical versions).
- ``project.json`` (sheets, image/audio references, style references, BGM, episode ledger, whole-source lists).
- Scripts (storyboard/unit media, end frames, grids).

Exceptions are directory-owned content: timelines at ``edit_timelines/episode_N/<id>.json``, memory at
``.arcreel/memory/``, materialized Agent configuration at ``CLAUDE.md`` / ``.claude/``. Project-specific
unmanifested skills are also configuration, so no materialization-manifest read is needed. Dot-prefixed
paths/migration backups are :attr:`FileRole.INFRASTRUCTURE`, excluded from file views.

The index is an unlocked read-only construction-time snapshot. Deletion callers rebuild under project locks before decisions."""

from __future__ import annotations

import json
import os
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path, PurePosixPath
from typing import Any

from lib.agent.agent_memory_paths import ARCREEL_DIRNAME, MEMORY_DIRNAME
from lib.artifacts.artifact_manifest import ArtifactKind, ProjectArtifactManifestAdapter
from lib.bgm.library import PROJECT_BGM_FIELD
from lib.episode.episode_ids import item_id_episode
from lib.episode.episode_paths import (
    REFERENCE_VIDEO_SCRIPT_PLAN_FILENAME,
    REFERENCE_VIDEO_SCRIPT_PLAN_LEGACY_FILENAME,
    SCRIPT_PLAN_FILENAMES,
    episode_drafts_dir,
    episode_source_relpath,
    script_plan_read_candidates,
)
from lib.episode.episode_sources import (
    SOURCE_SNAPSHOTS_DIR,
    SourceOrigin,
    episode_source_origin,
    is_whole_source_file_path,
    whole_source_files,
)
from lib.project.asset_types import ASSET_SPECS, DERIVATIVES_FIELD, normalize_asset_name
from lib.project.project_migrations.backups import is_versioned_backup_name
from lib.project.resource_paths import CHARACTER_DERIVATIVE_RESOURCE_TYPE, END_FRAME_RESOURCE_TYPE
from lib.script.draft_quarantine import QUARANTINE_FILENAMES
from lib.script.script_models import get_generated_assets

__all__ = ["FileOwner", "FileReference", "FileRole", "ReferenceIndex", "is_infrastructure_path"]


class FileRole(StrEnum):
    """A file's role within a project."""

    STRUCTURE = "structure"
    REFERENCED = "referenced"
    HISTORY = "history"
    UNREFERENCED = "unreferenced"
    DERIVED = "derived"
    AGENT_CONFIG = "agent_config"
    MEMORY = "memory"
    INFRASTRUCTURE = "infrastructure"


#: Highest-priority role wins for multiply referenced paths.
_ROLE_PRIORITY = {
    FileRole.STRUCTURE: 4,
    FileRole.REFERENCED: 3,
    FileRole.DERIVED: 2,
    FileRole.HISTORY: 1,
}


@dataclass(frozen=True, slots=True)
class FileOwner:
    """UI object owning a file, for navigation to its owning screen.

    ``kind``: ``asset`` (``asset_type`` + ``id`` is asset name), ``segment`` / ``reference_unit``
    (``episode`` + ``id`` is item ID), ``grid``, ``episode``, ``timeline`` (``id`` is timeline ID),
    ``source`` (episode view), ``memory`` (``id`` is memory filename), ``project`` (project settings)."""

    kind: str
    id: str | None = None
    episode: int | None = None
    asset_type: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            key: value
            for key, value in (
                ("kind", self.kind),
                ("id", self.id),
                ("episode", self.episode),
                ("asset_type", self.asset_type),
            )
            if value is not None
        }


@dataclass(frozen=True, slots=True)
class FileReference:
    role: FileRole
    owner: FileOwner | None = None
    #: Current selected history snapshot; version management refuses its deletion.
    current: bool = False


_PROJECT = FileOwner("project")
_SOURCE = FileOwner("source")
_UNREFERENCED = FileReference(FileRole.UNREFERENCED)
_INFRASTRUCTURE = FileReference(FileRole.INFRASTRUCTURE)
_AGENT_CONFIG = FileReference(FileRole.AGENT_CONFIG)

_TIMELINE_PATH_RE = re.compile(r"^edit_timelines/episode_[1-9]\d*/(tl-[0-9a-f]{8})\.json$")
_RENDER_RECORD_SUFFIX = ".render.json"
#: Script item arrays and ID fields; reference-to-video items are units.
_ITEM_ARRAYS = (("segments", "segment_id"), ("scenes", "scene_id"), ("shots", "shot_id"), ("video_units", "unit_id"))
_ASSET_TYPE_BY_BUCKET = {spec.bucket_key: asset_type for asset_type, spec in ASSET_SPECS.items()}
_SEGMENT_RESOURCE_TYPES = frozenset({"storyboards", "videos", "audio", END_FRAME_RESOURCE_TYPE})
_ASSET_RESOURCE_TYPES = frozenset({*_ASSET_TYPE_BY_BUCKET, CHARACTER_DERIVATIVE_RESOURCE_TYPE})
#: Workflow-owned episode drafts: quarantine and episode-view-read plans, including legacy ``.md``.
_EPISODE_DRAFT_FILENAMES = tuple(
    dict.fromkeys(
        (
            *QUARANTINE_FILENAMES,
            *(name for mode in SCRIPT_PLAN_FILENAMES for name in script_plan_read_candidates(mode)),
            REFERENCE_VIDEO_SCRIPT_PLAN_FILENAME,
            REFERENCE_VIDEO_SCRIPT_PLAN_LEGACY_FILENAME,
        )
    )
)


def is_infrastructure_path(rel: str) -> bool:
    """Locks, migration backups, manifests, and internal metadata hidden from file views.

    ``CLAUDE.md`` / ``.claude/`` are Agent configuration; ``.arcreel/memory/`` is project memory.
    All other dot-prefixed paths are infrastructure."""
    parts = PurePosixPath(rel).parts
    if not parts:
        return False
    if parts[0] == ".claude":
        return False
    if parts[:2] == (ARCREEL_DIRNAME, MEMORY_DIRNAME) or parts == (ARCREEL_DIRNAME,):
        return any(part.startswith(".") for part in parts[2:])
    return any(part.startswith(".") or part == "__MACOSX" or is_versioned_backup_name(part) for part in parts)


def _normalize(value: object) -> str | None:
    """Registered project-relative paths; discard external URLs, absolute paths, dot segments, and dirty values."""
    if not isinstance(value, str) or not value or "\\" in value or "://" in value:
        return None
    path = PurePosixPath(value.removeprefix("./"))
    if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        return None
    return path.as_posix()


def _read_json(path: Path) -> Any:
    if not path.is_file() or path.is_symlink():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


class ReferenceIndex:
    """Project reference snapshot: :meth:`role_of` identifies arbitrary relative paths; :meth:`contains_resource` checks continued resource existence."""

    def __init__(self, project_dir: Path) -> None:
        self._project_dir = project_dir
        self._directory_entries: dict[Path, dict[str, Path]] = {}
        self._refs: dict[str, FileReference] = {}
        self._resources: set[tuple[str, str]] = set()
        self._item_episodes: dict[str, int] = {}
        #: Whole-source filename stem → role; preserved ``source/raw/<主体>.<原扩展名>`` uploads follow it.
        self._source_stems: dict[str, FileReference] = {}

    @classmethod
    def build(cls, project_dir: Path) -> ReferenceIndex:
        """Build from project data; propagate corrupt data instead of misclassifying used files as unreferenced."""
        index = cls(project_dir)
        project = _read_json(project_dir / "project.json") or {}
        index._mark("project.json", FileRole.STRUCTURE, _PROJECT)
        index._index_assets(project)
        index._index_project_files(project)
        index._index_scripts(project_dir, project)
        index._index_sources(project)
        index._index_manifest(project_dir)
        index._index_versions(project_dir)
        index._index_grid_records(project_dir)
        return index

    # ------------------------------------------------------------------ Queries

    def reference_of(self, rel: str) -> FileReference:
        """Role/owner of a project-relative POSIX file path."""
        rel = self._canonical(rel)
        if is_infrastructure_path(rel):
            return _INFRASTRUCTURE
        if rel in self._refs:
            return self._refs[rel]
        rel = self._storage_path(rel)
        parts = PurePosixPath(rel).parts
        if rel == self._canonical("CLAUDE.md") or parts[:1] == (".claude",):
            return _AGENT_CONFIG
        if parts[:2] == (ARCREEL_DIRNAME, MEMORY_DIRNAME) and len(parts) == 3:
            return FileReference(FileRole.MEMORY, FileOwner("memory", id=parts[2]))
        timeline = rel.lower()
        if _TIMELINE_PATH_RE.match(timeline) and self._canonical(timeline) == rel:
            episode = int(PurePosixPath(timeline).parts[1].removeprefix("episode_"))
            return FileReference(
                FileRole.STRUCTURE, FileOwner("timeline", id=PurePosixPath(timeline).stem, episode=episode)
            )
        if parts[:2] == ("source", "raw") and len(parts) == 3:
            # Original uploads follow normalized texts on add/delete; episode views also handle originals outside whole source.
            return self._source_stems.get(PurePosixPath(rel).stem, FileReference(FileRole.UNREFERENCED, _SOURCE))
        if is_whole_source_file_path(rel):
            # Unregistered source files belonging to neither whole source nor episodes are handled by episode views.
            return FileReference(FileRole.UNREFERENCED, _SOURCE)
        return _UNREFERENCED

    def _storage_path(self, rel: str) -> str:
        """Recognize canonical storage directory spellings and only filesystem-confirmed same-directory aliases."""
        parts = PurePosixPath(rel).parts
        for prefix in ("source/raw", "source", ".claude", f"{ARCREEL_DIRNAME}/{MEMORY_DIRNAME}"):
            expected = PurePosixPath(prefix).parts
            if self._canonical(prefix) == "/".join(parts[: len(expected)]):
                return "/".join((*expected, *parts[len(expected) :]))
        return rel

    def role_of(self, rel: str) -> FileRole:
        return self.reference_of(rel).role

    def contains_resource(self, resource_type: str, resource_id: str) -> bool:
        """Whether resources (``lib/project/resource_paths`` type + ID) remain in project data; compare asset names with NFC."""
        if resource_type in _ASSET_RESOURCE_TYPES:
            resource_id = normalize_asset_name(resource_id)
        return (resource_type, resource_id) in self._resources

    # ------------------------------------------------------------------ Construction

    def _canonical(self, rel: str) -> str:
        """Use actual disk names to unify case/Unicode aliases, never merge independently existing files."""
        normalized = _normalize(rel)
        if normalized is None:
            return rel
        directory = self._project_dir
        parts: list[str] = []
        try:
            for part in PurePosixPath(normalized).parts:
                candidate = directory / part
                if candidate.is_symlink():
                    return rel
                if directory not in self._directory_entries:
                    self._directory_entries[directory] = {entry.name: entry for entry in directory.iterdir()}
                entries = self._directory_entries[directory]
                actual = entries.get(part)
                if actual is None:
                    identity = candidate.stat()
                    actual = next(
                        (
                            entry
                            for entry in entries.values()
                            if os.path.samestat(entry.stat(follow_symlinks=False), identity)
                        ),
                        None,
                    )
                if actual is None:
                    return rel
                parts.append(actual.name)
                directory = actual
        except (FileNotFoundError, NotADirectoryError):
            return rel
        return "/".join(parts)

    def _mark(self, rel: object, role: FileRole, owner: FileOwner | None = None, *, current: bool = False) -> None:
        path = _normalize(rel)
        if path is None:
            return
        path = self._canonical(path)
        existing = self._refs.get(path)
        if existing is None or _ROLE_PRIORITY[role] > _ROLE_PRIORITY[existing.role]:
            self._refs[path] = FileReference(role, owner, current)

    def _index_assets(self, project: Mapping[str, Any]) -> None:
        for asset_type, spec in ASSET_SPECS.items():
            bucket = project.get(spec.bucket_key)
            if not isinstance(bucket, Mapping):
                continue
            for name, asset in bucket.items():
                if not isinstance(asset, Mapping):
                    continue
                owner = FileOwner("asset", id=str(name), asset_type=asset_type)
                self._resources.add((spec.bucket_key, normalize_asset_name(str(name))))
                for field in (spec.sheet_field, *spec.extra_string_fields, *spec.original_image_fields):
                    for value in _as_list(asset.get(field)):
                        self._mark(value, FileRole.REFERENCED, owner)
                derivatives = asset.get(DERIVATIVES_FIELD) if spec.supports_derivatives else None
                for derivative_name, derivative in (
                    (derivatives or {}).items() if isinstance(derivatives, Mapping) else ()
                ):
                    self._resources.add(
                        (CHARACTER_DERIVATIVE_RESOURCE_TYPE, normalize_asset_name(f"{name}/{derivative_name}"))
                    )
                    if isinstance(derivative, Mapping):
                        self._mark(derivative.get(spec.sheet_field), FileRole.REFERENCED, owner)

    def _index_project_files(self, project: Mapping[str, Any]) -> None:
        self._mark(project.get("style_image"), FileRole.REFERENCED, _PROJECT)
        bgm = project.get(PROJECT_BGM_FIELD)
        for track in bgm.values() if isinstance(bgm, Mapping) else ():
            if isinstance(track, Mapping):
                self._mark(track.get("file"), FileRole.REFERENCED, _PROJECT)

    def _index_scripts(self, project_dir: Path, project: Mapping[str, Any]) -> None:
        for entry in _ledger(project):
            episode = entry["episode"]
            owner = FileOwner("episode", episode=episode)
            for filename in _EPISODE_DRAFT_FILENAMES:
                draft = episode_drafts_dir(project_dir, episode) / filename
                self._mark(draft.relative_to(project_dir).as_posix(), FileRole.STRUCTURE, owner)
            script_file = _normalize(entry.get("script_file"))
            if script_file is None:
                continue
            self._mark(script_file, FileRole.STRUCTURE, owner)
            script = _read_json(project_dir / script_file)
            if isinstance(script, Mapping):
                self._index_script_items(script, episode)

    def _index_script_items(self, script: Mapping[str, Any], episode: int) -> None:
        for array_key, id_field in _ITEM_ARRAYS:
            items = script.get(array_key)
            for item in items if isinstance(items, list) else ():
                if not isinstance(item, dict) or not isinstance(item.get(id_field), str):
                    continue
                item_id = item[id_field]
                self._item_episodes[item_id] = episode
                if array_key == "video_units":
                    owner = FileOwner("reference_unit", id=item_id, episode=episode)
                    # Video units also own narration audio.
                    self._resources.update({("reference_videos", item_id), ("audio", item_id)})
                else:
                    owner = FileOwner("segment", id=item_id, episode=episode)
                    self._resources.update((resource_type, item_id) for resource_type in _SEGMENT_RESOURCE_TYPES)
                assets = get_generated_assets(item)
                for field in ("storyboard_image", "storyboard_last_image", "video_clip", "narration_audio"):
                    self._mark(assets.get(field), FileRole.REFERENCED, owner)
                self._mark(item.get("end_frame_image"), FileRole.REFERENCED, owner)
                # Video-derived thumbnails are regenerable.
                self._mark(assets.get("video_thumbnail"), FileRole.DERIVED, owner)
                grid_id = assets.get("grid_id")
                if isinstance(grid_id, str) and grid_id:
                    self._index_grid(grid_id, episode)

    def _index_grid(self, grid_id: str, episode: int | None) -> None:
        owner = FileOwner("grid", id=grid_id, episode=episode)
        self._resources.add(("grids", grid_id))
        self._mark(f"grids/{grid_id}.json", FileRole.STRUCTURE, owner)
        self._mark(f"grids/{grid_id}.png", FileRole.REFERENCED, owner)

    def _index_grid_records(self, project_dir: Path) -> None:
        """Queued/failed grids lacking registrations/script references still belong to grid workflows; index last so known-episode references win."""
        for record in sorted((project_dir / "grids").glob("grid_*.json")):
            self._index_grid(record.stem, None)

    def _index_sources(self, project: Mapping[str, Any]) -> None:
        for rel in whole_source_files(project):
            rel = self._canonical(rel)
            self._mark(rel, FileRole.STRUCTURE, _SOURCE)
            self._mark(f"{SOURCE_SNAPSHOTS_DIR}/{PurePosixPath(rel).name}", FileRole.DERIVED, _SOURCE)
            self._source_stems[PurePosixPath(rel).stem] = FileReference(FileRole.STRUCTURE, _SOURCE)
        for entry in _ledger(project):
            origin = episode_source_origin(entry)
            if origin is SourceOrigin.NONE:
                continue
            owner = FileOwner("episode", episode=entry["episode"])
            # Cut-episode files derive from ledgers; source-owning episode files are their actual source.
            role = FileRole.DERIVED if origin is SourceOrigin.WHOLE_SOURCE else FileRole.STRUCTURE
            self._mark(episode_source_relpath(entry["episode"]), role, owner)

    def _index_manifest(self, project_dir: Path) -> None:
        for key, entry in ProjectArtifactManifestAdapter(project_dir).snapshot_entries().items():
            owner, role = _manifest_owner(key.kind, key.components, self._item_episodes)
            self._mark(entry.artifact_path, role, owner)
            if key.kind in {ArtifactKind.EPISODE_FINAL_CUT, ArtifactKind.EPISODE_JIANYING_DRAFT}:
                path = PurePosixPath(entry.artifact_path)
                self._mark(path.with_name(path.stem + _RENDER_RECORD_SUFFIX).as_posix(), FileRole.DERIVED, owner)
            if key.kind is ArtifactKind.EPISODE_GRID:
                self._index_grid(str(key.components[1]), int(key.components[0]))

    def _index_versions(self, project_dir: Path) -> None:
        versions_file = project_dir / "versions" / "versions.json"
        data = _read_json(versions_file)
        if data is None:
            return
        self._mark("versions/versions.json", FileRole.STRUCTURE)
        for resource_type, resources in data.items() if isinstance(data, Mapping) else ():
            for resource_id, record in resources.items() if isinstance(resources, Mapping) else ():
                owner = self._version_owner(resource_type, str(resource_id))
                if not isinstance(record, Mapping):
                    continue
                versions = record.get("versions")
                for version in versions if isinstance(versions, list) else ():
                    if isinstance(version, Mapping):
                        number = version.get("version")
                        current = number is not None and number == record.get("current_version")
                        self._mark(version.get("file"), FileRole.HISTORY, owner, current=current)

    def _version_owner(self, resource_type: str, resource_id: str) -> FileOwner | None:
        if resource_type in _ASSET_TYPE_BY_BUCKET:
            return FileOwner("asset", id=resource_id, asset_type=_ASSET_TYPE_BY_BUCKET[resource_type])
        if resource_type == CHARACTER_DERIVATIVE_RESOURCE_TYPE:
            return FileOwner("asset", id=resource_id.partition("/")[0], asset_type="character")
        episode = self._item_episodes.get(resource_id, item_id_episode(resource_id))
        if resource_type in _SEGMENT_RESOURCE_TYPES:
            return FileOwner("segment", id=resource_id, episode=episode)
        if resource_type == "reference_videos":
            return FileOwner("reference_unit", id=resource_id, episode=episode)
        if resource_type == "grids":
            return FileOwner("grid", id=resource_id)
        return None


def _as_list(value: object) -> Iterable[object]:
    return value if isinstance(value, list) else (value,)


def _ledger(project: Mapping[str, Any]) -> list[dict[str, Any]]:
    episodes = project.get("episodes")
    return [
        entry
        for entry in (episodes if isinstance(episodes, list) else ())
        if isinstance(entry, dict) and type(entry.get("episode")) is int and entry["episode"] > 0
    ]


def _manifest_owner(
    kind: ArtifactKind, components: tuple[str | int, ...], item_episodes: Mapping[str, int]
) -> tuple[FileOwner | None, FileRole]:
    if kind is ArtifactKind.ASSET_SHEET:
        return FileOwner("asset", id=str(components[1]), asset_type=str(components[0])), FileRole.REFERENCED
    if kind is ArtifactKind.PROJECT_BGM:
        return _PROJECT, FileRole.REFERENCED
    episode = int(components[0])
    if kind in {ArtifactKind.EPISODE_SCRIPT, ArtifactKind.EPISODE_SCRIPT_PLAN}:
        return FileOwner("episode", episode=episode), FileRole.STRUCTURE
    if kind in {ArtifactKind.EPISODE_STORYBOARD, ArtifactKind.EPISODE_VIDEO, ArtifactKind.EPISODE_AUDIO}:
        return FileOwner("segment", id=str(components[1]), episode=episode), FileRole.REFERENCED
    if kind is ArtifactKind.EPISODE_GRID:
        return FileOwner("grid", id=str(components[1]), episode=episode), FileRole.REFERENCED
    if kind in {ArtifactKind.EPISODE_FINAL_CUT, ArtifactKind.EPISODE_JIANYING_DRAFT}:
        return FileOwner("timeline", id=str(components[1]), episode=episode), FileRole.REFERENCED
    return FileOwner("episode", episode=episode), FileRole.REFERENCED
