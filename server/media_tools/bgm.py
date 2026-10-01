"""Host-neutral BGM tool: list the BGM uploaded to the project.

The handler is a thin adapter over the lib BGM command; domain errors keep their stable codes as tool problems.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict

from lib.bgm.library import BgmTrack
from lib.bgm.service import BgmError, BgmLibraryService
from server.media_tools.context import tool_error, tool_problem
from server.tool_runtime import CallerContext, ProjectScope, Services, ToolOutcome, ToolRequest


class ListBgmRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class BgmListItem(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: str
    name: str
    duration: float


def _item(track: BgmTrack) -> BgmListItem:
    return BgmListItem(id=track.id, name=track.name, duration=track.duration)


async def list_bgm(
    _request: ToolRequest[ListBgmRequest],
    scope: ProjectScope,
    _caller: CallerContext,
    services: Services,
) -> ToolOutcome[tuple[BgmListItem, ...]]:
    try:
        tracks = await BgmLibraryService(services.projects).list(scope.project_name)
    except BgmError as exc:
        return tool_problem(str(exc), code=exc.code, params=exc.params or None)
    except Exception as exc:
        return tool_error("list_bgm", exc)
    return ToolOutcome(value=tuple(_item(track) for track in tracks))


def bgm_list_summary(items: tuple[BgmListItem, ...]) -> str:
    if not items:
        return "项目里还没有 BGM；BGM 由创作者在剪辑视图的 BGM 轨上传"
    return "\n".join(f"- {item.id}「{item.name}」{item.duration} 秒" for item in items)


__all__ = ["BgmListItem", "ListBgmRequest", "bgm_list_summary", "list_bgm"]
