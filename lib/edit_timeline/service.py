"""剪辑时间线命令：HTTP 与 Agent 工具共用的新建、列出与读取入口。"""

from __future__ import annotations

import asyncio
import secrets
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime

from pydantic import BaseModel, ConfigDict, TypeAdapter, ValidationError

from lib.edit_timeline.errors import EditTimelineError
from lib.edit_timeline.model import (
    EditClip,
    EditTimelineContent,
    EditTimelineDocument,
    RevisionAuthor,
    TimelineName,
    TimelineRevision,
)
from lib.edit_timeline.operations import (
    AppliedBatch,
    InsertClip,
    TimelineOperation,
    apply_operations,
    default_source_volume,
    diff_content,
    sorted_clip_ids,
)
from lib.edit_timeline.readout import (
    ClipView,
    EditTimelineReadout,
    TimelineIdentity,
    TimelineIssue,
    project_readout,
)
from lib.edit_timeline.sources import (
    EpisodeScriptUnits,
    EpisodeSources,
    load_episode_script_units,
    load_episode_sources,
)
from lib.edit_timeline.store import EditTimelineStore
from lib.infra.async_thread import run_sync_transaction
from lib.project.project_manager import ProjectManager

MECHANICAL_CREATION_SUMMARY = "按当前脚本机械新建：每个视频单元整段使用、全部硬切"

REVISION_SUMMARY_MAX_LENGTH = 200

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


class ConcurrentRevision(BaseModel):
    """写入所依据的修订之后、本次写入之前由他人追加的修订。"""

    model_config = ConfigDict(frozen=True)

    number: int
    author: RevisionAuthor
    summary: str


class EditTimelineWriteResult(BaseModel):
    """一批编辑的写入结果：只含受影响片段的新状态与更新后的 issues，不含整份时间线。

    ``clips`` 按播放顺序列出本批点名或改动过的片段（含新插入的片段、跟随相邻关系恢复硬切的
    片段与旁白改挂到的片段）；``deleted_clip_ids`` 是本批删除的片段。
    """

    model_config = ConfigDict(frozen=True)

    timeline: TimelineIdentity
    revision: int
    base_revision: int
    message: str
    concurrent_revisions: tuple[ConcurrentRevision, ...]
    duration: float
    clips: tuple[ClipView, ...]
    deleted_clip_ids: tuple[str, ...]
    issues: tuple[TimelineIssue, ...]


@dataclass(frozen=True, slots=True)
class _Written:
    document: EditTimelineDocument
    base_revision: int
    previous_latest: TimelineRevision
    affected: frozenset[str]
    deleted: frozenset[str]


