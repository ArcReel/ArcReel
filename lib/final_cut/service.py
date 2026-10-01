"""成片命令：渲染前检查、渲染并登记、读取已有成片的时效；渲染任务、HTTP 与 Agent 工具共用。

渲染按「依据快照 → 临时文件 → 验收 → 原子替换并登记」进行（:mod:`lib.artifacts.rendered_artifact`）：
依据按任务开始时的指定修订（缺省为最新修订；HTTP 与 Agent 工具提交时已解析成具体修订）取快照，渲染期间剪辑时间线被改动、或显式渲染旧修订时，
成片一出来就如实判为 stale。

BGM 按片段起点混入整集音频，音量是登记时缓存的响度增益乘以片段音量，超出成片末尾的部分截断并在截断处淡出。

带旁白或烧入字幕的版本另取各视频单元的素材层（:class:`~lib.jianying_draft.placement.UnitMaterialSource`，由服务端注入），
与剪映草稿用同一份摆放：旁白从承载片段的起点整段混入，越界如实渲染；字幕跟随旁白或按源素材时间烧入。
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, replace
from pathlib import Path

from pydantic import BaseModel, ConfigDict

from lib.artifacts.artifact_currency import active_artifact_currency_resolver, read_artifact_content_digest
from lib.artifacts.artifact_manifest import ArtifactStatus, ProjectArtifactManifestAdapter
from lib.artifacts.rendered_artifact import commit_rendered_artifact, read_render_record
from lib.artifacts.version_manager import VersionManager
from lib.bgm.library import resolve_bgm_sources
from lib.edit_timeline.bgm import bgm_ids, place_bgm
from lib.edit_timeline.errors import EditTimelineError
from lib.edit_timeline.model import EditTimelineDocument, TimelineRevision
from lib.edit_timeline.readout import IssueScope, IssueSeverity, TimelineIssue, project_readout
from lib.edit_timeline.sources import EpisodeScriptUnits, load_episode_script_units, load_episode_sources
from lib.edit_timeline.store import EditTimelineStore
from lib.final_cut.basis import (
    FinalCutInputs,
    FinalCutVariant,
    SubtitleMode,
    current_video,
    final_cut_artifact_path,
    final_cut_basis,
    final_cut_key,
    output_profile_for_project,
    resolve_final_cut_inputs,
    resolve_final_cut_variant,
    video_resource_type_for,
)
from lib.final_cut.errors import FinalCutError
from lib.final_cut.ffmpeg_render import (
    DEFAULT_RENDER_DEADLINES,
    BgmInput,
    FinalCutAcceptance,
    NarrationInput,
    RenderDeadlines,
    accept_final_cut,
    render_plan_to_file,
)
from lib.final_cut.render_plan import RenderMedia, plan_render, render_clips
from lib.final_cut.subtitles import BurnedSubtitle
from lib.infra.ffmpeg import FfmpegUnavailableError, ffmpeg_executable
from lib.infra.media_probe import MediaProbeError, probe_media
from lib.infra.path_safety import PathTraversalError, safe_join
from lib.infra.subprocess_deadline import Spawner
from lib.jianying_draft.basis import DraftNarration
from lib.jianying_draft.placement import (
    DraftPlacement,
    UnitMaterials,
    UnitMaterialSource,
    UnitMaterialUnavailableError,
    place_timeline,
)
from lib.project.project_manager import ProjectManager
from lib.speech.narration_config import project_narration_delivery
from lib.speech.narration_delivery import USE_TTS


class FinalCutCheck(BaseModel):
    """渲染前检查通过：将要渲染的修订与版本，以及该版本里不阻断出片的 issues。"""

    model_config = ConfigDict(frozen=True)

    episode: int
    timeline_id: str
    revision: int
    narration: DraftNarration
    subtitles: SubtitleMode
    duration: float
    warnings: tuple[TimelineIssue, ...]

    @property
    def variant(self) -> FinalCutVariant:
        return FinalCutVariant(self.narration, self.subtitles)


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
    return issue.applies_to is IssueScope.ALL or variant.with_narration


@dataclass(frozen=True, slots=True)
class _Checked:
    document: EditTimelineDocument
    revision: TimelineRevision
    script: EpisodeScriptUnits
    check: FinalCutCheck


class FinalCutService:
    """``unit_materials`` 是带旁白或烧入字幕的版本取素材层的来源；只做检查与读取时效的调用方可以不给。"""

    def __init__(
        self,
        projects: ProjectManager,
        *,
        unit_materials: UnitMaterialSource | None = None,
        spawn: Spawner | None = None,
        deadlines: RenderDeadlines = DEFAULT_RENDER_DEADLINES,
    ) -> None:
        self._projects = projects
        self._unit_materials = unit_materials
        self._spawn = spawn
        self._deadlines = deadlines

    def _project_dir(self, project_name: str) -> Path:
        if not self._projects.project_exists(project_name):
            raise EditTimelineError("project_not_found", f"项目「{project_name}」不存在", project=project_name)
        return self._projects.get_project_path(project_name)

    async def _checked(
        self,
        project_name: str,
        timeline_id: str,
        revision: int | None,
        narration: DraftNarration | None,
        subtitles: SubtitleMode | None,
    ) -> _Checked:
        self._project_dir(project_name)
        project = await asyncio.to_thread(self._projects.load_project, project_name)
        variant = resolve_final_cut_variant(project, narration=narration, subtitles=subtitles)
        if variant.with_narration and project_narration_delivery(project) != USE_TTS:
            raise FinalCutError(
                "final_cut_narration_unavailable", "只有 TTS 配音项目可以渲染带旁白版本", narration=variant.narration
            )
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
                "剪辑时间线有阻断出片的问题："
                + "、".join(f"{issue.code}({issue.unit_id or '、'.join(issue.clip_ids)})" for issue in blocking),
                issues=[issue.model_dump(mode="json") for issue in blocking],
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
            narration=variant.narration,
            subtitles=variant.subtitles,
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
        narration: DraftNarration | None = None,
        subtitles: SubtitleMode | None = None,
    ) -> FinalCutCheck:
        """渲染前检查：剪辑时间线与修订存在、旁白版本可选、没有适用于该版本的阻断级 issue、内容可渲染、随包 ffmpeg 可用。

        省略的旁白版本与字幕方式按 :func:`~lib.final_cut.basis.resolve_final_cut_variant` 补齐，
        结果的 ``narration`` / ``subtitles`` 是实际检查的版本。
        """
        return (await self._checked(project_name, timeline_id, revision, narration, subtitles)).check

    def _snapshot(self, project_name: str, checked: _Checked) -> FinalCutInputs:
        project_dir = self._project_dir(project_name)
        adapter = ProjectArtifactManifestAdapter(project_dir)
        versions = VersionManager(project_dir)
        resource_type = video_resource_type_for(checked.script.kind)
        project = self._projects.load_project(project_name)
        referenced_bgm = bgm_ids(checked.revision.content.bgm)
        bgm_sources = resolve_bgm_sources(
            project_dir, project, referenced_bgm, lambda path: read_artifact_content_digest(adapter, path)
        )
        if missing_bgm := [bgm_id for bgm_id in referenced_bgm if bgm_id not in bgm_sources]:
            raise FinalCutError(
                "final_cut_blocked",
                "BGM 不在项目里或文件已不在：" + "、".join(missing_bgm),
                issues=[{"code": "bgm_missing", "bgm_id": bgm_id} for bgm_id in missing_bgm],
            )
        inputs = resolve_final_cut_inputs(
            document=checked.document,
            revision=checked.revision,
            variant=checked.check.variant,
            profile=output_profile_for_project(project, checked.script.kind),
            script_unit_ids={unit.unit_id for unit in checked.script.units},
            video_of=lambda unit_id: current_video(
                project_dir,
                versions,
                resource_type,
                unit_id,
                lambda path: read_artifact_content_digest(adapter, path),
            ),
            bgm_sources=bgm_sources,
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

    async def _materials(self, project_name: str, checked: _Checked) -> UnitMaterials:
        if self._unit_materials is None:
            raise RuntimeError("rendering narration or subtitles needs a unit material source")
        try:
            return await self._unit_materials(
                project_name,
                episode=checked.document.episode,
                content=checked.revision.content,
                narration=checked.check.variant.narration,
            )
        except UnitMaterialUnavailableError as exc:
            raise FinalCutError(
                "final_cut_presentation_unavailable",
                f"视频单元 {exc.unit_id} 的旁白配音或字幕取不出：{exc}",
                unit_id=exc.unit_id,
            ) from exc

    def _placement(self, checked: _Checked, materials: UnitMaterials, media: dict[str, RenderMedia]) -> DraftPlacement:
        """按渲染实测的视频时长摆放旁白与字幕，片段起点因此与渲染规划的片段边界一致。"""
        measured = {
            unit_id: replace(
                material, video_version=media[unit_id].video_version, video_duration_us=media[unit_id].duration_us
            )
            for unit_id, material in materials.materials.items()
            if unit_id in media
        }
        return place_timeline(checked.revision.content, measured)

    def _narrations(self, project_dir: Path, placement: DraftPlacement) -> tuple[NarrationInput, ...]:
        narrations: list[NarrationInput] = []
        for narration in placement.narrations:
            try:
                path = safe_join(project_dir, narration.audio_path, require_file=True)
            except (PathTraversalError, FileNotFoundError) as exc:
                raise FinalCutError(
                    "final_cut_render_failed",
                    f"剪辑片段 {narration.clip_id} 的旁白配音已不在：{narration.audio_path}",
                    clip_id=narration.clip_id,
                ) from exc
            narrations.append(NarrationInput(path=path, start_us=narration.start_us))
        return tuple(narrations)

    @staticmethod
    def _bgm(project_dir: Path, inputs: FinalCutInputs, total_us: int) -> tuple[BgmInput, ...]:
        """按成片实际时长摆放 BGM；音量是 BGM 的响度增益乘以片段音量。"""
        items: list[BgmInput] = []
        for placed in place_bgm(inputs.bgm, total_us):
            source = inputs.bgm_sources[placed.bgm_id]
            try:
                path = safe_join(project_dir, source.path, require_file=True)
            except (PathTraversalError, FileNotFoundError) as exc:
                raise FinalCutError(
                    "final_cut_render_failed",
                    f"BGM 片段 {placed.clip_id} 的 BGM 文件已不在：{source.path}",
                    clip_id=placed.clip_id,
                ) from exc
            items.append(
                BgmInput(
                    path=path,
                    start_us=placed.start_us,
                    source_in_us=placed.source_in_us,
                    duration_us=placed.duration_us,
                    volume=placed.volume * source.gain,
                    fade_in_us=placed.fade_in_us,
                    fade_out_us=placed.fade_out_us,
                )
            )
        return tuple(items)

    async def render(
        self,
        project_name: str,
        timeline_id: str,
        *,
        revision: int | None = None,
        narration: DraftNarration | None = None,
        subtitles: SubtitleMode | None = None,
    ) -> FinalCutRender:
        """把剪辑时间线的指定修订（缺省为最新修订）渲染成成片并登记；省略的选项按项目补齐，同 :meth:`check`。"""
        checked = await self._checked(project_name, timeline_id, revision, narration, subtitles)
        variant = checked.check.variant
        inputs = await asyncio.to_thread(self._snapshot, project_name, checked)
        materials = await self._materials(project_name, checked) if variant.consumes_unit_materials else None
        if materials is not None:
            inputs = replace(inputs, units=materials.bases)
        basis = final_cut_basis(inputs)
        project_dir = self._project_dir(project_name)
        media = await self._render_media(inputs)
        plan = plan_render(render_clips([item.clip for item in inputs.clips], media), inputs.profile)
        if not plan.segments:
            raise FinalCutError("final_cut_empty", "剪辑时间线没有可渲染的剪辑片段", timeline_id=inputs.timeline_id)
        narrations: tuple[NarrationInput, ...] = ()
        burned: tuple[BurnedSubtitle, ...] | None = None
        if materials is not None:
            placement = self._placement(checked, materials, media)
            if variant.with_narration:
                narrations = self._narrations(project_dir, placement)
            if variant.burns_subtitles:
                burned = tuple(
                    BurnedSubtitle(start_us=item.start_us, end_us=item.end_us, text=item.text)
                    for item in placement.subtitles
                )
        bgm = self._bgm(project_dir, inputs, plan.total_frames * 1_000_000 // plan.profile.fps)
        ffmpeg = ffmpeg_executable()

        async def render_step(output: Path, workspace: Path) -> None:
            await render_plan_to_file(
                ffmpeg,
                plan,
                output,
                workspace,
                narrations=narrations,
                bgm=bgm,
                subtitles=burned,
                deadlines=self._deadlines,
                spawn=self._spawn,
            )

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
        self,
        project_name: str,
        timeline_id: str,
        *,
        narration: DraftNarration | None = None,
        subtitles: SubtitleMode | None = None,
    ) -> FinalCutStatus:
        """一个成片产物身份的时效：current、stale（仍可下载）或 missing；省略的选项按项目补齐，同 :meth:`check`。"""

        def resolve() -> FinalCutStatus:
            project_dir = self._project_dir(project_name)
            document = EditTimelineStore(self._projects, project_name).find(timeline_id)
            project = self._projects.load_project(project_name)
            chosen = resolve_final_cut_variant(project, narration=narration, subtitles=subtitles)
            artifact_path = final_cut_artifact_path(document.episode, document.id, chosen)
            resolver = active_artifact_currency_resolver(project_dir, project)
            comparison = resolver.compare(
                final_cut_key(document.episode, document.id, chosen), artifact_path=artifact_path
            )
            record = (
                read_render_record(project_dir, artifact_path)
                if comparison.status in {ArtifactStatus.CURRENT, ArtifactStatus.STALE}
                else None
            )
            return FinalCutStatus(
                episode=document.episode,
                timeline_id=document.id,
                narration=chosen.narration,
                subtitles=chosen.subtitles,
                status=comparison.status,
                artifact_path=artifact_path,
                version=record.version if record is not None else None,
                rendered_at=record.rendered_at if record is not None else None,
            )

        return await asyncio.to_thread(resolve)


__all__ = ["FinalCutCheck", "FinalCutRender", "FinalCutService", "FinalCutStatus"]
