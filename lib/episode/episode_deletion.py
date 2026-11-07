"""Delete episodes into project trash with content confirmation (ADR 0096, ADR 0107).

Preview :class:`EpisodeDeletionImpact`, rendering server confirmation through
:func:`render_episode_deletion_text`, displayed unchanged by Web. Creators confirm with preview ``revision``;
locked inventory differences return to confirmation without writes.

- Deleted split ranges become unsplit. Removing source-position-last splits moves continuation starts backward;
  middle deletions leave gaps never backfilled by one-click planning.
- Episode sources/formal scripts/plans/drafts/item media/version histories/grids/narration subtitles/renderings/timelines/final videos
  share one record (:func:`episode_content`), restored through :mod:`lib.episode.episode_trash`.
  IDs/maxima never recycle/decrease; tasks/calls remain historical.

Ledger/trash/artifacts/versions commit under one project lock; any failure retracts records/restores ledgers.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import logging
import os
from collections.abc import Callable, Iterable, Mapping
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

from lib.artifacts.artifact_manifest import (
    ArtifactKey,
    ArtifactManifestEntry,
    ArtifactManifestError,
    ProjectArtifactManifestAdapter,
)
from lib.artifacts.version_manager import VersionManager
from lib.episode.episode_ids import (
    episode_display_name,
    episode_ids_in_names,
    item_id_episode,
)
from lib.episode.episode_ledger import discover_episode_file_aliases, parse_positive_episode_num
from lib.episode.episode_management import EpisodeManagementError
from lib.episode.episode_paths import episode_drafts_dir, episode_source_path
from lib.episode.episode_sources import (
    SourceOrigin,
    cut_episode_placements,
    discover_sources,
    episode_source_origin,
    source_snapshot_path,
    sync_source_snapshots,
    whole_source_files,
)
from lib.infra.text_metrics import count_reading_units, reading_unit_noun
from lib.project import project_trash
from lib.script.script_review import ForeignFormalScriptError, formal_script_filename, formal_script_overwrite

if TYPE_CHECKING:
    from lib.project.project_manager import ProjectManager

logger = logging.getLogger(__name__)

#: Deletion kind; object descriptors ``{"kind": "episode", "id": 集 ID, "title": 标题}``.
EPISODE_KIND = "episode"

#: Item-media directories, filenames with IDs such as ``scene_E3S01.png`` / ``E3U1.mp4``.
_ITEM_MEDIA_DIRS = (
    "storyboards",
    "end_frames",
    "videos",
    "reference_videos",
    "reference_videos/thumbnails",
    "audio",
    "thumbnails",
)
#: Episode-directory artifacts/formal content, ``<dir>/episode_N/``.
_EPISODE_DIRS = ("subtitles", "presentations", "edit_timelines", "renders")


@dataclass(frozen=True)
class EpisodeDeletionImpact:
    """Lost episode content; ``revision`` fingerprints inventories for confirmation."""

    episode: int
    origin: str
    #: Standalone-source volume in reading units; zero for others.
    source_units: int
    has_script: bool
    has_script_plan: bool
    storyboard_count: int
    video_count: int
    narration_audio_count: int
    end_frame_count: int
    grid_count: int
    edit_timeline_count: int
    final_cut_count: int
    #: Source-adjacent split IDs; None when not placed split episodes.
    cut_before: int | None
    cut_after: int | None
    #: Split ranges placed in whole sources.
    placed: bool

    @property
    def has_products(self) -> bool:
        return (
            self.has_script
            or self.has_script_plan
            or any(
                (
                    self.storyboard_count,
                    self.video_count,
                    self.narration_audio_count,
                    self.end_frame_count,
                    self.grid_count,
                    self.edit_timeline_count,
                    self.final_cut_count,
                )
            )
        )

    @property
    def recoverable(self) -> bool:
        """Deletion loses nothing unrebuildable within projects: no artifacts or source material exclusive to this episode."""
        return self.origin != SourceOrigin.OWN.value and not self.has_products

    @property
    def revision(self) -> str:
        payload = json.dumps(asdict(self), sort_keys=True, ensure_ascii=False)
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]

    def to_dict(self) -> dict[str, Any]:
        return {**asdict(self), "recoverable": self.recoverable, "revision": self.revision}


@dataclass(frozen=True)
class EpisodeDeletionConfirmationRequired:
    """Confirmation pending, without writes."""

    impact: EpisodeDeletionImpact


@dataclass(frozen=True)
class EpisodeDeletionResult:
    impact: EpisodeDeletionImpact


class _StaleConfirmation(Exception):
    def __init__(self, impact: EpisodeDeletionImpact):
        super().__init__("episode deletion needs confirmation")
        self.impact = impact


def _ledger_entry(project: Mapping[str, Any], episode: int) -> Mapping[str, Any]:
    raw = project.get("episodes")
    for entry in raw if isinstance(raw, list) else []:
        if isinstance(entry, Mapping) and parse_positive_episode_num(entry.get("episode")) == episode:
            return entry
    raise EpisodeManagementError("episode_not_found", f"集（id={episode}）不在账本中")


def _script_path(project_dir: Path, project: Mapping[str, Any], episode: int) -> Path | None:
    try:
        path = project_dir / "scripts" / formal_script_filename(project_dir, project, episode)
    except ForeignFormalScriptError:
        return None
    return path if path.is_file() else None


def _count_files(directory: Path, *, skip_suffix: str | None = None) -> int:
    if not directory.is_dir() or directory.is_symlink():
        return 0
    return sum(
        1
        for path in directory.rglob("*")
        if path.is_file() and not path.name.startswith(".") and not (skip_suffix and path.name.endswith(skip_suffix))
    )


def _grid_records(project_dir: Path, episode: int) -> list[str]:
    """Episode-owned ``grids/`` record IDs."""
    found: list[str] = []
    for path in sorted((project_dir / "grids").glob("grid_*.json")):
        try:
            record = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if isinstance(record, dict) and parse_positive_episode_num(record.get("episode")) == episode:
            found.append(path.stem)
    return found


def episode_deletion_impact(project_dir: Path, project: Mapping[str, Any], episode: int) -> EpisodeDeletionImpact:
    """Content lost by episode deletion."""
    entry = _ledger_entry(project, episode)
    origin = episode_source_origin(entry)
    source_units = 0
    if origin is SourceOrigin.OWN:
        try:
            text = episode_source_path(project_dir, episode).read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            text = ""
        source_units = count_reading_units(text, _language(project))
    cut_before = cut_after = None
    placed = False
    if origin is SourceOrigin.WHOLE_SOURCE:
        placements = cut_episode_placements(project, discover_sources(project_dir, project))
        own = placements.get(episode)
        if own is not None:
            placed = True
            ordered = sorted(placements.values(), key=lambda p: p.position)
            index = ordered.index(own)
            cut_before = ordered[index - 1].episode if index > 0 else None
            cut_after = ordered[index + 1].episode if index + 1 < len(ordered) else None
    overwrite = formal_script_overwrite(project_dir, project, episode)
    counts = overwrite.to_dict() if overwrite is not None else {}
    drafts = episode_drafts_dir(project_dir, episode)
    return EpisodeDeletionImpact(
        episode=episode,
        origin=origin.value,
        source_units=source_units,
        has_script=_script_path(project_dir, project, episode) is not None,
        has_script_plan=_count_files(drafts) > 0,
        storyboard_count=int(counts.get("storyboard_count") or 0),
        video_count=int(counts.get("video_count") or 0),
        narration_audio_count=int(counts.get("narration_audio_count") or 0),
        end_frame_count=int(counts.get("end_frame_count") or 0),
        grid_count=len(_grid_records(project_dir, episode)),
        edit_timeline_count=_count_files(project_dir / "edit_timelines" / f"episode_{episode}"),
        final_cut_count=_count_files(project_dir / "renders" / f"episode_{episode}", skip_suffix=".render.json"),
        cut_before=cut_before,
        cut_after=cut_after,
        placed=placed,
    )


def _language(project: Mapping[str, Any]) -> str | None:
    language = project.get("source_language")
    return language if isinstance(language, str) else None


# ---------------------------------------------------------------------------
# Delete.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class EpisodeContent:
    """All episode content in projects."""

    #: Ordinary files in deletion records, project-relative POSIX.
    files: list[str]
    #: Episode version records by resource type; snapshots in ``files``.
    versions: dict[str, dict[str, Any]]
    #: Lock/hidden infrastructure files, directly deleted with episodes.
    leftovers: list[Path] = field(default_factory=list)
    #: Episode directories, removing empties after moving files.
    dirs: list[Path] = field(default_factory=list)


def episode_content(project_dir: Path, project: Mapping[str, Any], episode: int) -> EpisodeContent:
    """Episode content in ``project_dir``; current projects for deletion, migrated temporary projects for restore. Never follow symlinks."""
    files: list[Path] = []
    leftovers: list[Path] = []
    source_dir = project_dir / "source"
    files.extend(discover_episode_file_aliases(project_dir).get(episode, []))
    files.append(episode_source_path(project_dir, episode))
    for pattern in (f"_episode_{episode}.txt*.bak", f"raw/episode_{episode}.*"):
        files.extend(sorted(source_dir.glob(pattern)))
    script_path = _script_path(project_dir, project, episode)
    if script_path is not None:
        files.append(script_path)
    leftovers.append(project_dir / "scripts" / f".episode_{episode}.json.lock")
    dirs = [episode_drafts_dir(project_dir, episode)]
    for name in _EPISODE_DIRS:
        dirs.append(project_dir / name / f"episode_{episode}")
        leftovers.append(project_dir / name / f".episode_{episode}.lock")
    for directory in dirs:
        for dirpath, _dirnames, filenames in os.walk(directory):
            for filename in sorted(filenames):
                (leftovers if filename.startswith(".") else files).append(Path(dirpath) / filename)
    for name in _ITEM_MEDIA_DIRS:
        directory = project_dir / name
        if directory.is_dir() and not directory.is_symlink():
            files.extend(path for path in sorted(directory.iterdir()) if episode_ids_in_names([path.name]) == {episode})
    grids = project_dir / "grids"
    grid_ids = _grid_records(project_dir, episode)
    for grid_id in grid_ids:
        files.extend((grids / f"{grid_id}.json", grids / f"{grid_id}.png"))
        leftovers.append(grids / f".{grid_id}.json.lock")
    versions = VersionManager(project_dir)
    records = versions.resource_records(_versioned_resources(project_dir, episode, grid_ids))
    files.extend(project_dir / rel for rel in versions.snapshot_files(records))
    plain = [path for path in files if path.is_file() and not path.is_symlink()]
    return EpisodeContent(
        files=list(dict.fromkeys(path.relative_to(project_dir).as_posix() for path in plain)),
        versions=records,
        leftovers=[path for path in leftovers if path.is_file() and not path.is_symlink()],
        dirs=dirs,
    )


def episode_trash_record(project_dir: Path, episode: int) -> project_trash.TrashRecord | None:
    """Recoverable episode deletion records; at most one because IDs never reuse."""
    return next(
        (
            record
            for record in project_trash.list_records(project_dir)
            if record.object.get("kind") == EPISODE_KIND and record.object.get("id") == episode
        ),
        None,
    )


def delete_episode(
    pm: ProjectManager, project_name: str, episode: int, *, revision: str | None = None
) -> EpisodeDeletionResult | EpisodeDeletionConfirmationRequired:
    """Move episodes into trash. Missing/mismatched ``revision`` returns inventories without writes."""
    project_dir = pm.get_project_path(project_name)
    project = pm.load_project(project_name)
    if project.get("content_mode") == "ad":
        raise EpisodeManagementError("ad_episode_locked", "广告/短片项目只有一集，不能新建或删除集")
    impact = episode_deletion_impact(project_dir, project, episode)
    if revision != impact.revision:
        return EpisodeDeletionConfirmationRequired(impact=impact)

    project_file = project_dir / "project.json"
    committed: dict[str, Any] = {}

    def _mutate(p: dict[str, Any]) -> None:
        locked_impact = episode_deletion_impact(project_dir, p, episode)
        if locked_impact.revision != revision:
            raise _StaleConfirmation(locked_impact)
        # Structure snapshots need predeletion ledgers; project.json still unchanged on disk.
        committed["before"] = project_file.read_bytes()
        committed["content"] = episode_content(project_dir, p, episode)
        committed["title"] = str(_ledger_entry(p, episode).get("title") or "")
        entries = p.get("episodes")
        p["episodes"] = [
            entry
            for entry in (entries if isinstance(entries, list) else [])
            if not (isinstance(entry, Mapping) and parse_positive_episode_num(entry.get("episode")) == episode)
        ]
        try:
            snapshot = ProjectArtifactManifestAdapter(project_dir).snapshot_entries()
        except ArtifactManifestError:
            # Unreadable manifests cannot contain valid episode registrations; continue deletion.
            snapshot = {}
        committed["expected"] = {key: entry for key, entry in snapshot.items() if key.episode_number == episode}
        committed["project"] = p
        committed["impact"] = locked_impact

    def _on_commit(_project_file: Path) -> None:
        from lib.artifacts.artifact_activation import register_artifact_entries_atomically

        content: EpisodeContent = committed["content"]
        # Create trash records first, retaining episode artifact/version registrations in structure snapshots for restore.
        record = project_trash.record_deletion(
            project_dir,
            {"kind": EPISODE_KIND, "id": episode, "title": committed["title"]},
            content.files,
            snapshot=True,
            before={"project.json": committed["before"]},
        )
        try:
            sync_source_snapshots(project_dir, committed["project"], {})
            VersionManager(project_dir).detach_resources(
                (resource_type, resource_id)
                for resource_type, bucket in content.versions.items()
                for resource_id in bucket
            )
            expected: dict[ArtifactKey, ArtifactManifestEntry | None] = committed["expected"]
            if expected:
                register_artifact_entries_atomically(project_dir, dict.fromkeys(expected), expected_entries=expected)
        except BaseException:
            project_trash.withdraw(project_dir, record.id)
            raise
        for path in content.leftovers:
            with contextlib.suppress(OSError):
                path.unlink()  # hard-delete: infra -- episode locks, content already moved into project trash
        for directory in content.dirs:
            _remove_empty_dirs(directory)

    # Failures after version extraction restore versions with predeletion ledgers.
    formal_paths = [
        VersionManager(project_dir).versions_file,
        *(source_snapshot_path(project_dir, rel) for rel in whole_source_files(project)),
    ]
    try:
        pm.update_project(project_name, _mutate, on_commit=_on_commit, formal_paths=formal_paths)
    except _StaleConfirmation as exc:
        return EpisodeDeletionConfirmationRequired(impact=exc.impact)
    result_impact: EpisodeDeletionImpact = committed["impact"]
    logger.info("已删除集：项目 %s，集 ID %s，%s", project_name, episode, result_impact.to_dict())
    return EpisodeDeletionResult(impact=result_impact)


def _remove_empty_dirs(directory: Path) -> None:
    for dirpath, _dirnames, _filenames in os.walk(directory, topdown=False):
        # Keep directories containing unmoved entries such as symlinks.
        with contextlib.suppress(OSError):
            os.rmdir(dirpath)  # hard-delete: derived -- files moved into trash, empty directories only


def _versioned_resources(project_dir: Path, episode: int, grid_ids: Iterable[str]) -> list[tuple[str, str]]:
    """Episode versions with prefixed item IDs or episode grids."""
    try:
        data = json.loads((project_dir / "versions" / "versions.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    if not isinstance(data, dict):
        return []
    grids = set(grid_ids)
    return [
        (resource_type, resource_id)
        for resource_type, bucket in data.items()
        if resource_type in VersionManager.RESOURCE_TYPES and isinstance(bucket, dict)
        for resource_id in bucket
        if item_id_episode(resource_id) == episode or (resource_type == "grids" and resource_id in grids)
    ]


# ---------------------------------------------------------------------------
# Confirmation text.
# ---------------------------------------------------------------------------

#: Aggregate loss rows: count-field/wording-key pairs, omitting zero counts.
_LOSS_COUNTS: tuple[tuple[str, str], ...] = (
    ("storyboard_count", "episode_delete_loss_storyboard"),
    ("video_count", "episode_delete_loss_video"),
    ("narration_audio_count", "episode_delete_loss_narration_audio"),
    ("end_frame_count", "episode_delete_loss_end_frame"),
    ("grid_count", "episode_delete_loss_grid"),
    ("edit_timeline_count", "episode_delete_loss_edit_timeline"),
    ("final_cut_count", "episode_delete_loss_final_cut"),
)


def render_episode_loss_items(
    impact: Mapping[str, Any], project: Mapping[str, Any], translate: Callable[..., str]
) -> list[str]:
    """Loss rows for standalone sources/formal scripts/plans/artifact counts, omitting zero counts."""
    items: list[str] = []
    if impact["origin"] == SourceOrigin.OWN.value:
        unit = "words" if reading_unit_noun(_language(project)) == "词" else "chars"
        items.append(translate(f"episode_delete_loss_source_{unit}", count=impact["source_units"]))
    if impact["has_script"]:
        items.append(translate("episode_delete_loss_script"))
    if impact["has_script_plan"]:
        items.append(translate("episode_delete_loss_script_plan"))
    items.extend(translate(key, count=count) for field, key in _LOSS_COUNTS if (count := int(impact[field] or 0)))
    return items


def render_episode_deletion_text(
    impact: Mapping[str, Any] | EpisodeDeletionImpact, project: Mapping[str, Any], translate: Callable[..., str]
) -> str:
    """Confirmation from loss inventories, naming episodes by title/position; Web displays unchanged."""
    data = impact.to_dict() if isinstance(impact, EpisodeDeletionImpact) else dict(impact)

    def name(episode: int) -> str:
        return episode_display_name(project, episode, translate)

    episode = int(data["episode"])
    lines: list[str] = []
    if data["recoverable"]:
        if data["origin"] == SourceOrigin.NONE.value:
            return translate("episode_delete_empty", name=name(episode))
        lines.append(translate("episode_delete_no_products", name=name(episode)))
    else:
        lines.append(translate("episode_delete_loss", name=name(episode)))
        lines.append(translate("episode_delete_separator").join(render_episode_loss_items(data, project, translate)))
        if any(int(data[field] or 0) for field, _ in _LOSS_COUNTS[:5]):
            lines.append(translate("episode_delete_history"))
        no_source = data["origin"] == SourceOrigin.NONE.value
        lines.append(translate("episode_delete_restore_no_source" if no_source else "episode_delete_restore"))
    if data["origin"] == SourceOrigin.WHOLE_SOURCE.value and data["placed"]:
        before, after = data["cut_before"], data["cut_after"]
        if after is None:
            lines.append(translate("episode_delete_cut_tail"))
        elif before is None:
            lines.append(translate("episode_delete_cut_gap_first", after=name(after)))
        else:
            lines.append(translate("episode_delete_cut_gap_between", before=name(before), after=name(after)))
    return "\n".join(lines) if not data["recoverable"] else "".join(lines)


__all__ = [
    "EPISODE_KIND",
    "EpisodeContent",
    "EpisodeDeletionConfirmationRequired",
    "EpisodeDeletionImpact",
    "EpisodeDeletionResult",
    "delete_episode",
    "episode_content",
    "episode_deletion_impact",
    "episode_trash_record",
    "render_episode_deletion_text",
    "render_episode_loss_items",
]
