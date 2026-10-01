"""成片命令：渲染前检查、渲染并登记、读取已有成片的时效；渲染任务、HTTP 与 Agent 工具共用。

渲染按「依据快照 → 临时文件 → 验收 → 原子替换并登记」进行（:mod:`lib.artifacts.rendered_artifact`）：
依据按任务开始时的指定修订（缺省为最新修订；HTTP 与 Agent 工具提交时已解析成具体修订）取快照，渲染期间剪辑时间线被改动、或显式渲染旧修订时，
成片一出来就如实判为 stale。
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from pathlib import Path

from pydantic import BaseModel, ConfigDict

from lib.artifacts.artifact_currency import active_artifact_currency_resolver, read_artifact_content_digest
from lib.artifacts.artifact_manifest import ArtifactStatus, ProjectArtifactManifestAdapter
from lib.artifacts.rendered_artifact import commit_rendered_artifact, read_render_record
from lib.artifacts.version_manager import VersionManager
from lib.edit_timeline.errors import EditTimelineError
from lib.edit_timeline.model import EditTimelineDocument, TimelineRevision
from lib.edit_timeline.readout import IssueScope, IssueSeverity, TimelineIssue, project_readout, unrendered_effects
from lib.edit_timeline.sources import EpisodeScriptUnits, load_episode_script_units, load_episode_sources
from lib.edit_timeline.store import EditTimelineStore
from lib.final_cut.basis import (
    DEFAULT_VARIANT,
    SUPPORTED_VARIANTS,
    WITHOUT_NARRATION,
    FinalCutInputs,
    FinalCutVariant,
    current_video,
    final_cut_artifact_path,
    final_cut_basis,
    final_cut_key,
    output_profile_for_project,
    resolve_final_cut_inputs,
    video_resource_type_for,
)
from lib.final_cut.errors import FinalCutError
from lib.final_cut.ffmpeg_render import (
    DEFAULT_RENDER_DEADLINES,
    FinalCutAcceptance,
    RenderDeadlines,
    accept_final_cut,
    render_plan_to_file,
)
from lib.final_cut.render_plan import RenderMedia, plan_render, render_clips
from lib.infra.ffmpeg import FfmpegUnavailableError, ffmpeg_executable
from lib.infra.media_probe import MediaProbeError, probe_media
from lib.infra.subprocess_deadline import Spawner
from lib.project.project_manager import ProjectManager


class FinalCutCheck(BaseModel):
    """渲染前检查通过：将要渲染的修订，以及不阻断出片的 issues。"""

    model_config = ConfigDict(frozen=True)

    episode: int
    timeline_id: str
    revision: int
    duration: float
    warnings: tuple[TimelineIssue, ...]


class FinalCutRender(BaseModel):
    """一次渲染登记下来的成片。"""

    model_config = ConfigDict(frozen=True)

    episode: int
    timeline_id: str
    revision: int
    narration: str
    subtitles: str
    artifact_path: str
    version: int
    rendered_at: str
    acceptance: FinalCutAcceptance
    warnings: tuple[TimelineIssue, ...]


class FinalCutStatus(BaseModel):
    """一个成片产物身份的现状；``status`` 为 missing 时没有版本与渲染时间。"""

    model_config = ConfigDict(frozen=True)

    episode: int
    timeline_id: str
    narration: str
    subtitles: str
    status: ArtifactStatus
    artifact_path: str
    version: int | None
    rendered_at: str | None


def _applies(issue: TimelineIssue, variant: FinalCutVariant) -> bool:
    return issue.applies_to is IssueScope.ALL or variant.narration != WITHOUT_NARRATION


@dataclass(frozen=True, slots=True)
class _Checked:
    document: EditTimelineDocument
    revision: TimelineRevision
    script: EpisodeScriptUnits
    check: FinalCutCheck


class FinalCutService:
    def __init__(
        self,
        projects: ProjectManager,
        *,
        spawn: Spawner | None = None,
        deadlines: RenderDeadlines = DEFAULT_RENDER_DEADLINES,
    ) -> None:
        self._projects = projects
        self._spawn = spawn
        self._deadlines = deadlines

    def _project_dir(self, project_name: str) -> Path:
        if not self._projects.project_exists(project_name):
            raise EditTimelineError("project_not_found", f"项目「{project_name}」不存在", project=project_name)
        return self._projects.get_project_path(project_name)

    async def _checked(
        self, project_name: str, timeline_id: str, revision: int | None, variant: FinalCutVariant
    ) -> _Checked:
        if variant not in SUPPORTED_VARIANTS:
            raise FinalCutError(
                "final_cut_variant_unsupported",
                "成片目前只能渲染不带旁白、不烧入字幕的版本",
                narration=variant.narration,
                subtitles=variant.subtitles,
            )
        self._project_dir(project_name)
        document = await asyncio.to_thread(lambda: EditTimelineStore(self._projects, project_name).find(timeline_id))
        number = revision if revision is not None else document.latest.number
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
        readout = project_readout(document, target, sources)
        applicable = [issue for issue in readout.issues if _applies(issue, variant)]
        blocking = [issue for issue in applicable if issue.severity is IssueSeverity.BLOCKING]
        if blocking:
            raise FinalCutError(
                "final_cut_blocked",
                "剪辑时间线有阻断出片的问题：" + "、".join(f"{issue.code}({issue.unit_id})" for issue in blocking),
                issues=[issue.model_dump(mode="json") for issue in blocking],
            )
        transitions, bgm_ids = unrendered_effects(readout)
        if transitions or bgm_ids:
            raise FinalCutError(
                "final_cut_content_unsupported",
                "成片目前只能渲染硬切、不带 BGM 的剪辑时间线",
                clip_ids=transitions,
                bgm_ids=bgm_ids,
            )
        if not any(clip.status != "unit_deleted" for clip in readout.clips):
            raise FinalCutError("final_cut_empty", "剪辑时间线没有可渲染的剪辑片段", timeline_id=document.id)
        try:
            await asyncio.to_thread(ffmpeg_executable)
        except FfmpegUnavailableError as exc:
            raise FinalCutError("final_cut_ffmpeg_unavailable", f"随包 ffmpeg 不可用：{exc}") from exc
        check = FinalCutCheck(
            episode=document.episode,
            timeline_id=document.id,
            revision=target.number,
            duration=readout.duration,
            warnings=tuple(issue for issue in applicable if issue.severity is IssueSeverity.WARNING),
        )
        return _Checked(document=document, revision=target, script=script, check=check)

    async def check(
        self,
        project_name: str,
        timeline_id: str,
        *,
        revision: int | None = None,
        variant: FinalCutVariant = DEFAULT_VARIANT,
    ) -> FinalCutCheck:
        """渲染前检查：剪辑时间线与修订存在、没有适用于该版本的阻断级 issue、内容可渲染、随包 ffmpeg 可用。"""
        return (await self._checked(project_name, timeline_id, revision, variant)).check

    def _snapshot(self, project_name: str, checked: _Checked, variant: FinalCutVariant) -> FinalCutInputs:
        project_dir = self._project_dir(project_name)
        adapter = ProjectArtifactManifestAdapter(project_dir)
        versions = VersionManager(project_dir)
        resource_type = video_resource_type_for(checked.script.kind)
        inputs = resolve_final_cut_inputs(
            document=checked.document,
            revision=checked.revision,
            variant=variant,
            profile=output_profile_for_project(self._projects.load_project(project_name), checked.script.kind),
            script_unit_ids={unit.unit_id for unit in checked.script.units},
            video_of=lambda unit_id: current_video(
                project_dir,
                versions,
                resource_type,
                unit_id,
                lambda path: read_artifact_content_digest(adapter, path),
            ),
        )
        if inputs.missing_video_units:
            raise FinalCutError(
                "final_cut_blocked",
                "视频单元还没有可用视频：" + "、".join(inputs.missing_video_units),
                issues=[{"code": "video_missing", "unit_id": unit_id} for unit_id in inputs.missing_video_units],
            )
        return inputs

    async def _render_media(self, inputs: FinalCutInputs) -> dict[str, RenderMedia]:
        media: dict[str, RenderMedia] = {}
        for item in inputs.clips:
            video = item.video
            if video.unit_id in media:
                continue
            path = video.snapshot
            try:
                probe = await probe_media(path, spawn=self._spawn)
            except MediaProbeError as exc:
                raise FinalCutError(
                    "final_cut_render_failed", f"视频单元 {video.unit_id} 的视频无法探测：{exc}", unit_id=video.unit_id
                ) from exc
            stream = probe.first_stream("video")
            if stream is None or not stream.duration_seconds:
                raise FinalCutError(
                    "final_cut_render_failed", f"视频单元 {video.unit_id} 的视频没有可用画面", unit_id=video.unit_id
                )
            media[video.unit_id] = RenderMedia(
                path=path,
                video_version=video.version,
                duration_us=round(stream.duration_seconds * 1_000_000),
                has_audio=video.provider_audio and probe.first_stream("audio") is not None,
            )
        return media

    async def render(
        self,
        project_name: str,
        timeline_id: str,
        *,
        revision: int | None = None,
        variant: FinalCutVariant = DEFAULT_VARIANT,
    ) -> FinalCutRender:
        """把剪辑时间线的指定修订（缺省为最新修订）渲染成成片并登记。"""
        checked = await self._checked(project_name, timeline_id, revision, variant)
        inputs = await asyncio.to_thread(self._snapshot, project_name, checked, variant)
        basis = final_cut_basis(inputs)
        project_dir = self._project_dir(project_name)
        media = await self._render_media(inputs)
        plan = plan_render(render_clips([item.clip for item in inputs.clips], media), inputs.profile)
        if not plan.segments:
            raise FinalCutError("final_cut_empty", "剪辑时间线没有可渲染的剪辑片段", timeline_id=inputs.timeline_id)
        ffmpeg = ffmpeg_executable()

        async def render_step(output: Path, workspace: Path) -> None:
            await render_plan_to_file(ffmpeg, plan, output, workspace, deadlines=self._deadlines, spawn=self._spawn)

        async def accept_step(output: Path) -> FinalCutAcceptance:
            return await accept_final_cut(output, plan, spawn=self._spawn)

        rendered = await commit_rendered_artifact(
            project_dir,
            key=final_cut_key(inputs.episode, inputs.timeline_id, variant),
            artifact_path=final_cut_artifact_path(inputs.episode, inputs.timeline_id, variant),
            basis=basis,
            render=render_step,
            accept=accept_step,
        )
        return FinalCutRender(
            episode=inputs.episode,
            timeline_id=inputs.timeline_id,
            revision=inputs.revision,
            narration=variant.narration,
            subtitles=variant.subtitles,
            artifact_path=rendered.artifact_path,
            version=rendered.record.version,
            rendered_at=rendered.record.rendered_at,
            acceptance=rendered.acceptance,
            warnings=checked.check.warnings,
        )

    async def status(
        self, project_name: str, timeline_id: str, *, variant: FinalCutVariant = DEFAULT_VARIANT
    ) -> FinalCutStatus:
        """一个成片产物身份的时效：current、stale（仍可下载）或 missing。"""

        def resolve() -> FinalCutStatus:
            project_dir = self._project_dir(project_name)
            document = EditTimelineStore(self._projects, project_name).find(timeline_id)
            artifact_path = final_cut_artifact_path(document.episode, document.id, variant)
            resolver = active_artifact_currency_resolver(project_dir, self._projects.load_project(project_name))
            comparison = resolver.compare(
                final_cut_key(document.episode, document.id, variant), artifact_path=artifact_path
            )
            record = (
                read_render_record(project_dir, artifact_path)
                if comparison.status in {ArtifactStatus.CURRENT, ArtifactStatus.STALE}
                else None
            )
            return FinalCutStatus(
                episode=document.episode,
                timeline_id=document.id,
                narration=variant.narration,
                subtitles=variant.subtitles,
                status=comparison.status,
                artifact_path=artifact_path,
                version=record.version if record is not None else None,
                rendered_at=record.rendered_at if record is not None else None,
            )

        return await asyncio.to_thread(resolve)


__all__ = ["FinalCutCheck", "FinalCutRender", "FinalCutService", "FinalCutStatus"]
