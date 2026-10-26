"""Director access to the same durable ending-frame snapshots used by the editor."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from lib.i18n import DEFAULT_LOCALE
from lib.i18n import _ as translate
from server.media_tools.context import ScriptFilename, tool_error, tool_problem
from server.services.project.end_frame import EndFrameError, clear_end_frame, set_end_frame_from_project_image
from server.tool_runtime import CallerContext, ProjectScope, Services, ToolOutcome, ToolRequest


class SetVideoEndFrameRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    script: ScriptFilename = Field(description="Script filename, for example episode_1.json")
    shot_id: str = Field(min_length=1, description="Storyboard shot ID")
    source_path: str | None = Field(
        description="Existing project-relative image to snapshot; explicit null clears the ending frame"
    )


async def set_video_end_frame(
    request: ToolRequest[SetVideoEndFrameRequest],
    scope: ProjectScope,
    _caller: CallerContext,
    services: Services,
) -> ToolOutcome[dict[str, Any]]:
    value = request.value
    try:
        if value.source_path is None:
            await clear_end_frame(
                project_name=scope.project_name,
                script_file=value.script,
                shot_id=value.shot_id,
                manager=services.projects,
            )
            relative = None
        else:
            relative = await set_end_frame_from_project_image(
                project_name=scope.project_name,
                script_file=value.script,
                shot_id=value.shot_id,
                source_path=value.source_path,
                manager=services.projects,
            )
        return ToolOutcome(value={"script": value.script, "shot_id": value.shot_id, "end_frame_image": relative})
    except EndFrameError as exc:
        return tool_problem(translate(exc.key, DEFAULT_LOCALE, **exc.params), code=exc.key)
    except Exception as exc:
        return tool_error("set_video_end_frame", exc)


def set_video_end_frame_summary(value: dict[str, Any]) -> str:
    action = f"set to {value['end_frame_image']}" if value["end_frame_image"] else "cleared"
    return f"Ending frame for {value['shot_id']} {action}."
