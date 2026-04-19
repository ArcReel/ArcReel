"""项目级资产 CRUD 路由的统一工厂（character / scene / prop）。

按 lib.asset_types.ASSET_SPECS 驱动，三类资产共用同一份路由模板。每类资产仅用 5 行
启用：

    router = build_asset_router(asset_type="character", pm_getter=lambda: get_project_manager())

工厂内部从 spec 解析 URL 路径段、bucket key、sheet 字段、PATCH 字段白名单
（description + sheet_field + extra_string_fields）。i18n key 命名差异（scene 用
历史前缀 "project_scene_*"）通过 _I18N_KEYS 表维护。
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict

from lib.asset_types import ASSET_SPECS
from lib.i18n import Translator
from lib.project_change_hints import project_change_source
from lib.project_manager import ProjectManager
from server.auth import CurrentUser

logger = logging.getLogger(__name__)


_I18N_KEYS: dict[str, dict[str, str]] = {
    "character": {
        "exists": "character_already_exists",
        "not_found": "character_not_found",
        "deleted": "character_deleted",
    },
    "scene": {
        "exists": "project_scene_already_exists",
        "not_found": "project_scene_not_found",
        "deleted": "project_scene_deleted",
    },
    "prop": {
        "exists": "prop_already_exists",
        "not_found": "prop_not_found",
        "deleted": "prop_deleted",
    },
}


class _CreateRequest(BaseModel):
    """通用 create 请求体；额外字段（如 voice_style）通过 extra='allow' 透传。"""

    model_config = ConfigDict(extra="allow")

    name: str
    description: str = ""


def build_asset_router(
    *,
    asset_type: str,
    pm_getter: Callable[[], ProjectManager],
) -> APIRouter:
    """构造单一类型的项目级资产 CRUD 路由。

    pm_getter 应为 lambda，每次调用动态读取 get_project_manager，确保 monkeypatch
    测试生效。
    """
    if asset_type not in ASSET_SPECS:
        raise ValueError(f"unknown asset_type: {asset_type}")
    spec = ASSET_SPECS[asset_type]
    keys = _I18N_KEYS[asset_type]
    result_key = asset_type
    update_fields: tuple[str, ...] = ("description", spec.sheet_field, *spec.extra_string_fields)

    router = APIRouter()

    @router.post(f"/projects/{{project_name}}/{spec.subdir}")
    async def add_entry(
        project_name: str,
        req: _CreateRequest,
        _user: CurrentUser,
        _t: Translator,
    ):
        try:
            extras = req.model_extra or {}

            def _sync():
                manager = pm_getter()
                entry: dict[str, Any] = {"description": req.description, spec.sheet_field: ""}
                for field in spec.extra_string_fields:
                    entry[field] = extras.get(field, "")
                with project_change_source("webui"):
                    ok = manager._add_asset(asset_type, project_name, req.name, entry)
                if not ok:
                    raise HTTPException(status_code=409, detail=_t(keys["exists"], name=req.name))
                data = manager.load_project(project_name)
                return {"success": True, result_key: data[spec.bucket_key][req.name]}

            return await asyncio.to_thread(_sync)
        except FileNotFoundError:
            raise HTTPException(status_code=404, detail=_t("project_not_found", name=project_name))
        except HTTPException:
            raise
        except Exception as exc:
            logger.exception("请求处理失败")
            raise HTTPException(status_code=500, detail=str(exc))

    @router.patch(f"/projects/{{project_name}}/{spec.subdir}/{{entry_name}}")
    async def update_entry(
        project_name: str,
        entry_name: str,
        req: dict[str, Any],
        _user: CurrentUser,
        _t: Translator,
    ):
        try:

            def _sync():
                manager = pm_getter()
                result: dict[str, Any] = {}

                def _mutate(project):
                    bucket = project.get(spec.bucket_key) or {}
                    if entry_name not in bucket:
                        raise KeyError(entry_name)
                    entry = bucket[entry_name]
                    for field in update_fields:
                        if req.get(field) is not None:
                            entry[field] = req[field]
                    result.update(entry)

                with project_change_source("webui"):
                    manager.update_project(project_name, _mutate)
                return {"success": True, result_key: result}

            return await asyncio.to_thread(_sync)
        except KeyError:
            raise HTTPException(status_code=404, detail=_t(keys["not_found"], name=entry_name))
        except FileNotFoundError:
            raise HTTPException(status_code=404, detail=_t("project_not_found", name=project_name))
        except HTTPException:
            raise
        except Exception as exc:
            logger.exception("请求处理失败")
            raise HTTPException(status_code=500, detail=str(exc))

    @router.delete(f"/projects/{{project_name}}/{spec.subdir}/{{entry_name}}")
    async def delete_entry(project_name: str, entry_name: str, _user: CurrentUser, _t: Translator):
        try:

            def _sync():
                manager = pm_getter()

                def _mutate(project):
                    bucket = project.get(spec.bucket_key) or {}
                    if entry_name not in bucket:
                        raise KeyError(entry_name)
                    del bucket[entry_name]

                with project_change_source("webui"):
                    manager.update_project(project_name, _mutate)
                return {"success": True, "message": _t(keys["deleted"], name=entry_name)}

            return await asyncio.to_thread(_sync)
        except KeyError:
            raise HTTPException(status_code=404, detail=_t(keys["not_found"], name=entry_name))
        except FileNotFoundError:
            raise HTTPException(status_code=404, detail=_t("project_not_found", name=project_name))
        except HTTPException:
            raise
        except Exception as exc:
            logger.exception("请求处理失败")
            raise HTTPException(status_code=500, detail=str(exc))

    return router
