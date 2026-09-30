"""项目数据升级的 Web 重试入口。

与 Agent 的 ``retry_project_migration`` 调用同一个服务命令，路由只把结果映射成 HTTP。升级失败时项目的
其余写入入口都被阻断，本入口正是解除阻断的途径，所以不挂 ``require_project_migration_ok``。
"""

from fastapi import APIRouter

from lib.infra.api_errors import NotFoundError, UnprocessableError
from lib.project.project_manager import get_project_manager
from server.auth import CurrentUser
from server.tool_runtime import (
    CallerContext,
    NoArguments,
    ProjectScope,
    Services,
    ToolRequest,
    retry_project_migration,
)

router = APIRouter()


@router.post("/projects/{name}/migration/retry")
async def retry_migration(name: str, user: CurrentUser):
    """重跑本项目的数据升级链（含产物补录）；已是最新版本时直接成功。

    升级仍失败时 422，诊断里的 ``reason`` 是失败原文，``details`` 是结构化明细（集 / 文件 / 违约）。
    """
    pm = get_project_manager()
    if not pm.project_exists(name):
        raise NotFoundError("project_not_found", name=name)
    outcome = await retry_project_migration(
        ToolRequest(NoArguments()),
        ProjectScope(project_name=name, data_root=pm.data_root),
        CallerContext(user_id=user.id, source="webui"),
        Services.defaults(pm),
    )
    if outcome.problem is not None:
        params = outcome.problem.params or {}
        raise UnprocessableError("project_migration_retry_failed").with_diagnostic(
            {"reason": outcome.problem.detail, "details": params.get("details", [])}
        )
    return {"success": True}
