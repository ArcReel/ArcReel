"""Restore a chosen starting image without replacing its frozen ending guide."""

from typing import Any

from pydantic import BaseModel, ConfigDict, Field, StrictInt

from lib.artifacts.version_manager import VersionManager
from lib.i18n import DEFAULT_LOCALE
from lib.i18n import _ as translate
from lib.infra.api_errors import ApiError
from lib.infra.async_thread import run_sync_transaction
from lib.project.project_manager import is_reference_video_project
from server.media_tools.context import tool_error, tool_problem
from server.media_tools.video_versions import project_video_unit_ids
from server.services.currency.image_version_restore import restore_non_typed_version
from server.services.currency.typed_media_restore import resolve_media_file
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
