"""由剪辑时间线生成剪映草稿，落盘登记为产物，并按本机草稿目录与剪映版本打包下载。

导出在 ``render`` 车道上执行：:meth:`TimelineJianyingDraftService.check` 在入队前按所选旁白版本检查阻断级 issue；
任务开始时 :meth:`TimelineJianyingDraftService.prepare` 取好生成依据快照与片段摆放，得到一个 :class:`JianyingDraftJob`，
再经 :func:`~lib.artifacts.rendered_artifact.commit_rendered_artifact` 渲染到临时文件、验收、原子替换正式文件并用快照依据登记。
视频单元的画面、旁白配音与字幕草稿都取自它当前的呈现模型。
"""

from __future__ import annotations

import asyncio
import shutil
import tempfile
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from lib.artifacts.artifact_currency import active_artifact_currency_resolver
from lib.artifacts.artifact_manifest import ArtifactBasis, ArtifactKey, ArtifactStatus
from lib.artifacts.rendered_artifact import commit_rendered_artifact, read_render_record
from lib.artifacts.version_manager import VersionManager
from lib.artifacts.video_visual_provenance import resolve_video_aspect_ratio
from lib.edit_timeline import (
    EditTimelineError,
    EditTimelineReadout,
    EditTimelineService,
    IssueScope,
    IssueSeverity,
    TimelineIssue,
)
from lib.edit_timeline.model import EditTimelineContent, microseconds_to_seconds
from lib.edit_timeline.store import EditTimelineStore
from lib.infra.async_thread import run_sync_transaction
from lib.infra.path_safety import safe_join
from lib.infra.thumbnail import extract_video_frame_before
from lib.jianying_draft.archive import (
    JianyingDraftArchiveError,
    JianyingVersion,
    canvas_size,
    package_jianying_draft,
    verify_jianying_draft,
    write_jianying_draft,
)
from lib.jianying_draft.basis import (
    WITH_NARRATION,
    DraftNarration,
    DraftUnitBasis,
    build_jianying_draft_basis,
    draft_unit_ids,
    effective_unit_variant,
    jianying_draft_artifact_path,
    jianying_draft_key,
)
from lib.jianying_draft.errors import JianyingDraftError
from lib.jianying_draft.placement import DraftPlacement, UnitCue, UnitMaterial, place_timeline
from lib.jianying_draft.results import JianyingDraftCheck, JianyingDraftRender, JianyingDraftStatus
from lib.project.project_manager import ProjectManager
from lib.script.script_editor import resolve_items
from lib.speech.narration_config import project_narration_delivery
from lib.speech.narration_delivery import USE_TTS
from lib.speech.speech_composition import admit_script_unit
from server.services.presentation.presentation_read_model import (
    MaterializedPresentation,
    PresentationReadModelService,
    PresentationUnavailableError,
)

_WINDOWS_UNSAFE_NAME_CHARACTERS = str.maketrans(dict.fromkeys('<>:"/\\|?*', "_"))


def _applicable_issues(issues: tuple[TimelineIssue, ...], narration: DraftNarration) -> tuple[TimelineIssue, ...]:
    """所选旁白版本适用的 issue：只影响带旁白版本的，在不带旁白版本里不计。"""
    return tuple(issue for issue in issues if issue.applies_to is IssueScope.ALL or narration == WITH_NARRATION)


def draft_folder_name(
    project_name: str, project: Mapping[str, Any], *, episode: int, timeline_name: str, narration: DraftNarration
) -> str:
    """剪映草稿文件夹名：项目标题、集与剪辑时间线显示名；带旁白版本另加后缀，两个版本可以并存。"""
    raw_title = project.get("title")
    title = raw_title if isinstance(raw_title, str) and raw_title.strip() else project_name
    base = title if project.get("content_mode") == "ad" else f"{title}_第{episode}集"
    name = f"{base}_{timeline_name}" + ("_带旁白" if narration == WITH_NARRATION else "")
    safe = name.translate(_WINDOWS_UNSAFE_NAME_CHARACTERS).replace("..", "_").strip().rstrip(".")
    return safe or project_name


