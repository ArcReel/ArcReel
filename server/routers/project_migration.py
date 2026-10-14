"""项目数据升级的 Web 入口：重试升级，确认升级报告。

重试与 Agent 的 ``retry_project_migration`` 调用同一个服务命令，路由只把结果映射成 HTTP。升级失败时项目的
其余写入入口都被阻断，重试正是解除阻断的途径，所以不挂 ``require_project_migration_ok``；确认报告只记下
用户已看过，同样不挂。
"""

import asyncio

from fastapi import APIRouter
from pydantic import BaseModel

from lib.infra.api_errors import NotFoundError, UnprocessableError
from lib.infra.async_thread import run_sync_transaction
from lib.project.project_manager import ProjectManager, get_project_manager
from lib.project.project_migration_report import acknowledge_migration_report
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
    if not await asyncio.to_thread(pm.project_exists, name):
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


class AcknowledgeReportRequest(BaseModel):
    #: 用户看过的那份报告的生成时间。盘上已换成另一份报告（之后又升级过）时不确认，新报告照常提示。
    migrated_at: str


@router.post("/projects/{name}/migration-report/acknowledge")
async def acknowledge_report(name: str, body: AcknowledgeReportRequest, _user: CurrentUser):
    """记下用户已看过上一次升级的报告：项目页不再提示。没有待确认的报告时同样成功。

    ``acknowledged`` 为假表示盘上没有这份待确认的报告：要么已确认过，要么已换成另一份。
    """
    acknowledged = await run_sync_transaction(_acknowledge, get_project_manager(), name, body.migrated_at)
    return {"success": True, "acknowledged": acknowledged}


def _acknowledge(pm: ProjectManager, name: str, migrated_at: str) -> bool:
    if not pm.project_exists(name):
        raise NotFoundError("project_not_found", name=name)
    return acknowledge_migration_report(pm.get_project_path(name), migrated_at=migrated_at)
