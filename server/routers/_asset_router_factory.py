"""Shared project-asset CRUD route factory for character / scene / prop / product.

lib.project.asset_types.ASSET_SPECS drives one route template across asset types. Enable each type in only 5 lines:

    router = build_asset_router(asset_type="character", pm_getter=lambda: get_project_manager())

Derive URL segments, bucket keys, sheet fields and PATCH allowlists from spec
(description + sheet_field + extra_string_fields + extra_list_fields). _I18N_KEYS maintains naming differences,
such as the historical scene prefix "project_scene_*".

Types enabling ``spec.supports_derivatives``, currently character only, also register derivative subresources;
see server/routers/_asset_derivatives.py."""

from __future__ import annotations

import logging
from collections.abc import Callable, Iterable, Mapping
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict

from lib.infra.api_errors import NotFoundError, UnprocessableError
from lib.infra.async_thread import run_sync_transaction
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
    """List fields require string lists; omission/None counts as absent, leaving caller-specific defaults."""
    for field in fields:
        value = values.get(field)
        if value is not None and not _is_string_list(value):
            raise UnprocessableError("asset_field_must_be_string_list").with_diagnostic(
                f"field '{field}' must be a list of strings"
            )


def _require_owned_files(spec: AssetSpec, name: str, entry: Mapping[str, Any], values: Mapping[str, Any]) -> None:
    """Asset images, references and reference audio may point only to this asset’s files; sharing other assets’ files leaves dangling paths after rename/delete/restore."""
    field = foreign_named_file_field(spec, name, entry, values)
    if field is not None:
        key = "asset_sheet_file_not_owned" if field == spec.sheet_field else "asset_reference_file_not_owned"
        raise UnprocessableError(key, name=name).with_diagnostic(
            f"field '{field}' must point at a file named after the asset"
        )


class _InvalidFieldValue(Exception):
    """A PATCH field failed business validation, distinct from type errors rejected with 422 at the boundary."""

    def __init__(self, field: str):
        self.field = field
        super().__init__(field)


class _RenameRequest(BaseModel):
    """Cascade-rename request. ``dry_run=True`` returns only impact previews: updated episode/reference/file counts."""

    new_name: str
    dry_run: bool = False


class _MergeRequest(BaseModel):
    """Asset-merge request: merge the path asset into ``target``; ``dry_run=True`` returns only per-episode impact previews."""

    model_config = ConfigDict(extra="forbid")

    target: str
    as_derivative: bool = False
    dry_run: bool = False


#: Merge rejection reason → i18n key. Nonmergeable types register no merge routes and never reach this mapping.
_MERGE_REJECTION_KEYS: dict[str, str] = {
    "same_asset": "asset_merge_same_asset",
    "derivative_needs_character": "asset_merge_derivative_needs_character",
}


