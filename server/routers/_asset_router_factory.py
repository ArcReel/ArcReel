"""项目级资产 CRUD 路由的统一工厂（character / scene / prop / product）。

按 lib.asset_types.ASSET_SPECS 驱动，各类资产共用同一份路由模板。每类资产仅用 5 行
启用：

    router = build_asset_router(asset_type="character", pm_getter=lambda: get_project_manager())

工厂内部从 spec 解析 URL 路径段、bucket key、sheet 字段、PATCH 字段白名单
（description + sheet_field + extra_string_fields + extra_list_fields）。i18n key
命名差异（scene 用历史前缀 "project_scene_*"）通过 _I18N_KEYS 表维护。

``spec.supports_derivatives`` 开启的类型（当前只有 character）额外挂上衍生子资源路由，
见 server/routers/_asset_derivatives.py。
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable, Iterable, Mapping
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict

from lib.api_errors import NotFoundError, UnprocessableError
from lib.asset_rename import (
    AssetRenameConflictError,
    AssetRenameFileCollisionError,
    AssetRenameHistoryCollisionError,
    AssetRenameNotFoundError,
)
from lib.asset_types import (
    ASSET_SPECS,
    DERIVATIVES_FIELD,
    ProjectAssetNameConflictError,
    localize_asset_type,
    validate_asset_name,
)
from lib.i18n import Translator
from lib.project_change_hints import project_change_source
from lib.project_manager import ProjectManager
from server.routers._asset_derivative_status import register_derivative_status_routes
from server.routers._asset_derivatives import register_derivative_routes

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


def _not_a_string(field: str) -> UnprocessableError:
    return UnprocessableError("asset_field_must_be_string").with_diagnostic(f"field '{field}' must be a string")


def _require_string_list_fields(values: Mapping[str, Any], fields: Iterable[str]) -> None:
    """列表字段须是字符串列表；缺省与 None 视同未提供，交由调用方各自的默认值处理。"""
    for field in fields:
        value = values.get(field)
        if value is not None and not _is_string_list(value):
            raise UnprocessableError("asset_field_must_be_string_list").with_diagnostic(
                f"field '{field}' must be a list of strings"
            )


class _InvalidFieldValue(Exception):
    """PATCH 请求体中某字段的值未通过业务校验（区别于类型校验，类型错误已在边界 422）。"""

    def __init__(self, field: str):
        self.field = field
        super().__init__(field)


class _RenameRequest(BaseModel):
    """级联重命名请求体。``dry_run=True`` 只返回影响预览（将更新的集数/引用处数/文件数）。"""

    new_name: str
    dry_run: bool = False


class _CreateRequest(BaseModel):
    """创建请求体。按资产类型接受不同的额外字段。"""

    model_config = ConfigDict(extra="allow")

    name: str
    description: str = ""


def localize_project_asset_name_conflict(exc: ProjectAssetNameConflictError, translate: Translator) -> str:
    return translate(
        "project_asset_name_conflict",
        name=exc.name,
        requested_type=localize_asset_type(exc.requested_asset_type or exc.existing.asset_type, translate),
        existing_type=localize_asset_type(exc.existing.asset_type, translate),
        existing_name=exc.existing.name,
    )


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

    # 以下四个处理器由 @router.* 就地注册，模块内无其它引用；basedpyright 把函数作用域内的符号
    # 一律判为私有，逐个标注的 reportUnusedFunction 均为工具误报。
    @router.post(f"/projects/{{project_name}}/{spec.subdir}")
    async def add_entry(  # pyright: ignore[reportUnusedFunction]
        project_name: str,
        req: _CreateRequest,
        _t: Translator,
    ):
        # 名称会被拼进文件路径与单段路由参数，路径不安全的名字在边界即拒绝，
        # 否则后续生成与按名访问（PATCH/DELETE/{name}）全部失效。
        try:
            name = validate_asset_name(req.name)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=_t("asset_invalid_name", name=req.name)) from exc
        extras = req.model_extra or {}
        # 字符串字段（voice_style / reference_image / reference_audio 等）与列表字段
        # （reference_images / selling_points 等）在创建时即校验类型，非法类型 422 在
        # 边界拦截，避免污染 project.json——PATCH 路径已做同等校验（见 update_fields
        # 校验），create 路径此前遗漏，extra="allow" 下调用方可传任意 JSON 类型。
        for field in spec.extra_string_fields:
            value = extras.get(field)
            if value is None:
                # None 视同未提供：不拒绝，但要从 extras 摘除，否则下面
                # entry[field] = extras.get(field, "") 的默认值不生效
                # （字段存在但值为 None 时 dict.get 不会回退到默认值），
                # 写入 project.json 的会是 None 而非空字符串，破坏该字段
                # 「必为字符串」的持久化契约。
                extras.pop(field, None)
            elif not isinstance(value, str):
                raise _not_a_string(field)
            elif field == "voice_notice_dismissed_at":
                # 新建角色尚无 voice_updated_at，PATCH 侧「必须等于当前 voice_updated_at」的
                # 校验在此处恒不成立（值不存在）；直接拒绝创建时携带该字段，防止绕过
                # PATCH 的等值校验写入远未来时间戳，永久压制存量过渡横幅。真实用户可能
                # 触发（如把已有角色的序列化结果整体复制进创建请求体），与 PATCH 侧的
                # 同名校验一样须走翻译。
                raise UnprocessableError("asset_voice_notice_dismissed_at_stale")
        _require_string_list_fields(extras, spec.extra_list_fields)
        try:

            def _sync():
                manager = pm_getter()
                entry: dict[str, Any] = {"description": req.description, spec.sheet_field: ""}
                for field in spec.extra_string_fields:
                    entry[field] = extras.get(field, "")
                # 创建即携带 reference_audio 时同样须机械戳 voice_updated_at：与 PATCH 侧
                # 同一字段的同一补戳理由一致，否则该角色的存量片段横幅永远感知不到这次
                # 「设置了声音」。
                if entry.get("reference_audio"):
                    entry["voice_updated_at"] = datetime.now(UTC).isoformat()
                for field in spec.extra_list_fields:
                    entry[field] = list(extras.get(field) or [])
                # 衍生只经衍生子资源登记：创建请求体里的同名字段一律不落盘，新条目一律空表。
                if spec.supports_derivatives:
                    entry[DERIVATIVES_FIELD] = {}
                with project_change_source("webui"):
                    ok = manager._add_asset(asset_type, project_name, name, entry)
                if not ok:
                    raise HTTPException(status_code=409, detail=_t(keys["exists"], name=name))
                data = manager.load_project(project_name)
                return {"success": True, result_key: data[spec.bucket_key][name]}

            return await asyncio.to_thread(_sync)
        except ProjectAssetNameConflictError as exc:
            raise HTTPException(status_code=409, detail=localize_project_asset_name_conflict(exc, _t)) from exc
        except FileNotFoundError as exc:
            raise NotFoundError("project_not_found", name=project_name) from exc
        except HTTPException:
            raise
        except Exception as exc:
            logger.exception("请求处理失败")
            raise HTTPException(status_code=500, detail=_t("internal_server_error")) from exc

    @router.patch(f"/projects/{{project_name}}/{spec.subdir}/{{entry_name}}")
    async def update_entry(  # pyright: ignore[reportUnusedFunction]
        project_name: str,
        entry_name: str,
        req: dict[str, Any],
        _t: Translator,
    ):
        # 写入前对所有可写字段做类型校验。req 是 dict[str, Any]，若客户端传入错误类型
        # 会污染 project.json 并在下游 (例如 execute_character_task 拼接 reference_image
        # 路径) 引发 TypeError。422 在边界拦截。字符串字段须为 str，列表字段须为字符串列表。
        for field in update_fields:
            value = req.get(field)
            if value is not None and not isinstance(value, str):
                raise _not_a_string(field)
        _require_string_list_fields(req, update_list_fields)

        try:

            def _sync():
                manager = pm_getter()

                def _mutate(entry: dict) -> None:
                    for field in (*update_fields, *update_list_fields):
                        if req.get(field) is not None:
                            # voice_notice_dismissed_at 语义是「已确认到的声音版本」，必须原样
                            # 回填角色自己的 voice_updated_at；放行任意字符串会让客户端写入
                            # 远未来时间戳，永久压制存量过渡横幅。
                            if field == "voice_notice_dismissed_at" and req[field] != entry.get("voice_updated_at"):
                                raise _InvalidFieldValue(field)
                            # reference_audio 经通用 PATCH 修改时必须同步刷新 voice_updated_at：
                            # 该字段仍在此处的可写集合内，若不补戳，经这条路径改声音会让
                            # 存量过渡横幅感知不到变化，或已关闭后不再重现。
                            if field == "reference_audio" and req[field] != entry.get("reference_audio"):
                                entry["voice_updated_at"] = datetime.now(UTC).isoformat()
                            entry[field] = req[field]

                with project_change_source("webui"):
                    result = manager.update_asset_entry(asset_type, project_name, entry_name, _mutate)
                return {"success": True, result_key: result}

            return await asyncio.to_thread(_sync)
        except ProjectAssetNameConflictError as exc:
            raise HTTPException(status_code=409, detail=localize_project_asset_name_conflict(exc, _t)) from exc
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=_t(keys["not_found"], name=entry_name)) from exc
        except _InvalidFieldValue as exc:
            # 与相邻的类型校验 422（"field ... must be a string"）不同：这条路径不需要
            # 客户端主动构造非法请求即可触发——横幅渲染后声音被再次更新、用户随后才点击
            # 关闭即会触发，是真实用户可能看到的错误，须走翻译。
            if exc.field == "voice_notice_dismissed_at":
                raise UnprocessableError("asset_voice_notice_dismissed_at_stale") from exc
            raise UnprocessableError("asset_field_invalid_value").with_diagnostic(
                f"field '{exc.field}' has an invalid value"
            ) from exc
        except FileNotFoundError as exc:
            raise NotFoundError("project_not_found", name=project_name) from exc
        except HTTPException:
            raise
        except Exception as exc:
            logger.exception("请求处理失败")
            raise HTTPException(status_code=500, detail=_t("internal_server_error")) from exc

    @router.post(f"/projects/{{project_name}}/{spec.subdir}/{{entry_name}}/rename")
    async def rename_entry(  # pyright: ignore[reportUnusedFunction]
        project_name: str,
        entry_name: str,
        req: _RenameRequest,
        _t: Translator,
    ):
        """级联重命名（dry_run 形态即影响预览）：预览与执行共用同一套扫描逻辑，数字必然一致。"""
        try:
            validate_asset_name(req.new_name)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=_t("asset_invalid_name", name=req.new_name)) from exc
        try:

            def _sync():
                manager = pm_getter()
                with project_change_source("webui"):
                    report = manager.rename_asset(
                        project_name, spec.bucket_key, entry_name, req.new_name, dry_run=req.dry_run
                    )
                return {
                    "success": True,
                    "dry_run": report.dry_run,
                    "old_name": report.old_name,
                    "new_name": report.new_name,
                    "episodes": report.episodes,
                    "references": report.references,
                    "files": report.files,
                }

            return await asyncio.to_thread(_sync)
        except AssetRenameNotFoundError as exc:
            raise HTTPException(status_code=404, detail=_t(keys["not_found"], name=entry_name)) from exc
        except ProjectAssetNameConflictError as exc:
            raise HTTPException(status_code=409, detail=localize_project_asset_name_conflict(exc, _t)) from exc
        except AssetRenameConflictError as exc:
            raise HTTPException(status_code=409, detail=_t(keys["exists"], name=exc.conflict_name)) from exc
        except AssetRenameFileCollisionError as exc:
            raise HTTPException(
                status_code=409, detail=_t("asset_rename_file_conflict", filename=exc.destination.name)
            ) from exc
        except AssetRenameHistoryCollisionError as exc:
            raise HTTPException(
                status_code=409, detail=_t("asset_rename_history_conflict", name=exc.resource_id)
            ) from exc
        except FileNotFoundError as exc:
            raise NotFoundError("project_not_found", name=project_name) from exc
        except ValueError as exc:
            # 结构「不更坏」校验拒绝等罕见情形：整体未落盘，提示用户重试或检查项目数据。
            logger.exception("资产重命名被校验拒绝")
            raise HTTPException(status_code=422, detail=_t("asset_rename_rejected", name=entry_name)) from exc
        except HTTPException:
            raise
        except Exception as exc:
            logger.exception("请求处理失败")
            raise HTTPException(status_code=500, detail=_t("internal_server_error")) from exc

    @router.delete(f"/projects/{{project_name}}/{spec.subdir}/{{entry_name}}")
    async def delete_entry(project_name: str, entry_name: str, _t: Translator):  # pyright: ignore[reportUnusedFunction]
        try:

            def _sync():
                manager = pm_getter()

                with project_change_source("webui"):
                    manager.delete_asset(project_name, spec.bucket_key, entry_name)
                return {"success": True, "message": _t(keys["deleted"], name=entry_name)}

            return await asyncio.to_thread(_sync)
        except ProjectAssetNameConflictError as exc:
            raise HTTPException(status_code=409, detail=localize_project_asset_name_conflict(exc, _t)) from exc
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=_t(keys["not_found"], name=entry_name)) from exc
        except FileNotFoundError as exc:
            raise NotFoundError("project_not_found", name=project_name) from exc
        except HTTPException:
            raise
        except Exception as exc:
            logger.exception("请求处理失败")
            raise HTTPException(status_code=500, detail=_t("internal_server_error")) from exc

    if spec.supports_derivatives:
        register_derivative_routes(router, spec=spec, not_found_key=keys["not_found"], pm_getter=pm_getter)
        register_derivative_status_routes(router, spec=spec, not_found_key=keys["not_found"], pm_getter=pm_getter)

    return router
