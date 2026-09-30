"""剪辑时间线读取与新建所依据的一集素材事实：按脚本顺序的视频单元、发声归属与 current 媒体。"""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from lib.artifacts.version_manager import VersionManager
from lib.edit_timeline.errors import EditTimelineError
from lib.edit_timeline.model import MICROSECONDS_PER_SECOND, seconds_to_microseconds
from lib.project.project_manager import ProjectManager
from lib.project.resource_paths import resource_relative_path
from lib.script.script_editor import ScriptEditError, resolve_items
from lib.script.script_models import item_duration
from lib.speech.audio_utils import probe_existing_audio_duration_seconds, probe_existing_video_duration_seconds
from lib.speech.speech_composition import SpeechMode, admit_script_unit


@dataclass(frozen=True, slots=True)
class ScriptUnit:
    """脚本里的一个视频单元及其发声归属；归属判不出（混合发声、待重新规划）时为 None。"""

    unit_id: str
    speech_mode: SpeechMode | None
    scripted_duration_us: int


@dataclass(frozen=True, slots=True)
class EpisodeScriptUnits:
    episode: int
    kind: str
    units: tuple[ScriptUnit, ...]

    @property
    def video_resource_type(self) -> str:
        return "reference_videos" if self.kind == "video_units" else "videos"


@dataclass(frozen=True, slots=True)
class UnitMedia:
    """视频单元的 current 媒体：没有可用视频时 ``video_version`` 为 None；时长探测不出时为 None。"""

    video_version: int | None
    video_duration_us: int | None
    narration_duration_us: int | None


@dataclass(frozen=True, slots=True)
class EpisodeSources:
    script: EpisodeScriptUnits
    media: Mapping[str, UnitMedia]

    def unit(self, unit_id: str) -> ScriptUnit | None:
        return next((unit for unit in self.script.units if unit.unit_id == unit_id), None)


def _episode_script_file(project: Mapping[str, Any], episode: int) -> str:
    for entry in project.get("episodes") or []:
        if isinstance(entry, Mapping) and entry.get("episode") == episode:
            script_file = entry.get("script_file")
            if isinstance(script_file, str) and script_file:
                return ProjectManager.normalize_script_filename(script_file)
            break
    raise EditTimelineError("episode_not_found", f"第 {episode} 集不存在或尚无脚本", episode=episode)


def load_episode_script_units(projects: ProjectManager, project_name: str, episode: int) -> EpisodeScriptUnits:
    """按当前脚本顺序列出一集的视频单元。"""
    project = projects.load_project(project_name)
    script_file = _episode_script_file(project, episode)
    try:
        script = projects.load_script_readonly(project_name, script_file)
        items, id_field, kind = resolve_items(script)
    except FileNotFoundError as exc:
        raise EditTimelineError("episode_not_found", f"第 {episode} 集的脚本不存在", episode=episode) from exc
    except ScriptEditError as exc:
        raise EditTimelineError("script_invalid", f"第 {episode} 集的脚本无法解析：{exc}", episode=episode) from exc
    units: list[ScriptUnit] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        unit_id = item.get(id_field)
        if not isinstance(unit_id, str) or not unit_id:
            continue
        admission = admit_script_unit(kind, item, ignore_marker=True)
        units.append(
            ScriptUnit(
                unit_id=unit_id,
                speech_mode=admission.mode,
                scripted_duration_us=seconds_to_microseconds(item_duration(kind, item)),
            )
        )
    return EpisodeScriptUnits(episode=episode, kind=kind, units=tuple(units))


def _current_file(
    project_path: Path, versions: VersionManager, resource_type: str, unit_id: str
) -> tuple[int, Path] | None:
    version = versions.get_current_version(resource_type, unit_id)
    path = project_path / resource_relative_path(resource_type, unit_id)
    if version <= 0 or not path.is_file():
        return None
    return version, path


async def _duration_us(path: Path | None, *, video: bool) -> int | None:
    if path is None:
        return None
    probe = probe_existing_video_duration_seconds if video else probe_existing_audio_duration_seconds
    seconds = await probe(path)
    # 实测时长保留微秒精度，输出时再规整为秒，避免逐段舍入累积漂移。
    return round(seconds * MICROSECONDS_PER_SECOND) if seconds is not None and seconds > 0 else None


async def _unit_media(project_path: Path, versions: VersionManager, resource_type: str, unit_id: str) -> UnitMedia:
    video = await asyncio.to_thread(_current_file, project_path, versions, resource_type, unit_id)
    narration = await asyncio.to_thread(_current_file, project_path, versions, "audio", unit_id)
    video_duration, narration_duration = await asyncio.gather(
        _duration_us(video[1] if video else None, video=True),
        _duration_us(narration[1] if narration else None, video=False),
    )
    return UnitMedia(
        video_version=video[0] if video else None,
        video_duration_us=video_duration,
        narration_duration_us=narration_duration,
    )


_PROBE_CONCURRENCY = 4


async def load_episode_sources(
    projects: ProjectManager, project_name: str, script: EpisodeScriptUnits, unit_ids: set[str]
) -> EpisodeSources:
    """读取 ``unit_ids`` 中仍在脚本里的视频单元的 current 视频与旁白配音。"""
    project_path = projects.get_project_path(project_name)
    versions = VersionManager(project_path)
    wanted = [unit.unit_id for unit in script.units if unit.unit_id in unit_ids]
    limiter = asyncio.Semaphore(_PROBE_CONCURRENCY)

    async def probe(unit_id: str) -> UnitMedia:
        async with limiter:
            return await _unit_media(project_path, versions, script.video_resource_type, unit_id)

    media = await asyncio.gather(*(probe(unit_id) for unit_id in wanted))
    return EpisodeSources(script=script, media=dict(zip(wanted, media, strict=True)))


__all__ = [
    "EpisodeScriptUnits",
    "EpisodeSources",
    "ScriptUnit",
    "UnitMedia",
    "load_episode_script_units",
    "load_episode_sources",
]
