"""Editable download bundles backed by the shared presentation read model."""

from __future__ import annotations

import asyncio
import json
import shutil
import tempfile
import zipfile
from pathlib import Path
from typing import Protocol

from lib.path_safety import PathTraversalError, safe_join
from lib.project_manager import ProjectManager
from lib.speech_artifact_provenance import RenditionVariant
from server.services.presentation_read_model import (
    MaterializedPresentation,
    PresentationReadModelService,
    PresentationUnavailableError,
)


class UnitPresentationReader(Protocol):
    async def materialize_unit(
        self,
        *,
        project_name: str,
        resource_type: str,
        resource_id: str,
        variant: RenditionVariant,
        video_version: int | None = None,
        audio_version: int | None = None,
    ) -> MaterializedPresentation:
        raise NotImplementedError


class PresentationBundleService:
    """Package unchanged selected media plus editable model/subtitle files."""

    def __init__(
        self,
        project_manager: ProjectManager,
        *,
        presentation_reader: UnitPresentationReader | None = None,
    ) -> None:
        self._project_manager = project_manager
        self._reader = presentation_reader or PresentationReadModelService(project_manager)

    async def export_unit(
        self,
        *,
        project_name: str,
        resource_type: str,
        resource_id: str,
        variant: RenditionVariant,
        video_version: int | None = None,
        audio_version: int | None = None,
    ) -> Path:
        result = await self._reader.materialize_unit(
            project_name=project_name,
            resource_type=resource_type,
            resource_id=resource_id,
            variant=variant,
            video_version=video_version,
            audio_version=audio_version,
        )
        project_path = await asyncio.to_thread(self._project_manager.get_project_path, project_name)
        return await asyncio.to_thread(self._write_bundle, project_path=project_path, result=result)

    @staticmethod
    def _write_bundle(*, project_path: Path, result: MaterializedPresentation) -> Path:
        presentation = result.presentation
        video = _selected_path(project_path, presentation.video.media.artifact_path)
        narration = (
            _selected_path(project_path, presentation.narration_audio.media.artifact_path)
            if presentation.narration_audio is not None
            else None
        )
        temp_dir = Path(tempfile.mkdtemp(prefix="arcreel_presentation_"))
        bundle_path = temp_dir / "presentation.zip"
        try:
            subtitle_value = presentation.subtitle_artifact_dict()
            subtitle_webvtt = presentation.subtitles_webvtt()
            with zipfile.ZipFile(bundle_path, "w") as archive:
                archive.write(video, f"media/video{video.suffix.lower()}", compress_type=zipfile.ZIP_STORED)
                if narration is not None:
                    archive.write(
                        narration,
                        f"media/narration{narration.suffix.lower()}",
                        compress_type=zipfile.ZIP_STORED,
                    )
                archive.writestr(
                    "presentation.json",
                    json.dumps(result.to_dict(), ensure_ascii=False, indent=2) + "\n",
                )
                if subtitle_value is not None:
                    if subtitle_webvtt is None:
                        raise RuntimeError("presentation subtitle projections disagree")
                    archive.writestr(
                        "subtitles.json",
                        json.dumps(subtitle_value, ensure_ascii=False, indent=2) + "\n",
                    )
                    archive.writestr("subtitles.vtt", subtitle_webvtt)
            return bundle_path
        except BaseException:
            shutil.rmtree(temp_dir, ignore_errors=True)
            raise


def _selected_path(project_path: Path, relative_path: str) -> Path:
    try:
        return safe_join(project_path, relative_path, require_file=True)
    except (PathTraversalError, FileNotFoundError) as exc:
        raise PresentationUnavailableError("selected presentation media is unavailable") from exc


__all__ = ["PresentationBundleService", "UnitPresentationReader"]