@dataclass(frozen=True, slots=True, kw_only=True)
class JianyingDraftJob:
    """一次剪映草稿渲染：开始时取好的依据快照与片段摆放。

    登记流程依次调用 :meth:`render` 与 :meth:`accept`，验收通过后原子替换 ``artifact_path`` 并用 ``basis`` 登记。
    渲染期间剪辑时间线或素材被改动，登记后的产物如实读作过期。
    """

    project_dir: Path
    episode: int
    key: ArtifactKey
    artifact_path: str
    basis: ArtifactBasis
    timeline_id: str
    revision: int
    narration: DraftNarration
    placement: DraftPlacement
    canvas: tuple[int, int]
    warnings: tuple[TimelineIssue, ...]

    async def render(self, output: Path, workspace: Path) -> None:
        """把剪映草稿产物写到 ``output``；定格静帧先抽到 ``workspace``，再随产物打包。"""
        hold_frames: dict[str, Path] = {}
        for clip in self.placement.clips:
            if clip.hold_us <= 0:
                continue
            video = safe_join(self.project_dir, clip.video_path, require_file=True)
            frame = await extract_video_frame_before(
                video, workspace / f"{clip.clip_id}.png", microseconds_to_seconds(clip.source_out_us)
            )
            if frame is None:
                raise JianyingDraftError(
                    "jianying_draft_hold_frame_unavailable",
                    f"无法取出剪辑片段 {clip.clip_id} 的出点帧，定格延长写不进剪映草稿",
                    clip_id=clip.clip_id,
                )
            hold_frames[clip.clip_id] = frame
        width, height = self.canvas
        await run_sync_transaction(
            write_jianying_draft,
            self.placement,
            project_dir=self.project_dir,
            width=width,
            height=height,
            hold_frames=hold_frames,
            with_narration_track=self.narration == WITH_NARRATION,
            output=output,
        )

    async def accept(self, output: Path) -> None:
        """验收：素材路径都是占位符且来源可解析，主视频轨时长等于剪辑时间线时长。"""
        try:
            await asyncio.to_thread(
                verify_jianying_draft,
                output,
                project_dir=self.project_dir,
                expected_duration_us=self.placement.duration_us,
            )
        except JianyingDraftArchiveError as exc:
            raise JianyingDraftError("jianying_draft_acceptance_failed", f"剪映草稿未通过验收：{exc}") from exc


def _cues(presented: MaterializedPresentation) -> tuple[UnitCue, ...]:
    return tuple(
        UnitCue(start_us=cue.start_microseconds, duration_us=cue.duration_microseconds, text=cue.text)
        for cue in presented.presentation.subtitles
    )


def _unit_material_and_basis(presented: MaterializedPresentation) -> tuple[UnitMaterial, DraftUnitBasis]:
    presentation = presented.presentation
    video = presentation.video
    narration = presentation.narration_audio
    material = UnitMaterial(
        unit_id=presentation.unit_id,
        video_path=video.media.artifact_path,
        video_version=video.media.version,
        video_duration_us=video.duration_microseconds,
        source_gain=video.gain,
        subtitles=_cues(presented),
        narration_path=narration.media.artifact_path if narration is not None else None,
        narration_duration_us=narration.duration_microseconds if narration is not None else None,
    )
    if presentation.presentation_basis is not None:
        return material, DraftUnitBasis(
            presentation.unit_id, presentation_digest=presentation.presentation_basis.digest
        )
    raw = presentation.video.media
    return material, DraftUnitBasis(presentation.unit_id, manual_upload=(raw.version, raw.content_digest))


@dataclass(frozen=True, slots=True)
class _Checked:
    readout: EditTimelineReadout
    project: Mapping[str, Any]
    check: JianyingDraftCheck