def _utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")


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

    async def edit(
        self,
        project_name: str,
        timeline_id: str,
        *,
        base_revision: int,
        summary: str,
        operations: Sequence[TimelineOperation],
        author: RevisionAuthor,
        agent_turn: str | None = None,
    ) -> EditTimelineWriteResult:
        """按 ``base_revision`` 解读一批操作，原子追加一个修订。

        ``base_revision`` 已不是最新修订时：本批点名或会改动的片段在那之后都没被改过就照常应用到
        最新修订上，否则以 ``revision_conflict`` 拒绝并给出冲突片段。
        """
        normalized_summary = summary.strip()
        if not normalized_summary or len(normalized_summary) > REVISION_SUMMARY_MAX_LENGTH:
            raise EditTimelineError(
                "revision_summary_invalid",
                f"改动摘要须为 1–{REVISION_SUMMARY_MAX_LENGTH} 个字符",
                allowed=f"1–{REVISION_SUMMARY_MAX_LENGTH}",
            )
        if not operations:
            raise EditTimelineError("operation_invalid", "operations 至少要有一条操作", allowed="1 条及以上")
        document = await asyncio.to_thread(lambda: self._store(project_name).find(timeline_id))
        base = self._base_revision(document, base_revision)
        script = await asyncio.to_thread(load_episode_script_units, self._projects, project_name, document.episode)
        unit_ids = {clip.unit_id for clip in (*base.content.clips, *document.latest.content.clips)}
        unit_ids |= {operation.unit_id for operation in operations if isinstance(operation, InsertClip)}
        sources = await load_episode_sources(self._projects, project_name, script, unit_ids)
        written = await run_sync_transaction(
            self._edit_sync,
            project_name,
            timeline_id,
            document.episode,
            base_revision,
            normalized_summary,
            operations,
            sources,
            author,
            agent_turn,
        )
        latest = written.document.latest
        if not {clip.unit_id for clip in latest.content.clips} <= unit_ids:
            sources = await load_episode_sources(
                self._projects, project_name, script, {clip.unit_id for clip in latest.content.clips}
            )
        readout = project_readout(written.document, latest, sources)
        concurrent = tuple(
            ConcurrentRevision(number=revision.number, author=revision.author, summary=revision.summary)
            for revision in written.document.revisions[written.base_revision : written.previous_latest.number]
        )
        message = f"剪辑时间线「{document.name}」已追加修订 {latest.number}：{normalized_summary}"
        if concurrent:
            message += (
                f"；修订 {written.base_revision} 之后已有他人写入修订 "
                f"{', '.join(str(item.number) for item in concurrent)}，本批涉及的片段未被改动，已在最新修订上应用"
            )
        return EditTimelineWriteResult(
            timeline=readout.timeline,
            revision=latest.number,
            base_revision=written.base_revision,
            message=message,
            concurrent_revisions=concurrent,
            duration=readout.duration,
            clips=tuple(clip for clip in readout.clips if clip.id in written.affected),
            deleted_clip_ids=sorted_clip_ids(written.deleted),
            issues=readout.issues,
        )

    @staticmethod
    def _base_revision(document: EditTimelineDocument, number: int) -> TimelineRevision:
        base = document.revision(number)
        if base is None:
            raise EditTimelineError(
                "revision_not_found",
                f"剪辑时间线「{document.id}」没有修订 {number}（最新修订为 {document.latest.number}）",
                timeline_id=document.id,
                revision=number,
                latest_revision=document.latest.number,
            )
        return base

    def _edit_sync(
        self,
        project_name: str,
        timeline_id: str,
        episode: int,
        base_number: int,
        summary: str,
        operations: Sequence[TimelineOperation],
        sources: EpisodeSources,
        author: RevisionAuthor,
        agent_turn: str | None,
    ) -> _Written:
        store = self._store(project_name)
        with store.locked_episode(episode):
            document = store.find(timeline_id)
            base = self._base_revision(document, base_number)
            latest = document.latest
            if base.number != latest.number:
                applied = self._check_conflicts(document, base, operations, sources)
            else:
                applied = apply_operations(latest.content, document.next_clip_number, operations, sources)
            changes = diff_content(latest.content, applied.content)
            revision = TimelineRevision(
                number=latest.number + 1,
                parent=latest.number,
                author=author,
                summary=summary,
                agent_turn=agent_turn,
                created_at=_utc_now(),
                content=applied.content,
                changed_clip_ids=sorted_clip_ids(frozenset(applied.last_operation)),
            )
            updated = EditTimelineDocument.model_validate(
                {
                    **document.model_dump(),
                    "next_clip_number": applied.next_clip_number,
                    "revisions": (*document.revisions, revision),
                }
            )
            store.write(updated)
        return _Written(
            document=updated,
            base_revision=base.number,
            previous_latest=latest,
            affected=(applied.targets | changes.added | changes.modified) - changes.removed,
            deleted=changes.removed,
        )

    @staticmethod
    def _check_conflicts(
        document: EditTimelineDocument,
        base: TimelineRevision,
        operations: Sequence[TimelineOperation],
        sources: EpisodeSources,
    ) -> AppliedBatch:
        """本批在基准修订上点名或会改动的片段，与基准修订之后他人改动过的片段不能相交。"""
        since_base: set[str] = set()
        for previous, revision in zip(
            document.revisions[base.number - 1 : -1], document.revisions[base.number :], strict=True
        ):
            since_base.update(
                revision.changed_clip_ids
                if revision.changed_clip_ids is not None
                else diff_content(previous.content, revision.content).changed
            )
        on_base = apply_operations(base.content, document.next_clip_number, operations, sources)
        touched = on_base.referenced | frozenset(on_base.last_operation)

        def reject(conflicting: set[str] | frozenset[str]) -> None:
            clip_ids = sorted_clip_ids(conflicting)
            raise EditTimelineError(
                "revision_conflict",
                f"修订 {base.number} 之后剪辑时间线已被改到修订 {document.latest.number}，"
                f"本批涉及的片段 {', '.join(clip_ids)} 期间被改动过；请重新读取后再改",
                timeline_id=document.id,
                base_revision=base.number,
                latest_revision=document.latest.number,
                conflicting_clip_ids=list(clip_ids),
            )

        if conflicting := touched & since_base:
            reject(conflicting)
        on_latest = apply_operations(document.latest.content, document.next_clip_number, operations, sources)
        if conflicting := frozenset(on_latest.last_operation) & since_base:
            reject(conflicting)
        return on_latest

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
    "REVISION_SUMMARY_MAX_LENGTH",
    "ConcurrentRevision",
    "EditTimelineService",
    "EditTimelineWriteResult",
    "TimelineSummary",
    "mechanical_content",
]
