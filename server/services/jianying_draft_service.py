"""Serialize shared speech presentations into Jianying draft archives."""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import tempfile
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

import pyJianYingDraft as draft
from pyJianYingDraft import (
    AudioMaterial,
    AudioSegment,
    ClipSettings,
    TextBorder,
    TextSegment,
    TextShadow,
    TextStyle,
    TrackType,
    TransitionType,
    VideoMaterial,
    VideoSegment,
    trange,
)

from lib.narration_delivery import POST_PRODUCTION
from lib.path_safety import PathTraversalError, safe_join
from lib.project_manager import ProjectManager
from lib.speech_artifact_provenance import RenditionVariant
from server.services.presentation_read_model import MaterializedPresentation, PresentationReadModelService

_TRANSITION_MAP: dict[str, TransitionType] = {
    "fade": TransitionType.闪黑,
    "dissolve": TransitionType.叠化,
}


class NoCompletedSegmentsError(ValueError):
    """The episode has no selected video presentation to export."""


class EpisodePresentationReader(Protocol):
    async def materialize_episode(
        self,
        *,
        project_name: str,
        episode: int,
        variant: RenditionVariant,
    ) -> tuple[MaterializedPresentation, ...]:
        raise NotImplementedError


@dataclass(frozen=True, slots=True)
class StagedPresentation:
    """One presentation whose selected immutable media are staged locally."""

    value: MaterializedPresentation
    video_path: str
    narration_audio_path: str | None