class TimelineJianyingDraftService:
    def __init__(
        self,
        projects: ProjectManager,
        *,
        presentation_reader: PresentationReadModelService | None = None,
    ) -> None:
        self._projects = projects
        self._timelines = EditTimelineService(projects)
        self._presentations = presentation_reader or PresentationReadModelService(projects)

    async def _checked(
        self, project_name: str, timeline_id: str, revision: int | None, narration: DraftNarration
    ) -> _Checked:
        readout = await self._timelines.read(project_name, timeline_id, revision=revision)
        project = await asyncio.to_thread(self._projects.load_project, project_name)
        if narration == WITH_NARRATION and project_narration_delivery(project) != USE_TTS:
            raise JianyingDraftError(
                "jianying_draft_narration_unavailable", "只有 TTS 配音项目可以导出带旁白版本", narration=narration
            )
        applicable = _applicable_issues(readout.issues, narration)
        blocking = [issue for issue in applicable if issue.severity is IssueSeverity.BLOCKING]
        if blocking:
            raise JianyingDraftError(
                "jianying_draft_blocked",
                "剪辑时间线有阻断导出的问题：" + "、".join(f"{issue.code}({issue.unit_id})" for issue in blocking),
                issues=[issue.model_dump(mode="json") for issue in blocking],
            )
        check = JianyingDraftCheck(
            episode=readout.timeline.episode,
            timeline_id=readout.timeline.id,
            revision=readout.revision,
            narration=narration,
            duration=readout.duration,
            warnings=tuple(issue for issue in applicable if issue.severity is IssueSeverity.WARNING),
        )
        return _Checked(readout=readout, project=project, check=check)

    async def check(
        self, project_name: str, timeline_id: str, *, narration: DraftNarration, revision: int | None = None
    ) -> JianyingDraftCheck:
        """导出前检查：剪辑时间线与修订存在、旁白版本可选、没有适用于该版本的阻断级 issue。"""
        return (await self._checked(project_name, timeline_id, revision, narration)).check

    async def prepare(
        self, project_name: str, timeline_id: str, *, narration: DraftNarration, revision: int | None = None
    ) -> JianyingDraftJob:
        """按指定修订（缺省为最新）检查后取依据快照与片段摆放，并物化各单元的呈现模型。"""
        checked = await self._checked(project_name, timeline_id, revision, narration)
        episode, number = checked.check.episode, checked.check.revision
        document = await asyncio.to_thread(lambda: EditTimelineStore(self._projects, project_name).find(timeline_id))
        target = document.revision(number)
        if target is None:
            raise EditTimelineError("revision_not_found", f"剪辑时间线「{timeline_id}」没有修订 {number}")
        project_dir = await asyncio.to_thread(self._projects.get_project_path, project_name)
        kind, items = await asyncio.to_thread(self._episode_items, project_name, checked.project, episode)
        resource_type = "reference_videos" if kind == "video_units" else "videos"
        materials, unit_bases = await self._present_units(
            project_name,
            project_dir=project_dir,
            content=target.content,
            kind=kind,
            items=items,
            resource_type=resource_type,
            narration=narration,
        )
        aspect_ratio = resolve_video_aspect_ratio(checked.project, resource_type)
        return JianyingDraftJob(
            project_dir=project_dir,
            episode=episode,
            key=jianying_draft_key(episode, timeline_id, narration),
            artifact_path=jianying_draft_artifact_path(episode, timeline_id, narration),
            basis=build_jianying_draft_basis(
                timeline_id=timeline_id,
                revision=target,
                narration=narration,
                aspect_ratio=aspect_ratio,
                units=unit_bases,
            ),
            timeline_id=timeline_id,
            revision=number,
            narration=narration,
            placement=place_timeline(target.content, materials),
            canvas=canvas_size(aspect_ratio),
            warnings=checked.check.warnings,
        )

    def _episode_items(
        self, project_name: str, project: Mapping[str, Any], episode: int
    ) -> tuple[str, dict[str, dict[str, Any]]]:
        script_file = next(
            (
                entry.get("script_file")
                for entry in project.get("episodes") or []
                if isinstance(entry, Mapping) and entry.get("episode") == episode
            ),
            None,
        )
        if not isinstance(script_file, str) or not script_file:
            raise EditTimelineError("episode_not_found", f"第 {episode} 集不存在或尚无脚本", episode=episode)
        script = self._projects.load_script_readonly(project_name, script_file)
        raw_items, id_field, kind = resolve_items(script)
        items = {
            str(item[id_field]): item for item in raw_items if isinstance(item, dict) and item.get(id_field) is not None
        }
        return kind, items

    async def _present_units(
        self,
        project_name: str,
        *,
        project_dir: Path,
        content: EditTimelineContent,
        kind: str,
        items: Mapping[str, dict[str, Any]],
        resource_type: str,
        narration: DraftNarration,
    ) -> tuple[dict[str, UnitMaterial], list[DraftUnitBasis]]:
        versions = VersionManager(project_dir)
        materials: dict[str, UnitMaterial] = {}
        unit_bases: list[DraftUnitBasis] = []
        for unit_id in draft_unit_ids(content, items):
            audio_version = await asyncio.to_thread(versions.get_current_version, "audio", unit_id)
            effective = effective_unit_variant(
                narration, admit_script_unit(kind, items[unit_id]).mode, has_narration_audio=audio_version > 0
            )
            try:
                presented = await self._presentations.materialize_unit(
                    project_name=project_name,
                    resource_type=resource_type,
                    resource_id=unit_id,
                    variant=effective,
                )
            except PresentationUnavailableError as exc:
                raise JianyingDraftError(
                    "jianying_draft_presentation_unavailable",
                    f"视频单元 {unit_id} 的素材无法用于剪映草稿：{exc}",
                    unit_id=unit_id,
                ) from exc
            material, unit_basis = _unit_material_and_basis(presented)
            materials[unit_id] = material
            unit_bases.append(unit_basis)
        return materials, unit_bases

    async def render(
        self, project_name: str, timeline_id: str, *, narration: DraftNarration, revision: int | None = None
    ) -> JianyingDraftRender:
        """把剪辑时间线的指定修订（缺省为最新修订）导出为剪映草稿并登记。"""
        job = await self.prepare(project_name, timeline_id, narration=narration, revision=revision)
        rendered = await commit_rendered_artifact(
            job.project_dir,
            key=job.key,
            artifact_path=job.artifact_path,
            basis=job.basis,
            render=job.render,
            accept=job.accept,
        )
        return JianyingDraftRender(
            episode=job.episode,
            timeline_id=job.timeline_id,
            revision=job.revision,
            narration=job.narration,
            artifact_path=rendered.artifact_path,
            version=rendered.record.version,
            rendered_at=rendered.record.rendered_at,
            duration=microseconds_to_seconds(job.placement.duration_us),
            warnings=job.warnings,
        )

    async def status(self, project_name: str, timeline_id: str, *, narration: DraftNarration) -> JianyingDraftStatus:
        """剪映草稿产物的时效：current、stale（已落后于剪辑时间线，仍可下载）或 missing（从未导出，或正式文件已不在）。"""
        document = await asyncio.to_thread(lambda: EditTimelineStore(self._projects, project_name).find(timeline_id))

        def resolve() -> JianyingDraftStatus:
            project_dir = self._projects.get_project_path(project_name)
            artifact_path = jianying_draft_artifact_path(document.episode, document.id, narration)
            resolver = active_artifact_currency_resolver(project_dir, self._projects.load_project(project_name))
            comparison = resolver.compare(
                jianying_draft_key(document.episode, document.id, narration), artifact_path=artifact_path
            )
            record = (
                read_render_record(project_dir, artifact_path)
                if comparison.status in {ArtifactStatus.CURRENT, ArtifactStatus.STALE}
                else None
            )
            return JianyingDraftStatus(
                episode=document.episode,
                timeline_id=document.id,
                narration=narration,
                status=comparison.status,
                artifact_path=artifact_path,
                version=record.version if record is not None else None,
                rendered_at=record.rendered_at if record is not None else None,
            )

        return await asyncio.to_thread(resolve)

    async def package_download(
        self,
        project_name: str,
        timeline_id: str,
        *,
        narration: DraftNarration,
        draft_root: str,
        jianying_version: JianyingVersion,
    ) -> tuple[Path, str]:
        """把已登记的剪映草稿代入本机草稿目录与剪映版本打包；过期的草稿照常可下载。

        返回临时目录里的 zip 与草稿文件夹名；调用方用完删除 zip 所在的临时目录。
        """
        status = await self.status(project_name, timeline_id, narration=narration)
        if status.status not in {ArtifactStatus.CURRENT, ArtifactStatus.STALE}:
            raise JianyingDraftError(
                "jianying_draft_not_exported",
                f"剪辑时间线「{timeline_id}」还没有导出过这个旁白版本的剪映草稿",
                timeline_id=timeline_id,
                narration=narration,
            )
        document = await asyncio.to_thread(lambda: EditTimelineStore(self._projects, project_name).find(timeline_id))
        project = await asyncio.to_thread(self._projects.load_project, project_name)
        project_dir = await asyncio.to_thread(self._projects.get_project_path, project_name)
        name = draft_folder_name(
            project_name, project, episode=document.episode, timeline_name=document.name, narration=narration
        )
        temp_dir = Path(tempfile.mkdtemp(prefix="arcreel_jy_download_"))
        output = temp_dir / f"{name}.zip"
        try:
            await run_sync_transaction(
                package_jianying_draft,
                project_dir / status.artifact_path,
                project_dir=project_dir,
                draft_root=draft_root,
                draft_name=name,
                jianying_version=jianying_version,
                output=output,
            )
        except JianyingDraftArchiveError as exc:
            shutil.rmtree(temp_dir, ignore_errors=True)
            raise JianyingDraftError("jianying_draft_invalid", str(exc), timeline_id=timeline_id) from exc
        except BaseException:
            shutil.rmtree(temp_dir, ignore_errors=True)
            raise
        return output, name


__all__ = [
    "JianyingDraftJob",
    "TimelineJianyingDraftService",
    "draft_folder_name",
]
