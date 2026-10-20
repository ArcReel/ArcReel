"""费用估算 API 路由。"""

from __future__ import annotations

import logging
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request

from lib.config.resolver import ConfigResolver
from lib.db import async_session_factory
from lib.infra.api_errors import NotFoundError
from lib.infra.async_thread import run_sync_transaction
from lib.project.project_manager import get_project_manager
from server.i18n import Locale, Translator
from server.routers._coalesce import RequestCoalescer
from server.routers._validators import reject_retired_query_params
from server.services.admission.cost_estimation import CostEstimationService

router = APIRouter()
logger = logging.getLogger(__name__)

_coalescer = RequestCoalescer()


def get_cost_estimate_coalescer() -> RequestCoalescer:
    return _coalescer


@router.get("/projects/{project_name}/cost-estimate")
async def get_cost_estimate(
    project_name: str,
    request: Request,
    _t: Translator,
    locale: Locale,
    coalescer: Annotated[RequestCoalescer, Depends(get_cost_estimate_coalescer)],
):
    """获取项目费用估算（预估 + 实际）。同一项目、同一语言的并发请求合流计算。"""

    reject_retired_query_params(request, "narration_delivery", "reference_unit_id")
    return await coalescer.run((project_name, locale), lambda: _estimate(project_name, _t))


async def _estimate(project_name: str, _t: Translator):
    def _sync():
        pm = get_project_manager()
        if not pm.project_exists(project_name):
            raise HTTPException(status_code=404, detail=_t("project_not_found", name=project_name))

        try:
            project_data = pm.load_project(project_name)
        except FileNotFoundError as exc:
            raise NotFoundError("project_not_found", name=project_name) from exc

        # 加载所有剧本
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
        raise HTTPException(status_code=500, detail=_t("cost_estimation_failed")) from exc
