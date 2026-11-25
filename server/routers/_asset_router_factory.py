"""项目级资产 CRUD 路由的统一工厂（character / scene / prop / product）。

按 lib.project.asset_types.ASSET_SPECS 驱动，各类资产共用同一份路由模板。每类资产仅用 5 行
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

from lib.generation.asset_admission import AssetTarget, asset_admission_locks, check_asset_write
from lib.infra.api_errors import ApiError, NotFoundError, UnprocessableError
from lib.infra.async_thread import EventLoopBridge, run_sync_transaction
from lib.project import asset_trash
from lib.project.asset_merge import (
    MERGEABLE_ASSET_TYPES,
    AssetMergeNotFoundError,
    AssetMergeRejectedError,
    AssetMergeReport,
)
from lib.project.asset_rename import (
    AssetRenameConflictError,
    AssetRenameFileCollisionError,
    AssetRenameHistoryCollisionError,
    AssetRenameNotFoundError,
    foreign_named_file_field,
)
from lib.project.asset_types import (
    ALIASES_FIELD,
    ASSET_SPECS,
    DERIVATIVES_FIELD,
    AssetSpec,
    ProjectAssetNameConflictError,
    localize_asset_type,
    record_asset_aliases,
    validate_asset_name,
)
from lib.project.project_change_hints import project_change_source
from lib.project.project_manager import ProjectManager
from server.i18n import Translator
from server.routers._asset_derivative_status import register_derivative_status_routes
from server.routers._asset_derivatives import register_derivative_routes
from server.routers._asset_prompt_preview import register_asset_prompt_preview_routes
from server.services.project.asset_identity import delete_project_asset, merge_project_asset, rename_project_asset

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


def _require_owned_files(spec: AssetSpec, name: str, entry: Mapping[str, Any], values: Mapping[str, Any]) -> None:
    """资产图、参考图与参考音频只能指向本资产名下的文件：共用别的资产的文件，改名、删除与恢复之后会悬空。"""
    field = foreign_named_file_field(spec, name, entry, values)
    if field is not None:
        key = "asset_sheet_file_not_owned" if field == spec.sheet_field else "asset_reference_file_not_owned"
        raise UnprocessableError(key, name=name).with_diagnostic(
            f"field '{field}' must point at a file named after the asset"
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


class _MergeRequest(BaseModel):
    """资产合并请求体：把路径里的资产并入 ``target``。``dry_run=True`` 只返回按集列出的影响预览。"""

    model_config = ConfigDict(extra="forbid")

    target: str
    as_derivative: bool = False
    dry_run: bool = False


_MERGE_REJECTION_KEYS: dict[str, str] = {
    "same_asset": "asset_merge_same_asset",
    "derivative_needs_character": "asset_merge_derivative_needs_character",
}


def merge_report_payload(report: AssetMergeReport) -> dict[str, Any]:
    """合并资产路由的响应体：预览与执行共用同一份字段。"""
    return {
        "dry_run": report.dry_run,
        "source": report.source,
        "target": report.target,
        "as_derivative": report.as_derivative,
        "aliases_added": list(report.aliases_added),
        "derivative_created": report.derivative_created,
        "derivatives_moved": list(report.derivatives_moved),
        "derivatives_folded": list(report.derivatives_folded),
        "references": report.references,
        "episodes": [
            {
                "episode": item.episode,
                "script_plan": item.script_plan,
                "script": item.script,
                "draft": item.draft,
                "prompt_text": item.prompt_text,
                "speaker": item.speaker,
                "storyboards": item.storyboards,
                "videos": item.videos,
            }
            for item in report.episodes
        ],
    }


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
    image_fields = (spec.sheet_field, *spec.original_image_fields)
    voice_fields = ("reference_audio",) if asset_type == "character" else ()

    router = APIRouter()
    register_asset_prompt_preview_routes(router, spec=spec, pm_getter=pm_getter)

    @router.post(f"/projects/{{project_name}}/{spec.subdir}")
    async def add_entry(
        project_name: str,
        req: _CreateRequest,
        _t: Translator,
    ):
        try:
            name = validate_asset_name(req.name)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=_t("asset_invalid_name", name=req.name)) from exc
        extras = req.model_extra or {}
        for field in spec.extra_string_fields:
            value = extras.get(field)
            if value is None:
                extras.pop(field, None)
            elif not isinstance(value, str):
                raise _not_a_string(field)
            elif field == "voice_notice_dismissed_at":
                raise UnprocessableError("asset_voice_notice_dismissed_at_stale")
        _require_string_list_fields(extras, spec.extra_list_fields)
        _require_owned_files(spec, name, {}, extras)
        try:

            def _sync():
                manager = pm_getter()
                entry: dict[str, Any] = {"description": req.description, spec.sheet_field: ""}
                for field in spec.extra_string_fields:
                    entry[field] = extras.get(field, "")
                if entry.get("reference_audio"):
                    entry["voice_updated_at"] = datetime.now(UTC).isoformat()
                for field in spec.extra_list_fields:
                    entry[field] = list(extras.get(field) or [])
                if spec.supports_derivatives:
                    entry[DERIVATIVES_FIELD] = {}
                with project_change_source("webui"):
                    ok = manager._add_asset(asset_type, project_name, name, entry)
                if not ok:
                    raise HTTPException(status_code=409, detail=_t(keys["exists"], name=name))
                data = manager.load_project(project_name)
                trash_record = asset_trash.deleted_asset_record(
                    manager.get_project_path(project_name), asset_type, name
                )
                return {"success": True, result_key: data[spec.bucket_key][name], "trash_record_id": trash_record}

            return await run_sync_transaction(_sync)
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
    async def update_entry(
        project_name: str,
        entry_name: str,
        req: dict[str, Any],
        _t: Translator,
    ):
        for field in update_fields:
            value = req.get(field)
            if value is not None and not isinstance(value, str):
                raise _not_a_string(field)
        _require_string_list_fields(req, update_list_fields)

        bridge = EventLoopBridge.capture()
        try:

            def _sync():
                manager = pm_getter()

                def _mutate(entry: dict) -> None:
                    _require_owned_files(spec, entry_name, entry, req)
                    image_changed = any(
                        req.get(field) is not None and req[field] != entry.get(field) for field in image_fields
                    )
                    voice_changed = any(
                        req.get(field) is not None and req[field] != entry.get(field) for field in voice_fields
                    )
                    if image_changed or voice_changed:
                        dimension = (
                            "whole_asset" if image_changed and voice_changed else "image" if image_changed else "voice"
                        )
                        bridge.run(check_asset_write(project_name, AssetTarget(asset_type, entry_name, dimension)))
                    for field in (*update_fields, *update_list_fields):
                        if req.get(field) is not None:
                            if field == "voice_notice_dismissed_at" and req[field] != entry.get("voice_updated_at"):
                                raise _InvalidFieldValue(field)
                            if field == "reference_audio" and req[field] != entry.get("reference_audio"):
                                entry["voice_updated_at"] = datetime.now(UTC).isoformat()
                            if field == ALIASES_FIELD:
                                entry[field] = []
                                record_asset_aliases(entry, req[field], asset_name=entry_name)
                                continue
                            entry[field] = req[field]

                with project_change_source("webui"):
                    result = manager.update_asset_entry(asset_type, project_name, entry_name, _mutate)
                return {"success": True, result_key: result}

            if any(req.get(field) is not None for field in (*image_fields, *voice_fields)):
                async with asset_admission_locks(project_name, AssetTarget(asset_type, entry_name, "whole_asset")):
                    return await run_sync_transaction(_sync)
            return await run_sync_transaction(_sync)
        except ProjectAssetNameConflictError as exc:
            raise HTTPException(status_code=409, detail=localize_project_asset_name_conflict(exc, _t)) from exc
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=_t(keys["not_found"], name=entry_name)) from exc
        except _InvalidFieldValue as exc:
            if exc.field == "voice_notice_dismissed_at":
                raise UnprocessableError("asset_voice_notice_dismissed_at_stale") from exc
            raise UnprocessableError("asset_field_invalid_value").with_diagnostic(
                f"field '{exc.field}' has an invalid value"
            ) from exc
        except FileNotFoundError as exc:
            raise NotFoundError("project_not_found", name=project_name) from exc
        except (HTTPException, ApiError):
            raise
        except Exception as exc:
            logger.exception("请求处理失败")
            raise HTTPException(status_code=500, detail=_t("internal_server_error")) from exc

    @router.post(f"/projects/{{project_name}}/{spec.subdir}/{{entry_name}}/rename")
    async def rename_entry(
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
            with project_change_source("webui"):
                report = await rename_project_asset(
                    pm_getter(), project_name, spec.bucket_key, entry_name, req.new_name, dry_run=req.dry_run
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
            logger.exception("资产重命名被校验拒绝")
            raise HTTPException(status_code=422, detail=_t("asset_rename_rejected", name=entry_name)) from exc
        except (HTTPException, ApiError):
            raise
        except Exception as exc:
            logger.exception("请求处理失败")
            raise HTTPException(status_code=500, detail=_t("internal_server_error")) from exc

    if asset_type in MERGEABLE_ASSET_TYPES:

        @router.post(f"/projects/{{project_name}}/{spec.subdir}/{{entry_name}}/merge")
        async def merge_entry(
            project_name: str,
            entry_name: str,
            req: _MergeRequest,
            _t: Translator,
        ):
            """把该资产并入同类型的 ``target``（dry_run 形态即影响预览，与执行共用同一次扫描）。"""
            try:
                with project_change_source("webui"):
                    report = await merge_project_asset(
                        pm_getter(),
                        project_name,
                        spec.bucket_key,
                        entry_name,
                        req.target,
                        as_derivative=req.as_derivative,
                        dry_run=req.dry_run,
                    )
                return {"success": True, **merge_report_payload(report)}
            except AssetMergeNotFoundError as exc:
                raise HTTPException(status_code=404, detail=_t(keys["not_found"], name=exc.name)) from exc
            except AssetMergeRejectedError as exc:
                raise HTTPException(
                    status_code=422, detail=_t(_MERGE_REJECTION_KEYS[exc.reason], name=entry_name)
                ) from exc
            except AssetRenameFileCollisionError as exc:
                raise HTTPException(
                    status_code=409,
                    detail=_t("asset_merge_derivative_conflict", target=req.target, name=exc.destination.stem),
                ) from exc
            except AssetRenameHistoryCollisionError as exc:
                raise HTTPException(
                    status_code=409,
                    detail=_t(
                        "asset_merge_derivative_conflict",
                        target=req.target,
                        name=exc.resource_id.rpartition("/")[2],
                    ),
                ) from exc
            except FileNotFoundError as exc:
                raise NotFoundError("project_not_found", name=project_name) from exc
            except ValueError as exc:
                logger.exception("资产合并被校验拒绝")
                raise HTTPException(status_code=422, detail=_t("asset_merge_rejected", name=entry_name)) from exc
            except (HTTPException, ApiError):
                raise
            except Exception as exc:
                logger.exception("请求处理失败")
                raise HTTPException(status_code=500, detail=_t("internal_server_error")) from exc

    @router.delete(f"/projects/{{project_name}}/{spec.subdir}/{{entry_name}}")
    async def delete_entry(project_name: str, entry_name: str, _t: Translator, dry_run: bool = False):
        """删除资产；``dry_run=true`` 只返回按集列出的引用数（与重命名同一套扫描），不改动任何数据。

        删除不改写脚本里的引用，也不因有引用而拒绝：预览只用于删除前告知影响。
        """
        try:
            manager = pm_getter()
            if dry_run:
                preview = await asyncio.to_thread(
                    manager.preview_asset_deletion, project_name, spec.bucket_key, entry_name
                )
                return {
                    "success": True,
                    "dry_run": True,
                    "name": preview.name,
                    "references": preview.references,
                    "episodes": [{"episode": item.episode, "references": item.references} for item in preview.episodes],
                }

            with project_change_source("webui"):
                await delete_project_asset(manager, project_name, spec.bucket_key, entry_name)
            return {"success": True, "message": _t(keys["deleted"], name=entry_name)}
        except ProjectAssetNameConflictError as exc:
            raise HTTPException(status_code=409, detail=localize_project_asset_name_conflict(exc, _t)) from exc
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=_t(keys["not_found"], name=entry_name)) from exc
        except FileNotFoundError as exc:
            raise NotFoundError("project_not_found", name=project_name) from exc
        except (HTTPException, ApiError):
            raise
        except Exception as exc:
            logger.exception("请求处理失败")
            raise HTTPException(status_code=500, detail=_t("internal_server_error")) from exc

    if spec.supports_derivatives:
        register_derivative_routes(router, spec=spec, not_found_key=keys["not_found"], pm_getter=pm_getter)
        register_derivative_status_routes(router, spec=spec, not_found_key=keys["not_found"], pm_getter=pm_getter)

    return router
