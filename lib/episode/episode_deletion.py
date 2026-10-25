"""删除一集：移进项目回收站，按删除内容确认（ADR 0096、ADR 0107）。

删除前先预览这一集要删除的内容（:class:`EpisodeDeletionImpact`），服务端成文确认文本
（:func:`render_episode_deletion_text`），Web 确认框只呈现这份文本。创作者确认后带上预览的 ``revision`` 重新调用；
锁内复核出的清单与预览不一致时退回确认，不写入。

- 切出集删除后，它的原文范围变回未切分的原文：删的是按源文位置最后一个切出集时，接续规划的起点随之回退；
  删的是中间一集时留下空段，一键规划不回填空段。
- 集原文、正式脚本、脚本规划与草稿、条目媒体与版本历史、宫格联合图、配音字幕呈现、剪辑时间线与成片
  一起成为一条删除记录（:func:`episode_content`），恢复见 :mod:`lib.episode.episode_trash`。
  集 ID 不回收，历史最高号不回退；任务与调用记录作为历史保留。

账本、删除记录、产物清单与版本历史在同一把项目锁内提交，任一步失败都撤回删除记录、账本回到删除前。
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

#: 删除记录的对象类型；对象描述 ``{"kind": "episode", "id": 集 ID, "title": 标题}``。
EPISODE_KIND = "episode"

#: 条目媒体所在目录：文件名带条目 ID（``scene_E3S01.png``、``E3U1.mp4``）。
_ITEM_MEDIA_DIRS = (
    "storyboards",
    "end_frames",
    "videos",
    "reference_videos",
    "reference_videos/thumbnails",
    "audio",
    "thumbnails",
)
#: 按集分目录的产物与正式内容：``<dir>/episode_N/``。
_EPISODE_DIRS = ("subtitles", "presentations", "edit_timelines", "renders")


@dataclass(frozen=True)
class EpisodeDeletionImpact:
    """删除一集会丢失的内容。``revision`` 是这份清单的指纹，确认时回传。"""

    episode: int
    origin: str
    #: 自带原文的集原文体量（阅读单位）；其他来源为 0。
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
    #: 切出集按源文位置前后相邻的切出集（集 ID）；不是落位的切出集时都为 None。
    cut_before: int | None
    cut_after: int | None
    #: 切出集落在整本源文里。
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
        """删除不丢失任何无法在项目内重建的内容：没有产物，原文也不是只存在于这一集里。"""
        return self.origin != SourceOrigin.OWN.value and not self.has_products

    @property
    def revision(self) -> str:
        payload = json.dumps(asdict(self), sort_keys=True, ensure_ascii=False)
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]

    def to_dict(self) -> dict[str, Any]:
        return {**asdict(self), "recoverable": self.recoverable, "revision": self.revision}


@dataclass(frozen=True)
class EpisodeDeletionConfirmationRequired:
    """等待确认；返回本对象时没有发生任何写入。"""

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
    """``grids/`` 里属于这一集的宫格记录 ID。"""
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
    """删除这一集会丢失的内容。"""
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
# 删除
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class EpisodeContent:
    """一集在项目里的全部内容。"""

    #: 进删除记录的普通文件（项目内相对 POSIX 路径）。
    files: list[str]
    #: 这一集资源的版本记录（按资源类型分桶）；快照文件在 ``files`` 里。
    versions: dict[str, dict[str, Any]]
    #: 锁与隐藏文件：基础设施，删集时直接删除。
    leftovers: list[Path] = field(default_factory=list)
    #: 按集分的目录：文件移走后清掉空目录。
    dirs: list[Path] = field(default_factory=list)


def episode_content(project_dir: Path, project: Mapping[str, Any], episode: int) -> EpisodeContent:
    """``project_dir`` 里属于这一集的内容；删集时取当前项目，恢复时取迁移后的临时项目。不跟随符号链接。"""
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
    """这一集的删除记录：集已删除、还能随集恢复时有；集 ID 不复用，至多一条。"""
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
    """删除一集，移进项目回收站。``revision`` 与当前丢失清单的指纹不符（含未传）时只返回清单，不写入。"""
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
        # 结构快照要删除前的账本：此刻盘上的 project.json 还没改写
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
            # 清单读不出时不可能有本集的有效登记，删除照常进行
            snapshot = {}
        committed["expected"] = {key: entry for key, entry in snapshot.items() if key.episode_number == episode}
        committed["project"] = p
        committed["impact"] = locked_impact

    def _on_commit(_project_file: Path) -> None:
        from lib.artifacts.artifact_activation import register_artifact_entries_atomically

        content: EpisodeContent = committed["content"]
        # 删除记录先建：结构快照里的产物清单与版本历史还带着这一集的登记，恢复时从中取回
        record = (
            project_trash.record_deletion(
                project_dir,
                {"kind": EPISODE_KIND, "id": episode, "title": committed["title"]},
                content.files,
                snapshot=True,
                before={"project.json": committed["before"]},
            )
            if content.files
            else None
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
            if record is not None:
                project_trash.withdraw(project_dir, record.id)
            raise
        for path in content.leftovers:
            with contextlib.suppress(OSError):
                path.unlink()  # hard-delete: infra -- 这一集的锁文件，内容都已移进项目回收站
        for directory in content.dirs:
            _remove_empty_dirs(directory)

    formal_paths = [source_snapshot_path(project_dir, rel) for rel in whole_source_files(project)]
    try:
        pm.update_project(project_name, _mutate, on_commit=_on_commit, formal_paths=formal_paths)
    except _StaleConfirmation as exc:
        return EpisodeDeletionConfirmationRequired(impact=exc.impact)
    result_impact: EpisodeDeletionImpact = committed["impact"]
    logger.info("已删除集：项目 %s，集 ID %s，%s", project_name, episode, result_impact.to_dict())
    return EpisodeDeletionResult(impact=result_impact)


def _remove_empty_dirs(directory: Path) -> None:
    for dirpath, _dirnames, _filenames in os.walk(directory, topdown=False):
        # 还有没移走的条目（如符号链接）时目录留着
        with contextlib.suppress(OSError):
            os.rmdir(dirpath)  # hard-delete: derived -- 文件都已移进项目回收站，只剩空目录


def _versioned_resources(project_dir: Path, episode: int, grid_ids: Iterable[str]) -> list[tuple[str, str]]:
    """版本历史里属于这一集的资源：条目 ID 带这一集的前缀，或是这一集的宫格。"""
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
# 确认文本
# ---------------------------------------------------------------------------

#: 丢失清单的汇总项：（计数字段，文案 key）。计数为 0 的项不出现。
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
    """丢失清单的逐项文本：自带原文、正式脚本、脚本规划与各类产物的计数；计数为 0 的项不出现。"""
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
    """把丢失清单渲染成确认文本：集以标题或播出位置指称。Web 确认框只呈现这份文本。"""
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
