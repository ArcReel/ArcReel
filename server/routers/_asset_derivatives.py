"""资产条目下的衍生子资源路由（``AssetSpec.supports_derivatives`` 开启的类型才注册）。

衍生挂在本体条目内的 ``DERIVATIVES_FIELD`` 表下，共享本体的名字与身份，只在所属条目内
唯一，引用写作 ``本体名/衍生名``。新增 / 改描述 / 删除经 ``ProjectManager.update_asset_entry``，
与本体字段的 PATCH 共用同一条项目锁 / 正式产物边界；改名要连带改写剧本里的
``本体名/旧衍生名`` 引用，走 ``ProjectManager.rename_asset_derivative`` 的级联事务。

本模块只做登记（新增、改描述、删除）与改名；衍生资产图的生成、版本与过期判定不在此处。
改名与删除是例外：那张图的落盘坐标含衍生名，改名要连带搬图、版本历史与清单键
（``lib.asset_derivative_rename``），删除要连带清掉三者（``lib.asset_derivative_cleanup``），
两者都与登记写入同属一次提交。
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from lib.api_errors import NotFoundError, UnprocessableError
from lib.asset_derivative_cleanup import purge_derivative_sheets
from lib.asset_derivatives import ensure_derivative_table
from lib.asset_rename import (
    AssetRenameConflictError,
    AssetRenameFileCollisionError,
    AssetRenameHistoryCollisionError,
    AssetRenameNotFoundError,
)
from lib.asset_types import (
    ASSET_SPECS,
    DERIVATIVES_FIELD,
    AssetSpec,
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


def _resolve_owner_key(manager: ProjectManager, asset_type: str, project_name: str, entry_name: str) -> str | None:
    """取本体条目的落盘真名；缺失时返回 ``None``，由随后的写入统一报 404。"""
    bucket = manager.load_project(project_name).get(ASSET_SPECS[asset_type].bucket_key)
    return resolve_asset_key(bucket, entry_name)


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

    async def _run(
        project_name: str,
        entry_name: str,
        write: Callable[[ProjectManager, Callable[[Path], None]], dict[str, Any]],
        _t: Translator,
        on_commit: Callable[[ProjectManager, str], None] | None = None,
    ) -> dict[str, Any]:
        """跑一次衍生写入，把领域异常映射为面向用户的响应。

        改名走 ``rename_asset_derivative`` 的级联事务，其余三个端点走
        ``update_asset_entry``；两条写入路径的异常映射同一份，端点不各自兜一遍。
        ``on_commit`` 与该写入同属一次提交，收到本体的落盘真名——删除路径据此清理那张图。
        """
        try:

            def _sync():
                manager = pm_getter()
                with project_change_source("webui"):
                    # 本体的落盘真名在写入前解析一次：级联清理要用它拼衍生图的目录，而
                    # 请求里的名字可能是另一种等价编码形式。
                    owner_key = _resolve_owner_key(manager, asset_type, project_name, entry_name)

                    def _commit(_project_file: Path) -> None:
                        if on_commit is not None and owner_key is not None:
                            on_commit(manager, owner_key)

                    return write(manager, _commit)

            return await asyncio.to_thread(_sync)
        except _DerivativeExists as exc:
            raise UnprocessableError("asset_derivative_already_exists", name=exc.name) from exc
        except _DerivativeMissing as exc:
            raise NotFoundError("asset_derivative_not_found", name=exc.name) from exc
        except AssetRenameFileCollisionError as exc:
            # 衍生图与本体资产图共用这两条冲突文案：拒绝的形状与用户要做的事完全一样。
            raise HTTPException(
                status_code=409, detail=_t("asset_rename_file_conflict", filename=exc.destination.name)
            ) from exc
        except AssetRenameHistoryCollisionError as exc:
            raise HTTPException(
                status_code=409, detail=_t("asset_rename_history_conflict", name=exc.resource_id)
            ) from exc
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
            table = ensure_derivative_table(entry)
            if resolve_asset_key(table, name) is not None:
                raise _DerivativeExists(name)
            table[name] = {"description": req.description, spec.sheet_field: ""}

        result = await _run(
            project_name,
            entry_name,
            lambda manager, commit: manager.update_asset_entry(
                asset_type, project_name, entry_name, _mutate, on_commit=commit
            ),
            _t,
        )
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
            table = ensure_derivative_table(entry)
            key = resolve_asset_key(table, derivative_name)
            if key is None:
                raise _DerivativeMissing(derivative_name)
            derivative = table[key]
            if not isinstance(derivative, dict):
                raise ValueError(f"derivative {key!r} must be an object")
            derivative["description"] = req.description

        result = await _run(
            project_name,
            entry_name,
            lambda manager, commit: manager.update_asset_entry(
                asset_type, project_name, entry_name, _mutate, on_commit=commit
            ),
            _t,
        )
        return {"success": True, asset_type: result}

    @router.post(f"{base}/{{derivative_name}}/rename")
    async def rename_derivative(  # pyright: ignore[reportUnusedFunction]
        project_name: str,
        entry_name: str,
        derivative_name: str,
        req: _DerivativeRenameRequest,
        _t: Translator,
    ):
        """改名是一次级联事务：本体条目内的键，与全部剧本 / 草稿里的 ``本体名/旧衍生名`` 引用。"""
        new_name = _validated_name(req.new_name)

        def _write(manager: ProjectManager, _commit: Callable[[Path], None]) -> dict[str, Any]:
            try:
                return manager.rename_asset_derivative(asset_type, project_name, entry_name, derivative_name, new_name)
            except AssetRenameNotFoundError as exc:
                raise _DerivativeMissing(derivative_name) from exc
            except AssetRenameConflictError as exc:
                raise _DerivativeExists(exc.conflict_name) from exc

        result = await _run(project_name, entry_name, _write, _t)
        return {"success": True, asset_type: result}

    @router.delete(f"{base}/{{derivative_name}}")
    async def delete_derivative(  # pyright: ignore[reportUnusedFunction]
        project_name: str,
        entry_name: str,
        derivative_name: str,
        _t: Translator,
    ):
        deleted: list[str] = []

        def _mutate(entry: dict[str, Any]) -> None:
            table = ensure_derivative_table(entry)
            key = resolve_asset_key(table, derivative_name)
            if key is None:
                raise _DerivativeMissing(derivative_name)
            del table[key]
            deleted.append(key)

        def _purge(manager: ProjectManager, owner_key: str) -> None:
            purge_derivative_sheets(manager.get_project_path(project_name), owner_key, deleted)

        await _run(
            project_name,
            entry_name,
            lambda manager, commit: manager.update_asset_entry(
                asset_type, project_name, entry_name, _mutate, on_commit=commit
            ),
            _t,
            _purge,
        )
        return {"success": True, "message": _t("asset_derivative_deleted", name=derivative_name)}
