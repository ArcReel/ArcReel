"""资产条目下的衍生子资源路由（``AssetSpec.supports_derivatives`` 开启的类型才注册）。

衍生挂在本体条目内的 ``DERIVATIVES_FIELD`` 表下，共享本体的名字与身份，只在所属条目内
唯一，引用写作 ``本体名/衍生名``。写入统一经 ``ProjectManager.update_asset_entry``，
与本体字段的 PATCH 共用同一条项目锁 / 正式产物边界。

本模块只做登记（新增、改描述、改名、删除）；衍生资产图的生成、版本与过期判定不在此处。
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from lib.api_errors import NotFoundError, UnprocessableError
from lib.asset_types import (
    DERIVATIVES_FIELD,
    AssetSpec,
    normalize_asset_name,
    rekey_equivalent_entries,
    resolve_asset_key,
    validate_asset_name,
)
from lib.i18n import Translator
from lib.project_change_hints import project_change_source
from lib.project_manager import ProjectManager

logger = logging.getLogger(__name__)


class _DerivativeCreateRequest(BaseModel):
    """新增衍生：名字在所属本体内唯一，描述是相对本体的外观变化。"""

    name: str
    description: str = ""


class _DerivativeUpdateRequest(BaseModel):
    """更新衍生描述。改名走 ``/rename``，资产图路径由生成侧写入，均不在此。"""

    description: str


class _DerivativeRenameRequest(BaseModel):
    new_name: str


class _DerivativeExists(Exception):
    """目标衍生名已被同一本体下的另一个衍生占用。"""

    def __init__(self, name: str):
        self.name = name
        super().__init__(name)


class _DerivativeMissing(Exception):
    """按名寻址的衍生在本体的衍生表里不存在。"""

    def __init__(self, name: str):
        self.name = name
        super().__init__(name)


def _validated_name(raw: str) -> str:
    """衍生名沿用资产名校验：它同样被拼进单段文件名与单段路由参数。"""
    try:
        return validate_asset_name(raw)
    except ValueError as exc:
        raise UnprocessableError("asset_derivative_invalid_name", name=raw) from exc


def _derivative_table(entry: dict[str, Any]) -> dict[str, Any]:
    """取本体条目里的衍生表；缺失或畸形时就地补空表，让写入落在可预期的形状上。"""
    table = entry.get(DERIVATIVES_FIELD)
    if not isinstance(table, dict):
        table = {}
        entry[DERIVATIVES_FIELD] = table
    return table


def register_derivative_routes(
    router: APIRouter,
    *,
    spec: AssetSpec,
    not_found_key: str,
    pm_getter: Callable[[], ProjectManager],
) -> None:
    """把衍生子资源的四个端点注册到该资产类型的路由上。"""

    base = f"/projects/{{project_name}}/{spec.subdir}/{{entry_name}}/{DERIVATIVES_FIELD}"
    asset_type = spec.asset_type

    async def _apply(
        project_name: str,
        entry_name: str,
        mutate: Callable[[dict[str, Any]], None],
        _t: Translator,
    ) -> dict[str, Any]:
        """在本体条目上跑一次衍生表变更，把领域异常映射为面向用户的响应。"""
        try:

            def _sync():
                manager = pm_getter()
                with project_change_source("webui"):
                    return manager.update_asset_entry(asset_type, project_name, entry_name, mutate)

            return await asyncio.to_thread(_sync)
        except _DerivativeExists as exc:
            raise UnprocessableError("asset_derivative_already_exists", name=exc.name) from exc
        except _DerivativeMissing as exc:
            raise NotFoundError("asset_derivative_not_found", name=exc.name) from exc
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=_t(not_found_key, name=entry_name)) from exc
        except FileNotFoundError as exc:
            raise NotFoundError("project_not_found", name=project_name) from exc
        except HTTPException:
            raise
        except Exception as exc:
            logger.exception("请求处理失败")
            raise HTTPException(status_code=500, detail=_t("internal_server_error")) from exc

    # 以下四个处理器由 @router.* 就地注册，模块内无其它引用；basedpyright 把函数作用域内的符号
    # 一律判为私有，逐个标注的 reportUnusedFunction 均为工具误报。
    @router.post(base)
    async def add_derivative(  # pyright: ignore[reportUnusedFunction]
        project_name: str,
        entry_name: str,
        req: _DerivativeCreateRequest,
        _t: Translator,
    ):
        name = _validated_name(req.name)

        def _mutate(entry: dict[str, Any]) -> None:
            table = _derivative_table(entry)
            if resolve_asset_key(table, name) is not None:
                raise _DerivativeExists(name)
            table[name] = {"description": req.description, spec.sheet_field: ""}

        result = await _apply(project_name, entry_name, _mutate, _t)
        return {"success": True, asset_type: result}

    @router.patch(f"{base}/{{derivative_name}}")
    async def update_derivative(  # pyright: ignore[reportUnusedFunction]
        project_name: str,
        entry_name: str,
        derivative_name: str,
        req: _DerivativeUpdateRequest,
        _t: Translator,
    ):
        def _mutate(entry: dict[str, Any]) -> None:
            table = _derivative_table(entry)
            key = resolve_asset_key(table, derivative_name)
            if key is None:
                raise _DerivativeMissing(derivative_name)
            derivative = table[key]
            if not isinstance(derivative, dict):
                raise ValueError(f"derivative {key!r} must be an object")
            derivative["description"] = req.description

        result = await _apply(project_name, entry_name, _mutate, _t)
        return {"success": True, asset_type: result}

    @router.post(f"{base}/{{derivative_name}}/rename")
    async def rename_derivative(  # pyright: ignore[reportUnusedFunction]
        project_name: str,
        entry_name: str,
        derivative_name: str,
        req: _DerivativeRenameRequest,
        _t: Translator,
    ):
        """改名只改本体条目内的键。脚本正文里 ``本体名/旧衍生名`` 的级联改写不在此处。"""
        new_name = _validated_name(req.new_name)

        def _mutate(entry: dict[str, Any]) -> None:
            table = _derivative_table(entry)
            key = resolve_asset_key(table, derivative_name)
            if key is None:
                raise _DerivativeMissing(derivative_name)
            taken = resolve_asset_key(table, new_name)
            if taken is not None and normalize_asset_name(taken) != normalize_asset_name(key):
                raise _DerivativeExists(new_name)
            rekey_equivalent_entries(table, key, new_name)

        result = await _apply(project_name, entry_name, _mutate, _t)
        return {"success": True, asset_type: result}

    @router.delete(f"{base}/{{derivative_name}}")
    async def delete_derivative(  # pyright: ignore[reportUnusedFunction]
        project_name: str,
        entry_name: str,
        derivative_name: str,
        _t: Translator,
    ):
        def _mutate(entry: dict[str, Any]) -> None:
            table = _derivative_table(entry)
            key = resolve_asset_key(table, derivative_name)
            if key is None:
                raise _DerivativeMissing(derivative_name)
            del table[key]

        await _apply(project_name, entry_name, _mutate, _t)
        return {"success": True, "message": _t("asset_derivative_deleted", name=derivative_name)}
