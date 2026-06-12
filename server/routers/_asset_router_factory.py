"""项目级资产 CRUD 路由的统一工厂（character / scene / prop / product）。

按 lib.asset_types.ASSET_SPECS 驱动，各类资产共用同一份路由模板。每类资产仅用 5 行
启用：

    router = build_asset_router(asset_type="character", pm_getter=lambda: get_project_manager())

工厂内部从 spec 解析 URL 路径段、bucket key、sheet 字段、PATCH 字段白名单
（description + sheet_field + extra_string_fields + extra_list_fields）。i18n key
命名差异（scene 用历史前缀 "project_scene_*"）通过 _I18N_KEYS 表维护。
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict

from lib.asset_types import ASSET_SPECS, validate_asset_name
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
    "product": {
        "exists": "product_already_exists",
        "not_found": "product_not_found",
        "deleted": "product_deleted",
    },
}


def _is_string_list(value: Any) -> bool:
    return isinstance(value, list) and all(isinstance(item, str) for item in value)


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
    update_list_fields: tuple[str, ...] = spec.extra_list_fields

    router = APIRouter()

    @router.post(f"/projects/{{project_name}}/{spec.subdir}")
    async def add_entry(
        project_name: str,
        req: _CreateRequest,
        _user: CurrentUser,
        _t: Translator,
    ):
        # 名称会被拼进文件路径与单段路由参数，路径不安全的名字在边界即拒绝，
        # 否则后续生成与按名访问（PATCH/DELETE/{name}）全部失效。
        try:
            name = validate_asset_name(req.name)
        except ValueError:
            raise HTTPException(status_code=400, detail=_t("asset_invalid_name", name=req.name))
        extras = req.model_extra or {}
        # 列表字段（reference_images / selling_points 等）在创建时即校验为字符串列表，
        # 非法类型 422 在边界拦截，避免污染 project.json。
        for field in spec.extra_list_fields:
            value = extras.get(field)
            if value is not None and not _is_string_list(value):
                raise HTTPException(status_code=422, detail=f"field '{field}' must be a list of strings")
        try:

            def _sync():
                manager = pm_getter()
                entry: dict[str, Any] = {"description": req.description, spec.sheet_field: ""}
                for field in spec.extra_string_fields:
                    entry[field] = extras.get(field, "")
                for field in spec.extra_list_fields:
                    entry[field] = list(extras.get(field) or [])
                with project_change_source("webui"):
                    ok = manager._add_asset(asset_type, project_name, name, entry)
                if not ok:
                    raise HTTPException(status_code=409, detail=_t(keys["exists"], name=name))
                data = manager.load_project(project_name)
                return {"success": True, result_key: data[spec.bucket_key][name]}

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
        # 写入前对所有可写字段做类型校验。req 是 dict[str, Any]，若客户端传入错误类型
        # 会污染 project.json 并在下游 (例如 execute_character_task 拼接 reference_image
        # 路径) 引发 TypeError。422 在边界拦截。字符串字段须为 str，列表字段须为字符串列表。
        for field in update_fields:
            value = req.get(field)
            if value is not None and not isinstance(value, str):
                raise HTTPException(status_code=422, detail=f"field '{field}' must be a string")
        for field in update_list_fields:
            value = req.get(field)
            if value is not None and not _is_string_list(value):
                raise HTTPException(status_code=422, detail=f"field '{field}' must be a list of strings")

        try:

            def _sync():
                manager = pm_getter()
                result: dict[str, Any] = {}

                def _mutate(project):
                    bucket = project.get(spec.bucket_key) or {}
                    if entry_name not in bucket:
                        raise KeyError(entry_name)
                    entry = bucket[entry_name]
                    for field in (*update_fields, *update_list_fields):
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
