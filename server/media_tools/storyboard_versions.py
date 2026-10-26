"""Restore a chosen starting image without replacing its frozen ending guide."""

import asyncio
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, StrictInt

from lib.artifacts.version_manager import VersionManager
from lib.i18n import DEFAULT_LOCALE
from lib.i18n import _ as translate
from lib.infra.api_errors import ApiError
from lib.infra.async_thread import run_noninterruptible_sync, run_sync_transaction
from lib.infra.image_utils import normalize_storyboard_upload
from lib.infra.path_safety import safe_join
from lib.project.project_manager import is_reference_video_project
from lib.project.resource_paths import resource_relative_path
from lib.script.storyboard_sequence import find_storyboard_item, get_storyboard_items
from server.media_tools.context import ScriptFilename, tool_error, tool_problem
from server.media_tools.video_versions import project_video_unit_ids
from server.services.currency.image_version_restore import restore_non_typed_version
from server.services.currency.typed_media_restore import resolve_media_file
from server.services.currency.upload_finalize import commit_manual_storyboard_upload, stage_uploaded_bytes
from server.services.project.end_frame import EndFrameError, read_project_image
from server.tool_runtime import CallerContext, ProjectScope, Services, ToolOutcome, ToolRequest


class SelectStoryboardVersionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    shot_id: str = Field(min_length=1, description="Storyboard shot ID")
    version: StrictInt = Field(ge=1, description="Saved storyboard version number, starting at 1")


async def select_storyboard_version(
    request: ToolRequest[SelectStoryboardVersionRequest],
    scope: ProjectScope,
    _caller: CallerContext,
    services: Services,
) -> ToolOutcome[dict[str, Any]]:
    def restore() -> ToolOutcome[dict[str, Any]]:
        shot_id = request.value.shot_id
        project = services.projects.load_project(scope.project_name)
        if is_reference_video_project(project) or shot_id not in project_video_unit_ids(
            services.projects, scope.project_name, project
        ):
            return tool_problem(translate("segment_not_found", DEFAULT_LOCALE, id=shot_id), code="segment_not_found")
        project_path = services.projects.get_project_path(scope.project_name)
        versions = VersionManager(project_path)
        try:
            current_file, file_path = resolve_media_file("storyboards", shot_id, project_path)
            result = restore_non_typed_version(
                versions=versions,
                resource_type="storyboards",
                project_name=scope.project_name,
                resource_id=shot_id,
                version=request.value.version,
                current_file=current_file,
                file_path=file_path,
                project_path=project_path,
                project_manager=services.projects,
            )
        except ApiError as exc:
            params = None
            if exc.key == "version_not_found":
                params = {
                    "available_versions": [
                        v["version"] for v in versions.get_versions("storyboards", shot_id)["versions"]
                    ]
                }
            return tool_problem(translate(exc.key, DEFAULT_LOCALE, **exc.params), code=exc.key, params=params)
        return ToolOutcome(
            value={"shot_id": shot_id, "current_version": result["current_version"], "file_path": file_path}
        )

    try:
        return await run_sync_transaction(restore)
    except Exception as exc:
        return tool_error("select_storyboard_version", exc)


def select_storyboard_version_summary(value: dict[str, Any]) -> str:
    return f"Storyboard {value['shot_id']} now uses v{value['current_version']} ({value['file_path']})."


class ReuseStoryboardImageRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    script: ScriptFilename = Field(description="Script filename, for example episode_1.json")
    shot_id: str = Field(min_length=1, description="Existing storyboard shot receiving a new image version")
    source_path: str = Field(
        min_length=1, description="Existing image inside this project to reuse as the starting image"
    )


async def reuse_storyboard_image(
    request: ToolRequest[ReuseStoryboardImageRequest],
    scope: ProjectScope,
    _caller: CallerContext,
    services: Services,
) -> ToolOutcome[dict[str, Any]]:
    value = request.value
    staged: list[Path] = []

    def prepare() -> tuple[Path, str, bytes, VersionManager]:
        project = services.projects.load_project(scope.project_name)
        script = services.projects.load_script(scope.project_name, value.script)
        items, id_field, _, _, _ = get_storyboard_items(script)
        if is_reference_video_project(project) or find_storyboard_item(items, id_field, value.shot_id) is None:
            raise EndFrameError("segment_not_found", id=value.shot_id)
        project_path = services.projects.get_project_path(scope.project_name)
        relative = resource_relative_path("storyboards", value.shot_id)
        target = safe_join(project_path, relative)
        content = read_project_image(project_path, value.source_path)
        try:
            normalized = normalize_storyboard_upload(content)
        except ValueError as exc:
            raise EndFrameError("invalid_image_file") from exc
        return target, relative, normalized, VersionManager(project_path)

    try:
        target, relative, normalized, versions = await asyncio.to_thread(prepare)

        def stage() -> None:
            staged.append(stage_uploaded_bytes(normalized, target))

        await run_sync_transaction(stage)
        version = await commit_manual_storyboard_upload(
            project_name=scope.project_name,
            script_file=value.script,
            shot_id=value.shot_id,
            asset_path=relative,
            staged_image=staged[0],
            current_image=target,
            versions=versions,
            original_filename=value.source_path,
            project_manager=services.projects,
        )
        return ToolOutcome(value={"shot_id": value.shot_id, "current_version": version, "file_path": relative})
    except EndFrameError as exc:
        return tool_problem(translate(exc.key, DEFAULT_LOCALE, **exc.params), code=exc.key)
    except Exception as exc:
        return tool_error("reuse_storyboard_image", exc)
    finally:
        for path in staged:
            await run_noninterruptible_sync(path.unlink, missing_ok=True)  # hard-delete: temp -- image reuse staging
