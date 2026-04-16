"""项目内资产桶（scenes/props）的通用 CRUD 路由工厂。

两类资产（场景 / 道具）结构同构：都是 project.json 里的 dict bucket，名字唯一，
仅字段命名不同（scene_sheet vs prop_sheet）。该 factory 将 CRUD 模板化，避免
两个路由文件逐字镜像。
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from lib.i18n import Translator
from lib.project_change_hints import project_change_source
from lib.project_manager import ProjectManager
from server.auth import CurrentUser

logger = logging.getLogger(__name__)


class BucketCreateRequest(BaseModel):
    name: str
    description: str = ""


class BucketUpdateRequest(BaseModel):
    description: str | None = None
    sheet: str | None = None  # 客户端用 scene_sheet / prop_sheet 映射到此


def build_bucket_router(
    *,
    bucket_key: str,  # "scenes" | "props"
    sheet_field: str,  # "scene_sheet" | "prop_sheet"
    path_segment: str,  # 路径段（与 bucket_key 一致，保留参数便于未来灵活命名）
    result_key: str,  # 响应体 key（单数：scene / prop）
    i18n_exists_key: str,
    i18n_not_found_key: str,
    i18n_deleted_key: str,
    add_method: Callable[[ProjectManager, str, str, str], bool],
    pm_getter: Callable[[], ProjectManager],
) -> APIRouter:
    """pm_getter 应为 lambda，每次调用时从模块作用域动态读取 get_project_manager，
    确保 monkeypatch 测试生效。"""
    router = APIRouter()

    @router.post(f"/projects/{{project_name}}/{path_segment}")
    async def add_entry(
        project_name: str,
        req: BucketCreateRequest,
        _user: CurrentUser,
        _t: Translator,
    ):
        try:

            def _sync():
                with project_change_source("webui"):
                    ok = add_method(pm_getter(), project_name, req.name, req.description)
                if not ok:
                    raise HTTPException(status_code=409, detail=_t(i18n_exists_key, name=req.name))
                data = pm_getter().load_project(project_name)
                return {"success": True, result_key: data[bucket_key][req.name]}

            return await asyncio.to_thread(_sync)
        except FileNotFoundError:
            raise HTTPException(status_code=404, detail=_t("project_not_found", name=project_name))
        except HTTPException:
            raise
        except Exception as exc:
            logger.exception("请求处理失败")
            raise HTTPException(status_code=500, detail=str(exc))

    @router.patch(f"/projects/{{project_name}}/{path_segment}/{{entry_name}}")
    async def update_entry(
        project_name: str,
        entry_name: str,
        req: dict[str, Any],  # 动态字段：description + <sheet_field>
        _user: CurrentUser,
        _t: Translator,
    ):
        try:

            def _sync():
                manager = pm_getter()
                result: dict[str, Any] = {}

                def _mutate(project):
                    if entry_name not in project.get(bucket_key, {}):
                        raise KeyError(entry_name)
                    entry = project[bucket_key][entry_name]
                    if req.get("description") is not None:
                        entry["description"] = req["description"]
                    if req.get(sheet_field) is not None:
                        entry[sheet_field] = req[sheet_field]
                    result.update(entry)

                with project_change_source("webui"):
                    manager.update_project(project_name, _mutate)
                return {"success": True, result_key: result}

            return await asyncio.to_thread(_sync)
        except KeyError:
            raise HTTPException(status_code=404, detail=_t(i18n_not_found_key, name=entry_name))
        except FileNotFoundError:
            raise HTTPException(status_code=404, detail=_t("project_not_found", name=project_name))
        except HTTPException:
            raise
        except Exception as exc:
            logger.exception("请求处理失败")
            raise HTTPException(status_code=500, detail=str(exc))

    @router.delete(f"/projects/{{project_name}}/{path_segment}/{{entry_name}}")
    async def delete_entry(project_name: str, entry_name: str, _user: CurrentUser, _t: Translator):
        try:

            def _sync():
                manager = pm_getter()

                def _mutate(project):
                    if entry_name not in project.get(bucket_key, {}):
                        raise KeyError(entry_name)
                    del project[bucket_key][entry_name]

                with project_change_source("webui"):
                    manager.update_project(project_name, _mutate)
                return {"success": True, "message": _t(i18n_deleted_key, name=entry_name)}

            return await asyncio.to_thread(_sync)
        except KeyError:
            raise HTTPException(status_code=404, detail=_t(i18n_not_found_key, name=entry_name))
        except FileNotFoundError:
            raise HTTPException(status_code=404, detail=_t("project_not_found", name=project_name))
        except HTTPException:
            raise
        except Exception as exc:
            logger.exception("请求处理失败")
            raise HTTPException(status_code=500, detail=str(exc))

    return router
