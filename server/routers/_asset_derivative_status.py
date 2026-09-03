"""衍生资产图的读取视图：图路径与过期标记。

登记信息（描述、名字）本就随本体条目一起下发，唯独「这张衍生图还对不对得上本体现在的样子」
不在 project.json 里——它是产物清单与规范状态的一次比对。比对要读文件、算内容指纹，放进
项目读取这条最热的路径上不合算，故单列一个按角色寻址的端点，由需要它的界面按需取。
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from typing import Any

from fastapi import APIRouter, HTTPException

from lib.api_errors import NotFoundError
from lib.artifact_activation import active_artifact_currency_resolver
from lib.artifact_manifest import ArtifactStatus
from lib.asset_derivatives import derivative_artifact_key, derivative_table
from lib.asset_types import DERIVATIVES_FIELD, AssetSpec, asset_name_comparison_key, resolve_asset_key
from lib.i18n import Translator
from lib.project_manager import ProjectManager

logger = logging.getLogger(__name__)


def register_derivative_status_routes(
    router: APIRouter,
    *,
    spec: AssetSpec,
    not_found_key: str,
    pm_getter: Callable[[], ProjectManager],
) -> None:
    """把「本体名下衍生的图与过期状态」查询端点注册到该资产类型的路由上。"""

    base = f"/projects/{{project_name}}/{spec.subdir}/{{entry_name}}/{DERIVATIVES_FIELD}"

    # 处理器由 @router.get 就地注册，模块内无其它引用；basedpyright 把函数作用域内的符号
    # 一律判为私有，reportUnusedFunction 在此是工具误报。
    @router.get(base)
    async def list_derivatives(  # pyright: ignore[reportUnusedFunction]
        project_name: str,
        entry_name: str,
        _t: Translator,
    ):
        def _sync() -> dict[str, Any]:
            manager = pm_getter()
            project = manager.load_project(project_name)
            project_dir = manager.get_project_path(project_name)
            bucket = project.get(spec.bucket_key)
            owner_key = resolve_asset_key(bucket, entry_name)
            entry = bucket.get(owner_key) if isinstance(bucket, dict) and owner_key is not None else None
            if owner_key is None or not isinstance(entry, dict):
                raise HTTPException(status_code=404, detail=_t(not_found_key, name=entry_name))
            resolver = active_artifact_currency_resolver(project_dir, project)
            derivatives: dict[str, dict[str, Any]] = {}
            for name, derivative in derivative_table(entry).items():
                if not isinstance(derivative, dict):
                    continue
                sheet = derivative.get(spec.sheet_field)
                sheet_path = sheet if isinstance(sheet, str) and sheet else ""
                stale = False
                if sheet_path:
                    comparison = resolver.compare(
                        derivative_artifact_key(asset_name_comparison_key(owner_key), asset_name_comparison_key(name)),
                        artifact_path=sheet_path,
                    )
                    # 只有「登记在案但已不等于规范状态」才是过期；缺失或被阻断另有其表现
                    # （图根本渲染不出来），不折进同一个标记。
                    stale = comparison.status is ArtifactStatus.STALE
                derivatives[name] = {
                    "description": derivative.get("description", ""),
                    spec.sheet_field: sheet_path,
                    "stale": stale,
                }
            return {"success": True, DERIVATIVES_FIELD: derivatives}

        try:
            return await asyncio.to_thread(_sync)
        except FileNotFoundError as exc:
            raise NotFoundError("project_not_found", name=project_name) from exc
        except HTTPException:
            raise
        except Exception as exc:
            logger.exception("请求处理失败")
            raise HTTPException(status_code=500, detail=_t("internal_server_error")) from exc