class JianyingDraftService:
    """Write Jianying data without deriving speech ownership or timing."""

    def __init__(
        self,
        project_manager: ProjectManager,
        *,
        presentation_reader: EpisodePresentationReader | None = None,
    ) -> None:
        self.pm = project_manager
        self._presentation_reader = presentation_reader or PresentationReadModelService(project_manager)

    @staticmethod
    def _resolve_canvas_size(project: dict[str, Any], first_video_path: Path | None = None) -> tuple[int, int]:
        aspect_ratio = project.get("aspect_ratio")
        aspect = (
            aspect_ratio
            if isinstance(aspect_ratio, str)
            else aspect_ratio.get("video")
            if isinstance(aspect_ratio, dict)
            else None
        )
        if aspect is None and first_video_path is not None:
            material = VideoMaterial(str(first_video_path))
            aspect = "9:16" if material.height > material.width else "16:9"
        return (1080, 1920) if aspect == "9:16" else (1920, 1080)

    @staticmethod
    def _stage_file(source: Path, staging_dir: Path) -> Path:
        destination = staging_dir / source.name
        suffix = 1
        while destination.exists():
            destination = staging_dir / f"{source.stem}_{suffix}{source.suffix}"
            suffix += 1
        try:
            destination.hardlink_to(source)
        except OSError:
            shutil.copy2(source, destination)
        return destination

    def _generate_draft(
        self,
        *,
        draft_dir: Path,
        draft_name: str,
        presentations: tuple[StagedPresentation, ...],
        width: int,
        height: int,
    ) -> None:
        """Serialize model tracks verbatim; this layer owns only editor syntax."""

        draft_dir.parent.mkdir(parents=True, exist_ok=True)
        folder = draft.DraftFolder(str(draft_dir.parent))
        script_file = folder.create_draft(draft_name, width=width, height=height, allow_replace=True)
        script_file.add_track(TrackType.video)

        has_subtitles = any(staged.value.presentation.subtitles for staged in presentations)
        has_narration = any(staged.value.presentation.narration_audio is not None for staged in presentations)
        if has_subtitles:
            script_file.add_track(TrackType.text, "字幕")
        if has_narration:
            script_file.add_track(TrackType.audio, "旁白")

        is_portrait = height > width
        text_style = TextStyle(
            size=12.0 if is_portrait else 8.0,
            color=(1.0, 1.0, 1.0),
            align=1,
            bold=True,
            auto_wrapping=True,
            max_line_width=0.82 if is_portrait else 0.6,
        )
        text_border = TextBorder(color=(0.0, 0.0, 0.0), width=30.0)
        text_shadow = TextShadow(
            color=(0.0, 0.0, 0.0),
            alpha=0.7,
            diffuse=8.0,
            distance=3.0,
            angle=-45.0,
        )
        subtitle_position = ClipSettings(transform_y=-0.75 if is_portrait else -0.8)

        offset_microseconds = 0
        last_index = len(presentations) - 1
        for index, staged in enumerate(presentations):
            presentation = staged.value.presentation
            video_track = presentation.video
            video_material = VideoMaterial(staged.video_path)
            video_segment = VideoSegment(
                video_material,
                trange(offset_microseconds, video_track.duration_microseconds),
                source_timerange=trange(video_track.start_microseconds, video_track.duration_microseconds),
                volume=video_track.gain,
            )
            if index < last_index:
                transition = _TRANSITION_MAP.get(staged.value.transition_to_next)
                if transition is not None:
                    video_segment.add_transition(transition)
            script_file.add_segment(video_segment)

            for cue in presentation.subtitles:
                script_file.add_segment(
                    TextSegment(
                        text=cue.text,
                        timerange=trange(
                            offset_microseconds + cue.start_microseconds,
                            cue.duration_microseconds,
                        ),
                        style=text_style,
                        border=text_border,
                        shadow=text_shadow,
                        clip_settings=subtitle_position,
                    )
                )

            narration = presentation.narration_audio
            if narration is not None:
                if staged.narration_audio_path is None:
                    raise ValueError("shared presentation narration media was not staged")
                audio_material = AudioMaterial(staged.narration_audio_path)
                script_file.add_segment(
                    AudioSegment(
                        audio_material,
                        trange(
                            offset_microseconds + narration.start_microseconds,
                            narration.duration_microseconds,
                        ),
                        source_timerange=trange(0, narration.duration_microseconds),
                        volume=narration.gain,
                    ),
                    "旁白",
                )

            offset_microseconds += video_track.duration_microseconds

        script_file.save()

    @staticmethod
    def _replace_paths_in_draft(*, json_path: Path, tmp_prefix: str, target_prefix: str) -> None:
        try:
            real_path = safe_join(tempfile.gettempdir(), json_path, require_file=True)
        except (PathTraversalError, FileNotFoundError) as exc:
            raise ValueError(f"draft path is outside the temporary directory: {json_path}") from exc

        with real_path.open(encoding="utf-8") as handle:
            value = json.load(handle)

        def replace(current: Any) -> Any:
            if isinstance(current, str) and tmp_prefix in current:
                return current.replace(tmp_prefix, target_prefix)
            if isinstance(current, dict):
                return {key: replace(item) for key, item in current.items()}
            if isinstance(current, list):
                return [replace(item) for item in current]
            return current

        with real_path.open("w", encoding="utf-8") as handle:
            json.dump(replace(value), handle, ensure_ascii=False)

    async def export_episode_draft(
        self,
        project_name: str,
        episode: int,
        draft_path: str,
        *,
        variant: RenditionVariant = POST_PRODUCTION,
        use_draft_info_name: bool = True,
    ) -> Path:
        """Materialize one episode once, then serialize that exact selection."""

        project = await asyncio.to_thread(self.pm.load_project, project_name)
        project_dir = await asyncio.to_thread(self.pm.get_project_path, project_name)
        values = await self._presentation_reader.materialize_episode(
            project_name=project_name,
            episode=episode,
            variant=variant,
        )
        if not values:
            raise NoCompletedSegmentsError(f"episode {episode} has no completed video presentations")
        return await asyncio.to_thread(
            self._export_materialized,
            project_name=project_name,
            project=project,
            project_dir=project_dir,
            episode=episode,
            draft_path=draft_path,
            values=values,
            use_draft_info_name=use_draft_info_name,
        )

    def _export_materialized(
        self,
        *,
        project_name: str,
        project: dict[str, Any],
        project_dir: Path,
        episode: int,
        draft_path: str,
        values: tuple[MaterializedPresentation, ...],
        use_draft_info_name: bool,
    ) -> Path:
        first_video = self._resolve_selected_media(project_dir, values[0].presentation.video.media.artifact_path)
        width, height = self._resolve_canvas_size(project, first_video)
        draft_name = self._draft_name(project_name, project, episode)
        temp_dir = Path(tempfile.mkdtemp(prefix="arcreel_jy_"))
        try:
            staging_dir = temp_dir / "staging"
            staging_dir.mkdir()
            staged_by_source: dict[Path, Path] = {}

            def stage(relative_path: str) -> str:
                source = self._resolve_selected_media(project_dir, relative_path)
                staged = staged_by_source.get(source)
                if staged is None:
                    staged = self._stage_file(source, staging_dir)
                    staged_by_source[source] = staged
                return str(staged)

            staged_values = tuple(
                StagedPresentation(
                    value=value,
                    video_path=stage(value.presentation.video.media.artifact_path),
                    narration_audio_path=(
                        stage(value.presentation.narration_audio.media.artifact_path)
                        if value.presentation.narration_audio is not None
                        else None
                    ),
                )
                for value in values
            )
            draft_dir = temp_dir / "draft" / draft_name
            self._generate_draft(
                draft_dir=draft_dir,
                draft_name=draft_name,
                presentations=staged_values,
                width=width,
                height=height,
            )

            assets_dir = draft_dir / "assets"
            assets_dir.mkdir(exist_ok=True)
            staged_files = tuple(staging_dir.iterdir())
            for staged in staged_files:
                source = safe_join(staging_dir, staged.name, require_file=True)
                destination = safe_join(assets_dir, staged.name)
                source_root = os.path.realpath(staging_dir) + os.sep
                destination_root = os.path.realpath(assets_dir) + os.sep
                if not str(source).startswith(source_root) or not str(destination).startswith(destination_root):
                    raise ValueError(f"staged media path escaped its destination: {staged.name}")
                shutil.move(str(source), str(destination))

            content_path = draft_dir / "draft_content.json"
            self._replace_paths_in_draft(
                json_path=content_path,
                tmp_prefix=str(staging_dir),
                target_prefix=f"{draft_path}/{draft_name}/assets",
            )
            if use_draft_info_name:
                content_path.rename(draft_dir / "draft_info.json")

            zip_path = temp_dir / f"{draft_name}.zip"
            stored_suffixes = {".mp4", ".webm", ".mov", ".avi", ".mkv"}
            with zipfile.ZipFile(zip_path, "w") as archive:
                for file in draft_dir.rglob("*"):
                    if not file.is_file():
                        continue
                    archive_name = f"{draft_name}/{file.relative_to(draft_dir)}"
                    compression = zipfile.ZIP_STORED if file.suffix.lower() in stored_suffixes else zipfile.ZIP_DEFLATED
                    archive.write(file, archive_name, compress_type=compression)
            return zip_path
        except BaseException:
            shutil.rmtree(temp_dir, ignore_errors=True)
            raise

    @staticmethod
    def _resolve_selected_media(project_dir: Path, relative_path: str) -> Path:
        try:
            return safe_join(project_dir, relative_path, require_file=True)
        except PathTraversalError as exc:
            raise ValueError("shared presentation media is outside the project") from exc
        except FileNotFoundError as exc:
            raise ValueError("shared presentation media is unavailable") from exc

    @staticmethod
    def _draft_name(project_name: str, project: dict[str, Any], episode: int) -> str:
        raw_title = project.get("title")
        title = raw_title if isinstance(raw_title, str) and raw_title.strip() else project_name
        safe_title = title.replace("/", "_").replace("\\", "_").replace("..", "_")
        name = safe_title if project.get("content_mode") == "ad" else f"{safe_title}_第{episode}集"
        return name if name.replace(".", "").strip() else project_name


__all__ = [
    "EpisodePresentationReader",
    "JianyingDraftService",
    "NoCompletedSegmentsError",
    "StagedPresentation",
]
