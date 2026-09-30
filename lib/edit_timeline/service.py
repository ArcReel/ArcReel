"""剪辑时间线命令：HTTP 与 Agent 工具共用的新建、列出与读取入口。"""

from __future__ import annotations

import asyncio
import secrets
from datetime import UTC, datetime

from pydantic import BaseModel, ConfigDict, TypeAdapter, ValidationError

from lib.edit_timeline.errors import EditTimelineError
from lib.edit_timeline.model import (
    DEFAULT_SOURCE_VOLUME,
    VOICEOVER_SOURCE_VOLUME,
    EditClip,
    EditTimelineContent,
    EditTimelineDocument,
    RevisionAuthor,
    TimelineName,
    TimelineRevision,
)
from lib.edit_timeline.readout import EditTimelineReadout, project_readout
from lib.edit_timeline.sources import (
    EpisodeScriptUnits,
    load_episode_script_units,
    load_episode_sources,
)
from lib.edit_timeline.store import EditTimelineStore
from lib.infra.async_thread import run_sync_transaction
from lib.project.project_manager import ProjectManager
from lib.speech.speech_composition import SpeechMode

MECHANICAL_CREATION_SUMMARY = "按当前脚本机械新建：每个视频单元整段使用、全部硬切"

_TIMELINE_NAME = TypeAdapter(TimelineName)


class TimelineSummary(BaseModel):
    """列表里的一条剪辑时间线；``updated_*`` 描述最新修订。"""

    model_config = ConfigDict(frozen=True)

    id: str
    name: str
    episode: int
    revision: int
    clip_count: int
    created_at: str
    updated_at: str
    updated_by: RevisionAuthor
    update_summary: str
    agent_turn: str | None


def _utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")


def default_source_volume(speech_mode: SpeechMode | None) -> float:
    """新建剪辑片段未指定原声音量时，按视频单元的发声归属取默认值。"""
    return VOICEOVER_SOURCE_VOLUME if speech_mode is SpeechMode.NARRATOR_VOICEOVER else DEFAULT_SOURCE_VOLUME


def mechanical_content(script: EpisodeScriptUnits) -> EditTimelineContent:
    """按当前脚本顺序排列每个视频单元：整段使用、全部硬切，旁白挂在该单元的第一个片段上。"""
    return EditTimelineContent(
        clips=tuple(
            EditClip(
                id=f"c{index}",
                unit_id=unit.unit_id,
                source_volume=default_source_volume(unit.speech_mode),
                carries_narration=True,
            )
            for index, unit in enumerate(script.units, start=1)
        )
    )


def _normalized_name(name: str) -> str:
    try:
        return _TIMELINE_NAME.validate_python(name)
    except ValidationError as exc:
        raise EditTimelineError("timeline_name_invalid", "剪辑时间线显示名须为 1–40 个字符", name=name) from exc


class EditTimelineService:
    def __init__(self, projects: ProjectManager) -> None:
        self._projects = projects

    def _store(self, project_name: str) -> EditTimelineStore:
        if not self._projects.project_exists(project_name):
            raise EditTimelineError("project_not_found", f"项目「{project_name}」不存在", project=project_name)
        return EditTimelineStore(self._projects, project_name)

    def _create_from_script_sync(
        self, project_name: str, episode: int, name: str, author: RevisionAuthor, agent_turn: str | None
    ) -> EditTimelineDocument:
        store = self._store(project_name)
        script = load_episode_script_units(self._projects, project_name, episode)
        content = mechanical_content(script)
        with store.locked_episode(episode):
            if any(existing.name.casefold() == name.casefold() for existing in store.list_documents(episode)):
                raise EditTimelineError(
                    "timeline_name_conflict",
                    f"第 {episode} 集已有名为「{name}」的剪辑时间线",
                    episode=episode,
                    name=name,
                )
            now = _utc_now()
            document = EditTimelineDocument(
                id=f"tl-{secrets.token_hex(4)}",
                episode=episode,
                name=name,
                created_at=now,
                next_clip_number=len(content.clips) + 1,
                revisions=(
                    TimelineRevision(
                        number=1,
                        author=author,
                        summary=MECHANICAL_CREATION_SUMMARY,
                        agent_turn=agent_turn,
                        created_at=now,
                        content=content,
                    ),
                ),
            )
            store.write(document)
        return document

    async def create_from_script(
        self,
        project_name: str,
        *,
        episode: int,
        name: str,
        author: RevisionAuthor,
        agent_turn: str | None = None,
    ) -> EditTimelineReadout:
        """按当前脚本机械新建一条剪辑时间线，返回它的第一个修订的读取结果。"""
        normalized = _normalized_name(name)
        document = await run_sync_transaction(
            self._create_from_script_sync, project_name, episode, normalized, author, agent_turn
        )
        return await self._readout(project_name, document, document.latest.number)

    async def list_timelines(self, project_name: str, *, episode: int | None = None) -> tuple[TimelineSummary, ...]:
        def load() -> list[EditTimelineDocument]:
            return self._store(project_name).list_documents(episode)

        return tuple(
            TimelineSummary(
                id=document.id,
                name=document.name,
                episode=document.episode,
                revision=document.latest.number,
                clip_count=len(document.latest.content.clips),
                created_at=document.created_at,
                updated_at=document.latest.created_at,
                updated_by=document.latest.author,
                update_summary=document.latest.summary,
                agent_turn=document.latest.agent_turn,
            )
            for document in await asyncio.to_thread(load)
        )

    async def read(self, project_name: str, timeline_id: str, *, revision: int | None = None) -> EditTimelineReadout:
        """读取一条剪辑时间线的指定修订（缺省为最新修订）。"""
        document = await asyncio.to_thread(lambda: self._store(project_name).find(timeline_id))
        return await self._readout(project_name, document, revision if revision is not None else document.latest.number)

    async def _readout(self, project_name: str, document: EditTimelineDocument, number: int) -> EditTimelineReadout:
        target = document.revision(number)
        if target is None:
            raise EditTimelineError(
                "revision_not_found",
                f"剪辑时间线「{document.id}」没有修订 {number}（最新修订为 {document.latest.number}）",
                timeline_id=document.id,
                revision=number,
                latest_revision=document.latest.number,
            )
        script = await asyncio.to_thread(load_episode_script_units, self._projects, project_name, document.episode)
        sources = await load_episode_sources(
            self._projects, project_name, script, {clip.unit_id for clip in target.content.clips}
        )
        return project_readout(document, target, sources)


__all__ = [
    "MECHANICAL_CREATION_SUMMARY",
    "EditTimelineService",
    "TimelineSummary",
    "default_source_volume",
    "mechanical_content",
]
