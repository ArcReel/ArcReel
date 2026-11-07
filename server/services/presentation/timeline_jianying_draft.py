"""Generate Jianying drafts from timelines, persist/register artifacts, and package downloads using local draft directories and Jianying versions.

Export uses the ``render`` lane. :meth:`TimelineJianyingDraftService.check` checks blocking issues for the selected narration
variant before enqueueing. At task start, :meth:`TimelineJianyingDraftService.prepare` snapshots basis and clip placement
into :class:`JianyingDraftJob`. :func:`~lib.artifacts.rendered_artifact.commit_rendered_artifact` renders temporary files,
accepts them, atomically replaces formal files and registers snapshot basis. Unit visuals, narration and subtitle drafts
come from current presentation models; project-registered BGM volume is loudness gain multiplied by clip volume."""

from __future__ import annotations

import asyncio
import shutil
import tempfile
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from lib.artifacts.artifact_currency import active_artifact_currency_resolver, read_artifact_content_digest
from lib.artifacts.artifact_manifest import ArtifactBasis, ArtifactKey, ArtifactStatus, ProjectArtifactManifestAdapter
from lib.artifacts.rendered_artifact import commit_rendered_artifact, read_render_record
from lib.artifacts.video_visual_provenance import resolve_video_aspect_ratio
from lib.bgm.library import BgmSource, resolve_bgm_sources
from lib.edit_timeline import (
    EditTimelineError,
    EditTimelineReadout,
    EditTimelineService,
    IssueScope,
    IssueSeverity,
    TimelineIssue,
)
from lib.edit_timeline.bgm import bgm_ids
from lib.edit_timeline.model import microseconds_to_seconds
from lib.edit_timeline.readout import bgm_missing_issues
from lib.edit_timeline.store import EditTimelineStore
from lib.episode.episode_ids import episode_file_label
from lib.i18n import _ as translate_default
from lib.infra.async_thread import run_noninterruptible_sync, run_sync_transaction
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
    build_jianying_draft_basis,
    default_draft_narration,
    jianying_draft_artifact_path,
    jianying_draft_key,
)
from lib.jianying_draft.errors import JianyingDraftError
from lib.jianying_draft.placement import DraftPlacement, UnitMaterialUnavailableError, place_timeline
from lib.jianying_draft.results import JianyingDraftCheck, JianyingDraftRender, JianyingDraftStatus
from lib.project.project_manager import ProjectManager
from lib.speech.narration_config import project_narration_delivery
from lib.speech.narration_delivery import USE_TTS
from server.services.presentation.presentation_read_model import (
    PresentationReadModelService,
)
from server.services.presentation.timeline_units import TimelineUnitMaterials, load_episode_items

_WINDOWS_UNSAFE_NAME_CHARACTERS = str.maketrans(dict.fromkeys('<>:"/\\|?*', "_"))


def _applicable_issues(issues: tuple[TimelineIssue, ...], narration: DraftNarration) -> tuple[TimelineIssue, ...]:
    """Issues applicable to the selected narration variant; narrated-only issues do not count for nonnarrated output."""
    return tuple(issue for issue in issues if issue.applies_to is IssueScope.ALL or narration == WITH_NARRATION)


def draft_folder_name(
    project_name: str,
    project: Mapping[str, Any],
    *,
    episode: int,
    timeline_name: str,
    narration: DraftNarration,
    translate: Callable[..., str] = translate_default,
) -> str:
    """Jianying draft folder name combines ``{两位播出位置}_{集名}`` and timeline display name, using project title if the episode is absent from the ledger.
    Narrated variants add a suffix so both coexist. Empty episode titles use the language selected by ``translate``."""
    base = episode_file_label(project, episode, translate)
    if base is None:
        raw_title = project.get("title")
        base = raw_title if isinstance(raw_title, str) and raw_title.strip() else project_name
    name = f"{base}_{timeline_name}" + ("_带旁白" if narration == WITH_NARRATION else "")
    safe = name.translate(_WINDOWS_UNSAFE_NAME_CHARACTERS).replace("..", "_").strip().rstrip(".")
    return safe or project_name


