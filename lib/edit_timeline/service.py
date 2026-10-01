"""剪辑时间线命令：HTTP 与 Agent 工具共用的新建、列出与读取入口。"""

from __future__ import annotations

import asyncio
import secrets
import shutil
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from itertools import pairwise

from pydantic import BaseModel, ConfigDict, TypeAdapter, ValidationError

from lib.artifacts.formal_write import project_metadata_lock
from lib.artifacts.rendered_artifact import timeline_renders_dir
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
    SetTransition,
    TimelineOperation,
    apply_operations,
    default_source_volume,
    diff_content,
    restore_changed_clip_ids,
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


class RevisionSummary(BaseModel):
    """修订历史里的一条：谁在什么时候为什么改，以及改动了哪些片段（旧修订没有记录时为 null）。"""

    model_config = ConfigDict(frozen=True)

    number: int
    parent: int | None
    author: RevisionAuthor
    summary: str
    agent_turn: str | None
    created_at: str
    clip_count: int
    changed_clip_ids: tuple[str, ...] | None
    restored_from: int | None


class RevisionHistory(BaseModel):
    """一条剪辑时间线的修订历史，按修订号从旧到新排列。"""

    model_config = ConfigDict(frozen=True)

    timeline: TimelineIdentity
    latest_revision: int
    revisions: tuple[RevisionSummary, ...]


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


def _ensure_name_free(store: EditTimelineStore, episode: int, name: str, *, excluding: str | None = None) -> None:
    """显示名在集内不重名；``excluding`` 是正在改名的时间线自己。"""
    if any(
        existing.id != excluding and existing.name.casefold() == name.casefold()
        for existing in store.list_documents(episode)
    ):
        raise EditTimelineError(
            "timeline_name_conflict", f"集（id={episode}）已有名为「{name}」的剪辑时间线", episode=episode, name=name
        )


def _successors(content: EditTimelineContent) -> dict[str, str]:
    return dict(pairwise(clip.id for clip in content.clips))


def _new_document(
    episode: int,
    name: str,
    content: EditTimelineContent,
    *,
    summary: str,
    author: RevisionAuthor,
    agent_turn: str | None,
    next_clip_number: int,
    next_bgm_number: int = 1,
) -> EditTimelineDocument:
    """以 ``content`` 为修订 1 的新剪辑时间线，ID 随机生成。"""
    now = _utc_now()
    return EditTimelineDocument(
        id=f"tl-{secrets.token_hex(4)}",
        episode=episode,
        name=name,
        created_at=now,
        next_clip_number=next_clip_number,
        next_bgm_number=next_bgm_number,
        revisions=(
            TimelineRevision(
                number=1,
                author=author,
                summary=summary,
                agent_turn=agent_turn,
                created_at=now,
                content=content,
            ),
        ),
    )


