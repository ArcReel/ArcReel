"""Host-neutral tool that switches a video unit's current video version.

The switch is the same service the Web version restore uses; the tool only resolves the unit,
maps the service's domain errors to problems, and reports which version is now current.
"""

from __future__ import annotations

import logging
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, StrictInt

from lib.artifacts.version_manager import VersionManager
from lib.i18n import DEFAULT_LOCALE
from lib.i18n import _ as translate
from lib.infra.api_errors import ApiError
from lib.project.project_manager import ProjectManager, is_reference_video_project
from lib.script.script_editor import ScriptEditError, resolve_items
from server.media_tools.context import tool_error, tool_problem
from server.services.currency.typed_media_restore import restore_typed_media_resource
from server.tool_runtime import CallerContext, ProjectScope, Services, ToolOutcome, ToolRequest

logger = logging.getLogger(__name__)

_OPERATION = "select_video_version"


class SelectVideoVersionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    unit_id: str = Field(
        min_length=1,
        description="视频单元 ID：分镜图生视频为分镜 ID（如 E1S01），参考生视频为 unit_id（如 E1U1）",
    )
    version: StrictInt = Field(ge=1, description="要设为 current 的视频版本号（从 1 起）")


def _unit_exists(projects: ProjectManager, project_name: str, project: dict[str, Any], unit_id: str) -> bool:
    kind = "video_units" if is_reference_video_project(project) else None
    for script_name in sorted(projects.list_scripts(project_name)):
        try:
            script = projects.load_script_readonly(project_name, script_name)
            items, id_field, _kind = resolve_items(script, kind=kind)
        except (ScriptEditError, ValueError, OSError):
            # 读不出的剧本无法证明单元存在，跳过；其余剧本照常判定
            logger.warning("视频版本切换查找单元时跳过无法读取的剧本: %s", script_name, exc_info=True)
            continue
        if any(isinstance(item, dict) and str(item.get(id_field)) == unit_id for item in items):
            return True
    return False


def _api_problem(exc: ApiError, *, unit_id: str, versions: VersionManager, resource_type: str) -> ToolOutcome[Any]:
    """服务的领域异常按默认语言转述给 Agent，问题码沿用异常的 i18n key。"""
    detail = translate(exc.key, DEFAULT_LOCALE, **exc.params)
    params: dict[str, Any] | None = None
    if exc.key == "version_not_found":
        available = [
            record["version"]
            for record in versions.get_versions(resource_type, unit_id).get("versions", [])
            if isinstance(record, dict) and isinstance(record.get("version"), int)
        ]
        params = {"available_versions": available}
        detail = f"{detail}；该视频单元现有版本：{', '.join(map(str, available)) or '无'}"
    return tool_problem(detail, code=exc.key, params=params)


async def select_video_version(
    request: ToolRequest[SelectVideoVersionRequest],
    scope: ProjectScope,
    _caller: CallerContext,
    services: Services,
) -> ToolOutcome[dict[str, Any]]:
    unit_id = request.value.unit_id
    try:
        project = services.projects.load_project(scope.project_name)
        if not _unit_exists(services.projects, scope.project_name, project, unit_id):
            return tool_problem(f"视频单元「{unit_id}」不存在", code="video_unit_not_found")

        resource_type = "reference_videos" if is_reference_video_project(project) else "videos"
        versions = VersionManager(services.projects.get_project_path(scope.project_name))
        try:
            restored = await restore_typed_media_resource(
                project_manager=services.projects,
                versions=versions,
                project_name=scope.project_name,
                resource_type=resource_type,
                resource_id=unit_id,
                version=request.value.version,
            )
        except ApiError as exc:
            return _api_problem(exc, unit_id=unit_id, versions=versions, resource_type=resource_type)
        return ToolOutcome(
            value={
                "unit_id": unit_id,
                "current_version": restored["current_version"],
                "file_path": restored["file_path"],
                "prompt": restored.get("prompt"),
            }
        )
    except Exception as exc:
        return tool_error(_OPERATION, exc)


def select_video_version_summary(value: dict[str, Any]) -> str:
    return f"✅ 视频单元 {value['unit_id']} 的 current 视频版本已切换为 v{value['current_version']}（{value['file_path']}）"


__all__ = ["SelectVideoVersionRequest", "select_video_version", "select_video_version_summary"]