@dataclass(frozen=True, slots=True, kw_only=True)
class JianyingDraftJob:
    """One Jianying render with basis and clip placement snapshotted at start.

    Registration calls :meth:`render`, then :meth:`accept`, atomically replacing ``artifact_path`` and registering ``basis``
    after acceptance. Timeline/material changes during rendering correctly make the registered artifact stale."""

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
        """Write the Jianying artifact to ``output``; place freeze frames, draft directories and staged materials under ``workspace``."""
        hold_frames: dict[str, Path] = {}
        held_clips = [clip for clip in self.placement.clips if clip.hold_us > 0]
        videos = await asyncio.to_thread(
            lambda: [safe_join(self.project_dir, clip.video_path, require_file=True) for clip in held_clips]
        )
        for clip, video in zip(held_clips, videos, strict=True):
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
            workspace=workspace,
        )

    async def accept(self, output: Path) -> None:
        """Accept only placeholder material paths with resolvable sources and a main-video-track duration equal to timeline duration."""
        try:
            await asyncio.to_thread(
                verify_jianying_draft,
                output,
                project_dir=self.project_dir,
                expected_duration_us=self.placement.duration_us,
            )
        except JianyingDraftArchiveError as exc:
            raise JianyingDraftError("jianying_draft_acceptance_failed", f"剪映草稿未通过验收：{exc}") from exc


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
        self._unit_materials = TimelineUnitMaterials(projects, presentation_reader=presentation_reader)

    async def _checked(
        self, project_name: str, timeline_id: str, revision: int | None, narration: DraftNarration | None
    ) -> _Checked:
        readout = await self._timelines.read(project_name, timeline_id, revision=revision)
        project = await asyncio.to_thread(self._projects.load_project, project_name)
        narration = narration or default_draft_narration(project)
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
        if not any(clip.status != "unit_deleted" for clip in readout.clips):
            raise JianyingDraftError(
                "jianying_draft_empty", "剪辑时间线没有可导出的剪辑片段", timeline_id=readout.timeline.id
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
        self,
        project_name: str,
        timeline_id: str,
        *,
        narration: DraftNarration | None = None,
        revision: int | None = None,
    ) -> JianyingDraftCheck:
        """Pre-export checks: timeline/revision exist, narration variant is selectable, and no applicable blocking issues exist.

        Omitted ``narration`` uses the project default; result ``narration`` identifies the variant actually checked."""
        return (await self._checked(project_name, timeline_id, revision, narration)).check

    async def prepare(
        self, project_name: str, timeline_id: str, *, narration: DraftNarration, revision: int | None = None
    ) -> JianyingDraftJob:
        """Check the specified revision (latest by default), snapshot basis/clip placement, and materialize unit presentation models."""
        checked = await self._checked(project_name, timeline_id, revision, narration)
        episode, number = checked.check.episode, checked.check.revision
        document = await asyncio.to_thread(lambda: EditTimelineStore(self._projects, project_name).find(timeline_id))
        target = document.revision(number)
        if target is None:
            raise EditTimelineError("revision_not_found", f"剪辑时间线「{timeline_id}」没有修订 {number}")
        project_dir = await asyncio.to_thread(self._projects.get_project_path, project_name)
        kind, _items = await asyncio.to_thread(
            load_episode_items, self._projects, project_name, checked.project, episode
        )
        resource_type = "reference_videos" if kind == "video_units" else "videos"
        try:
            unit_materials = await self._unit_materials(
                project_name, episode=episode, content=target.content, narration=narration
            )
        except UnitMaterialUnavailableError as exc:
            raise JianyingDraftError(
                "jianying_draft_presentation_unavailable",
                f"视频单元 {exc.unit_id} 的素材无法用于剪映草稿：{exc}",
                unit_id=exc.unit_id,
            ) from exc
        aspect_ratio = resolve_video_aspect_ratio(checked.project, resource_type)
        referenced_bgm = bgm_ids(target.content.bgm)

        def _resolve_bgm() -> dict[str, BgmSource]:
            adapter = ProjectArtifactManifestAdapter(project_dir)
            return resolve_bgm_sources(
                project_dir,
                checked.project,
                referenced_bgm,
                lambda path: read_artifact_content_digest(adapter, path),
            )

        bgm_sources = await asyncio.to_thread(_resolve_bgm)
        if missing_bgm := bgm_missing_issues(target.content, bgm_sources):
            raise JianyingDraftError(
                "jianying_draft_blocked",
                "BGM 不在项目里或文件已不在："
                + "、".join(dict.fromkeys(issue.params["bgm_id"] for issue in missing_bgm)),
                issues=[issue.model_dump(mode="json") for issue in missing_bgm],
            )
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
                units=unit_materials.bases,
                bgm_sources=bgm_sources,
            ),
            timeline_id=timeline_id,
            revision=number,
            narration=narration,
            placement=place_timeline(target.content, unit_materials.materials, bgm_sources),
            canvas=canvas_size(aspect_ratio),
            warnings=checked.check.warnings,
        )

    async def render(
        self, project_name: str, timeline_id: str, *, narration: DraftNarration, revision: int | None = None
    ) -> JianyingDraftRender:
        """Export/register a Jianying draft for the specified timeline revision, latest by default."""
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

    async def status(
        self, project_name: str, timeline_id: str, *, narration: DraftNarration | None = None
    ) -> JianyingDraftStatus:
        """Jianying artifact currency: current, stale (behind the timeline but downloadable), or missing (never exported or formal file absent).

        Omitted ``narration`` uses the project default."""
        document = await asyncio.to_thread(lambda: EditTimelineStore(self._projects, project_name).find(timeline_id))

        def resolve() -> JianyingDraftStatus:
            project_dir = self._projects.get_project_path(project_name)
            project = self._projects.load_project(project_name)
            variant = narration or default_draft_narration(project)
            artifact_path = jianying_draft_artifact_path(document.episode, document.id, variant)
            resolver = active_artifact_currency_resolver(project_dir, project)
            comparison = resolver.compare(
                jianying_draft_key(document.episode, document.id, variant), artifact_path=artifact_path
            )
            record = (
                read_render_record(project_dir, artifact_path)
                if comparison.status in {ArtifactStatus.CURRENT, ArtifactStatus.STALE}
                else None
            )
            return JianyingDraftStatus(
                episode=document.episode,
                timeline_id=document.id,
                narration=variant,
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
        narration: DraftNarration | None = None,
        draft_root: str,
        jianying_version: JianyingVersion,
        translate: Callable[..., str] = translate_default,
    ) -> tuple[Path, str]:
        """Package a registered Jianying draft with the local draft directory/version; stale drafts remain downloadable.

        Omitted ``narration`` uses the project default. Return a temporary zip and draft folder name; callers delete the zip’s temporary directory after use."""
        status = await self.status(project_name, timeline_id, narration=narration)
        narration = status.narration
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
            project_name,
            project,
            episode=document.episode,
            timeline_name=document.name,
            narration=narration,
            translate=translate,
        )
        temp_dirs: list[Path] = []

        def _package() -> Path:
            temp_dir = Path(tempfile.mkdtemp(prefix="arcreel_jy_download_"))
            temp_dirs.append(temp_dir)
            output = temp_dir / f"{name}.zip"
            package_jianying_draft(
                project_dir / status.artifact_path,
                project_dir=project_dir,
                draft_root=draft_root,
                draft_name=name,
                jianying_version=jianying_version,
                output=output,
            )
            return output

        try:
            output = await run_sync_transaction(_package)
        except BaseException as exc:
            for temp_dir in temp_dirs:
                # hard-delete: temp -- temporary packaging directory
                await run_noninterruptible_sync(shutil.rmtree, temp_dir, ignore_errors=True)
            if isinstance(exc, JianyingDraftArchiveError):
                raise JianyingDraftError("jianying_draft_invalid", str(exc), timeline_id=timeline_id) from exc
            raise
        return output, name


__all__ = [
    "JianyingDraftJob",
    "TimelineJianyingDraftService",
    "draft_folder_name",
]