def merge_report_payload(report: AssetMergeReport) -> dict[str, Any]:
    """Asset-merge response fields shared by preview and execution."""
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
    """Creation request accepting extra fields according to asset type."""

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
    """Build project-asset CRUD routes for one type.

    pm_getter should be a lambda resolving get_project_manager dynamically per call so monkeypatch tests work."""
    if asset_type not in ASSET_SPECS:
        raise ValueError(f"unknown asset_type: {asset_type}")
    spec = ASSET_SPECS[asset_type]
    keys = _I18N_KEYS[asset_type]
    result_key = asset_type
    update_fields: tuple[str, ...] = ("description", spec.sheet_field, *spec.extra_string_fields)
    update_list_fields: tuple[str, ...] = spec.extra_list_fields

    router = APIRouter()
    register_asset_prompt_preview_routes(router, spec=spec, pm_getter=pm_getter)

    @router.post(f"/projects/{{project_name}}/{spec.subdir}")
    async def add_entry(
        project_name: str,
        req: _CreateRequest,
        _t: Translator,
    ):
        # Names form paths/single-segment route parameters; reject unsafe names at the boundary,
        # otherwise generation and named PATCH/DELETE/{name} access would fail.
        try:
            name = validate_asset_name(req.name)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=_t("asset_invalid_name", name=req.name)) from exc
        extras = req.model_extra or {}
        # Validate string fields (voice_style / reference_image / reference_audio) and list fields
        # (reference_images / selling_points) at creation, rejecting invalid types with 422
        # before project.json pollution. PATCH already validates equally (update_fields
        # validation); creation previously omitted it and extra="allow" admitted arbitrary JSON types.
        for field in spec.extra_string_fields:
            value = extras.get(field)
            if value is None:
                # Treat None as absent: allow it but remove it from extras, otherwise
                # entry[field] = extras.get(field, "") cannot apply its default
                # because dict.get does not default for a present None value,
                # writing None rather than an empty string and violating
                # the persisted string-only contract.
                extras.pop(field, None)
            elif not isinstance(value, str):
                raise _not_a_string(field)
            elif field == "voice_notice_dismissed_at":
                # New characters lack voice_updated_at, so PATCH’s equality-to-current validation
                # cannot succeed here. Reject this field on creation to prevent bypassing
                # PATCH equality with far-future timestamps permanently suppressing legacy transition banners. Users may
                # trigger this by copying serialized existing characters into creation requests; like PATCH
                # validation of the same field, translate the error.
                raise UnprocessableError("asset_voice_notice_dismissed_at_stale")
        _require_string_list_fields(extras, spec.extra_list_fields)
        _require_owned_files(spec, name, {}, extras)
        try:

            def _sync():
                manager = pm_getter()
                entry: dict[str, Any] = {"description": req.description, spec.sheet_field: ""}
                for field in spec.extra_string_fields:
                    entry[field] = extras.get(field, "")
                # Creating with reference_audio must also stamp voice_updated_at for the same reason
                # as PATCH: otherwise banners on existing character clips never detect this
                # newly configured voice.
                if entry.get("reference_audio"):
                    entry["voice_updated_at"] = datetime.now(UTC).isoformat()
                for field in spec.extra_list_fields:
                    entry[field] = list(extras.get(field) or [])
                # Register derivatives only through subresources; ignore creation-body derivative fields and always initialize empty tables.
                if spec.supports_derivatives:
                    entry[DERIVATIVES_FIELD] = {}
                with project_change_source("webui"):
                    ok = manager._add_asset(asset_type, project_name, name, entry)
                if not ok:
                    raise HTTPException(status_code=409, detail=_t(keys["exists"], name=name))
                data = manager.load_project(project_name)
                # Tell frontend about same-named trash records so creators can recover prior asset images.
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
        # Validate all writable fields before persistence. req is dict[str, Any]; wrong client types
        # would pollute project.json and raise TypeError downstream, such as joining reference_image
        # paths in execute_character_task. Reject with 422; strings require str and lists require string lists.
        for field in update_fields:
            value = req.get(field)
            if value is not None and not isinstance(value, str):
                raise _not_a_string(field)
        _require_string_list_fields(req, update_list_fields)

        try:

            def _sync():
                manager = pm_getter()

                def _mutate(entry: dict) -> None:
                    _require_owned_files(spec, entry_name, entry, req)
                    for field in (*update_fields, *update_list_fields):
                        if req.get(field) is not None:
                            # voice_notice_dismissed_at identifies the acknowledged voice version and must copy
                            # this character’s voice_updated_at unchanged; arbitrary strings could inject
                            # far-future timestamps permanently suppressing legacy transition banners.
                            if field == "voice_notice_dismissed_at" and req[field] != entry.get("voice_updated_at"):
                                raise _InvalidFieldValue(field)
                            # Generic PATCH changes to reference_audio must refresh voice_updated_at:
                            # this writable path would otherwise change voices without letting legacy
                            # transition banners detect changes or reappear after dismissal.
                            if field == "reference_audio" and req[field] != entry.get("reference_audio"):
                                entry["voice_updated_at"] = datetime.now(UTC).isoformat()
                            # Normalize aliases using name equality: trim, deduplicate and remove aliases equal to the asset name.
                            if field == ALIASES_FIELD:
                                entry[field] = []
                                record_asset_aliases(entry, req[field], asset_name=entry_name)
                                continue
                            entry[field] = req[field]

                with project_change_source("webui"):
                    result = manager.update_asset_entry(asset_type, project_name, entry_name, _mutate)
                return {"success": True, result_key: result}

            return await run_sync_transaction(_sync)
        except ProjectAssetNameConflictError as exc:
            raise HTTPException(status_code=409, detail=localize_project_asset_name_conflict(exc, _t)) from exc
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=_t(keys["not_found"], name=entry_name)) from exc
        except _InvalidFieldValue as exc:
            # Unlike adjacent type-validation 422 ("field ... must be a string"), this path needs no
            # deliberately invalid request: a voice update after banner rendering but before the user
            # dismisses it triggers a real user-visible error, so translate it.
            if exc.field == "voice_notice_dismissed_at":
                raise UnprocessableError("asset_voice_notice_dismissed_at_stale") from exc
            raise UnprocessableError("asset_field_invalid_value").with_diagnostic(
                f"field '{exc.field}' has an invalid value"
            ) from exc
        except FileNotFoundError as exc:
            raise NotFoundError("project_not_found", name=project_name) from exc
        except HTTPException:
            raise
        except UnprocessableError:
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
        """Cascade rename, or impact preview with dry_run; preview/execution share scans, so counts necessarily match."""
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

            return await run_sync_transaction(_sync)
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
            # Rare structural nonworsening rejection: nothing persisted; suggest retry or project-data inspection.
            logger.exception("资产重命名被校验拒绝")
            raise HTTPException(status_code=422, detail=_t("asset_rename_rejected", name=entry_name)) from exc
        except HTTPException:
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
            """Merge this asset into same-type ``target``; dry_run previews impact using the same scan as execution."""
            try:

                def _sync():
                    manager = pm_getter()
                    with project_change_source("webui"):
                        report = manager.merge_asset(
                            project_name,
                            spec.bucket_key,
                            entry_name,
                            req.target,
                            as_derivative=req.as_derivative,
                            dry_run=req.dry_run,
                        )
                    return {"success": True, **merge_report_payload(report)}

                return await run_sync_transaction(_sync)
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
                # Rare structural nonworsening rejection: nothing persisted; suggest retry or project-data inspection.
                logger.exception("资产合并被校验拒绝")
                raise HTTPException(status_code=422, detail=_t("asset_merge_rejected", name=entry_name)) from exc
            except HTTPException:
                raise
            except Exception as exc:
                logger.exception("请求处理失败")
                raise HTTPException(status_code=500, detail=_t("internal_server_error")) from exc

    @router.delete(f"/projects/{{project_name}}/{spec.subdir}/{{entry_name}}")
    async def delete_entry(project_name: str, entry_name: str, _t: Translator, dry_run: bool = False):
        """Delete an asset; ``dry_run=true`` returns only per-episode reference counts using rename scans, without changing data.

        Deletion neither rewrites script references nor rejects referenced assets; preview only informs users of the impact first."""
        try:

            def _sync():
                manager = pm_getter()
                if dry_run:
                    preview = manager.preview_asset_deletion(project_name, spec.bucket_key, entry_name)
                    return {
                        "success": True,
                        "dry_run": True,
                        "name": preview.name,
                        "references": preview.references,
                        "episodes": [
                            {"episode": item.episode, "references": item.references} for item in preview.episodes
                        ],
                    }

                with project_change_source("webui"):
                    manager.delete_asset(project_name, spec.bucket_key, entry_name)
                return {"success": True, "message": _t(keys["deleted"], name=entry_name)}

            return await run_sync_transaction(_sync)
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
