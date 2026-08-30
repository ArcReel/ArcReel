"""费用估算 API 路由。"""

from __future__ import annotations

import asyncio
import logging

from fastapi import APIRouter, HTTPException

from lib.api_errors import NotFoundError
from lib.config.resolver import ConfigResolver
from lib.db import async_session_factory
from lib.i18n import Translator
from lib.project_manager import get_project_manager
from lib.reference_video import find_reference_unit
from lib.reference_video.request_projection import POST_PRODUCTION, NarrationDelivery, ReferenceRequestOptions
from server.services.cost_estimation import CostEstimationService

router = APIRouter()
logger = logging.getLogger(__name__)


@router.get("/projects/{project_name}/cost-estimate")
async def get_cost_estimate(
    project_name: str,
    _t: Translator,
    reference_unit_id: str | None = None,
    narration_delivery: NarrationDelivery = POST_PRODUCTION,
):
    """获取项目费用估算（预估 + 实际）。"""

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

        return project_data, scripts

    project_data, scripts = await asyncio.to_thread(_sync)

    resolver = ConfigResolver(async_session_factory)
    service = CostEstimationService(
        resolver,
        async_session_factory,
        project_path=get_project_manager().get_project_path(project_name),
    )

    if reference_unit_id and not any(find_reference_unit(script, reference_unit_id) for script in scripts.values()):
        raise HTTPException(status_code=404, detail=_t("ref_unit_not_found", unit_id=reference_unit_id))
    reference_request_options = (
        {
            reference_unit_id: ReferenceRequestOptions(
                narration_delivery=narration_delivery,
            )
        }
        if reference_unit_id
        else None
    )

    try:
        return await service.compute(
            project_data,
            scripts,
            project_name=project_name,
            reference_request_options=reference_request_options,
        )
    except Exception as exc:
        logger.exception("费用估算失败")
        raise HTTPException(status_code=500, detail=_t("cost_estimation_failed")) from exc