def _summarize(document: EditTimelineDocument) -> TimelineSummary:
    latest = document.latest
    return TimelineSummary(
        id=document.id,
        name=document.name,
        episode=document.episode,
        revision=latest.number,
        clip_count=len(latest.content.clips),
        created_at=document.created_at,
        updated_at=latest.created_at,
        updated_by=latest.author,
        update_summary=latest.summary,
        agent_turn=latest.agent_turn,
    )


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
            _ensure_name_free(store, episode, name)
            document = _new_document(
                episode,
                name,
                content,
                summary=MECHANICAL_CREATION_SUMMARY,
                author=author,
                agent_turn=agent_turn,
                next_clip_number=len(content.clips) + 1,
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

    def _copy_sync(
        self,
        project_name: str,
        timeline_id: str,
        revision: int | None,
        name: str,
        author: RevisionAuthor,
        agent_turn: str | None,
    ) -> EditTimelineDocument:
        store = self._store(project_name)
        episode = store.find(timeline_id).episode
        with store.locked_episode(episode):
            source = store.find(timeline_id)
            target = self._base_revision(source, revision if revision is not None else source.latest.number)
            _ensure_name_free(store, episode, name)
            document = _new_document(
                episode,
                name,
                target.content,
                summary=f"复制自「{source.name}」的修订 {target.number}",
                author=author,
                agent_turn=agent_turn,
                next_clip_number=source.next_clip_number,
                next_bgm_number=source.next_bgm_number,
            )
            store.write(document)
        return document

    async def copy(
        self,
        project_name: str,
        timeline_id: str,
        *,
        name: str,
        revision: int | None = None,
        author: RevisionAuthor,
        agent_turn: str | None = None,
    ) -> EditTimelineReadout:
        """把一条剪辑时间线的指定修订（缺省为最新修订）复制成同一集的新时间线，返回新时间线的第一个修订。

        内容原样复制，包括原声音量、截取与转场；片段编号保持不变，编号分配器一并带过去，之后新建的片段不会撞号。
        """
        normalized = _normalized_name(name)
        document = await run_sync_transaction(
            self._copy_sync, project_name, timeline_id, revision, normalized, author, agent_turn
        )
        return await self._readout(project_name, document, document.latest.number)

    def _rename_sync(self, project_name: str, timeline_id: str, name: str) -> EditTimelineDocument:
        store = self._store(project_name)
        episode = store.find(timeline_id).episode
        with store.locked_episode(episode):
            document = store.find(timeline_id)
            if document.name == name:
                return document
            _ensure_name_free(store, episode, name, excluding=timeline_id)
            renamed = document.model_copy(update={"name": name})
            store.write(renamed)
        return renamed

    async def rename(self, project_name: str, timeline_id: str, *, name: str) -> TimelineSummary:
        """改显示名。显示名不属于剪辑内容，改名不产生修订，成片与剪映草稿也不因此过期。"""
        document = await run_sync_transaction(self._rename_sync, project_name, timeline_id, _normalized_name(name))
        return _summarize(document)

    async def list_revisions(self, project_name: str, timeline_id: str) -> RevisionHistory:
        document = await asyncio.to_thread(lambda: self._store(project_name).find(timeline_id))
        return RevisionHistory(
            timeline=TimelineIdentity(id=document.id, name=document.name, episode=document.episode),
            latest_revision=document.latest.number,
            revisions=tuple(
                RevisionSummary(
                    number=revision.number,
                    parent=revision.parent,
                    author=revision.author,
                    summary=revision.summary,
                    agent_turn=revision.agent_turn,
                    created_at=revision.created_at,
                    clip_count=len(revision.content.clips),
                    changed_clip_ids=revision.changed_clip_ids,
                    restored_from=revision.restored_from,
                )
                for revision in document.revisions
            ),
        )

    def _restore_sync(
        self,
        project_name: str,
        timeline_id: str,
        episode: int,
        number: int,
        author: RevisionAuthor,
        agent_turn: str | None,
    ) -> _Written:
        store = self._store(project_name)
        with store.locked_episode(episode):
            document = store.find(timeline_id)
            target = self._base_revision(document, number)
            latest = document.latest
            if target.content == latest.content:
                raise EditTimelineError(
                    "revision_unchanged",
                    f"修订 {number} 的内容与最新修订 {latest.number} 相同，无需回滚",
                    timeline_id=timeline_id,
                    revision=number,
                    latest_revision=latest.number,
                )
            changed = restore_changed_clip_ids(latest.content, target.content)
            revision = TimelineRevision(
                number=latest.number + 1,
                parent=latest.number,
                author=author,
                summary=f"回滚到修订 {number}，撤销之后的改动；回滚前的修订仍保留在历史里",
                agent_turn=agent_turn,
                created_at=_utc_now(),
                content=target.content,
                changed_clip_ids=sorted_clip_ids(changed),
                restored_from=number,
            )
            updated = EditTimelineDocument.model_validate(
                {**document.model_dump(), "revisions": (*document.revisions, revision)}
            )
            store.write(updated)
        changes = diff_content(latest.content, target.content)
        return _Written(
            document=updated,
            base_revision=latest.number,
            previous_latest=latest,
            affected=changed - changes.removed,
            deleted=changes.removed,
        )

    async def restore(
        self,
        project_name: str,
        timeline_id: str,
        *,
        revision: int,
        author: RevisionAuthor,
        agent_turn: str | None = None,
    ) -> EditTimelineWriteResult:
        """回滚：以旧修订的内容追加一个新修订，历史不改写。

        回滚总是作用在最新修订上，不做乐观并发判定：它整段替换内容，被还原掉的修订仍在历史里，可以再回滚回去。
        """
        document = await asyncio.to_thread(lambda: self._store(project_name).find(timeline_id))
        target = self._base_revision(document, revision)
        script = await asyncio.to_thread(load_episode_script_units, self._projects, project_name, document.episode)
        unit_ids = {clip.unit_id for clip in (*target.content.clips, *document.latest.content.clips)}
        sources = await load_episode_sources(self._projects, project_name, script, unit_ids)
        written = await run_sync_transaction(
            self._restore_sync, project_name, timeline_id, document.episode, revision, author, agent_turn
        )
        message = (
            f"剪辑时间线「{document.name}」已回滚：新修订 {written.document.latest.number} 的内容取自修订 {revision}，"
            f"回滚前的最新修订 {written.previous_latest.number} 仍保留在历史里"
        )
        return await self._write_result(project_name, script, sources, unit_ids, written, message)

    def _delete_sync(self, project_name: str, timeline_id: str) -> None:
        # 产物登记依赖剪辑时间线的模型（经 jianying_draft.basis），模块顶层导入会成环。
        from lib.artifacts.artifact_registration import forget_timeline_render_artifacts

        store = self._store(project_name)
        episode = store.find(timeline_id).episode
        project_dir = self._projects.get_project_path(project_name)
        with store.locked_episode(episode):
            document = store.find(timeline_id)
            store.delete(document)
            # 时间线 ID 不复用，成片与剪映草稿的身份挂在它上面：时间线没了，登记与文件都没有归属。
            with project_metadata_lock(project_dir):
                forget_timeline_render_artifacts(project_dir, timeline_id)
                shutil.rmtree(project_dir / timeline_renders_dir(episode, timeline_id), ignore_errors=True)

    async def delete(self, project_name: str, timeline_id: str) -> None:
        """删除一条剪辑时间线及其成片与剪映草稿（登记与 ``renders/`` 下的文件）。

        调用方负责确认没有以它为对象的渲染任务仍在排队或执行。
        """
        await run_sync_transaction(self._delete_sync, project_name, timeline_id)

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
        concurrent = tuple(
            ConcurrentRevision(number=revision.number, author=revision.author, summary=revision.summary)
            for revision in written.document.revisions[written.base_revision : written.previous_latest.number]
        )
        message = f"剪辑时间线「{document.name}」已追加修订 {written.document.latest.number}：{normalized_summary}"
        if concurrent:
            message += (
                f"；修订 {written.base_revision} 之后已有他人写入修订 "
                f"{', '.join(str(item.number) for item in concurrent)}，本批涉及的片段未被改动，已在最新修订上应用"
            )
        return await self._write_result(project_name, script, sources, unit_ids, written, message, concurrent)

    async def _write_result(
        self,
        project_name: str,
        script: EpisodeScriptUnits,
        sources: EpisodeSources,
        unit_ids: set[str],
        written: _Written,
        message: str,
        concurrent: tuple[ConcurrentRevision, ...] = (),
    ) -> EditTimelineWriteResult:
        latest = written.document.latest
        if not {clip.unit_id for clip in latest.content.clips} <= unit_ids:
            sources = await load_episode_sources(
                self._projects, project_name, script, {clip.unit_id for clip in latest.content.clips}
            )
        readout = project_readout(written.document, latest, sources)
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
        """本批在基准修订上点名或会改动的片段，与基准修订之后他人改动过的片段不能相交。

        本批设置转场的片段，在基准修订与最新修订上应用本批后必须接着同一个片段：转场描述的是
        调用方在基准修订上看到的那个切点。两次预演只判断冲突、不校验转场窗口；窗口按最新修订在最后的
        完整应用中校验。
        """
        since_base: set[str] = set()
        for previous, revision in zip(
            document.revisions[base.number - 1 : -1], document.revisions[base.number :], strict=True
        ):
            since_base.update(
                revision.changed_clip_ids
                if revision.changed_clip_ids is not None
                else diff_content(previous.content, revision.content).changed
            )
        on_base = apply_operations(base.content, document.next_clip_number, operations, sources, check_windows=False)
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
        on_latest = apply_operations(
            document.latest.content, document.next_clip_number, operations, sources, check_windows=False
        )
        if conflicting := frozenset(on_latest.last_operation) & since_base:
            reject(conflicting)
        base_next, latest_next = _successors(on_base.content), _successors(on_latest.content)
        if moved_cuts := {
            operation.clip
            for operation in operations
            if isinstance(operation, SetTransition) and base_next.get(operation.clip) != latest_next.get(operation.clip)
        }:
            reject(moved_cuts)
        return apply_operations(document.latest.content, document.next_clip_number, operations, sources)

    async def list_timelines(self, project_name: str, *, episode: int | None = None) -> tuple[TimelineSummary, ...]:
        def load() -> list[EditTimelineDocument]:
            return self._store(project_name).list_documents(episode)

        return tuple(_summarize(document) for document in await asyncio.to_thread(load))

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
    "RevisionHistory",
    "RevisionSummary",
    "TimelineSummary",
    "mechanical_content",
]
