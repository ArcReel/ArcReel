"""费用估算 API 路由。"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Request

from lib.config.resolver import ConfigResolver
from lib.db import async_session_factory
from lib.infra.api_errors import ApiError, NotFoundError
from lib.infra.async_thread import run_sync_transaction
from lib.project.project_manager import ProjectManager, get_project_manager
from server.auth import CurrentUser
from server.dependencies import ProjectReads
from server.routers._validators import reject_retired_query_params
from server.services.admission.cost_estimation import CostEstimationService

router = APIRouter()
logger = logging.getLogger(__name__)


@router.get("/projects/{project_name}/cost-estimate")
async def get_cost_estimate(
    project_name: str,
    request: Request,
    current_user: CurrentUser,
    coalescer: ProjectReads,
):
    """获取项目费用估算（预估 + 实际）。"""

    reject_retired_query_params(request, "narration_delivery", "reference_unit_id")
    pm = get_project_manager()
    return await coalescer.run(
        ("cost-estimate", id(pm), project_name, current_user.id, current_user.role, id(async_session_factory)),
        lambda: _estimate(pm, project_name),
    )


async def _estimate(pm: ProjectManager, project_name: str):
    def _sync():
        if not pm.project_exists(project_name):
            raise NotFoundError("project_not_found", name=project_name)

        try:
            project_data = pm.load_project(project_name)
        except FileNotFoundError as exc:
            raise NotFoundError("project_not_found", name=project_name) from exc

        scripts: dict[str, dict] = {}
        for ep in project_data.get("episodes", []):
            script_file = ep.get("script_file", "")
            if script_file:
                try:
                    scripts[script_file] = pm.load_script(project_name, script_file)
                except FileNotFoundError:
                    logger.debug("剧本文件不存在，跳过: %s/%s", project_name, script_file)

        return project_data, scripts, pm.get_project_path(project_name)

    project_data, scripts, project_path = await run_sync_transaction(_sync)

    resolver = ConfigResolver(async_session_factory)
    service = CostEstimationService(
        resolver,
        async_session_factory,
        project_path=project_path,
    )

    try:
        return await service.compute(
            project_data,
            scripts,
            project_name=project_name,
        )
    except Exception as exc:
        logger.exception("费用估算失败")
        raise ApiError("cost_estimation_failed", status_code=500) from exc
