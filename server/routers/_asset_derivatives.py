"""Derivative subresource routes under asset entries, registered only for types enabling ``AssetSpec.supports_derivatives``.

Derivatives live under the base entry’s ``DERIVATIVES_FIELD``, sharing base name/identity and unique only within that entry;
references use ``本体名/衍生名``. Add, describe and delete through ``ProjectManager.update_asset_entry``, sharing project locks
and formal-artifact boundaries with base-field PATCH. Renaming cascades ``本体名/旧衍生名`` script references through
``ProjectManager.rename_asset_derivative`` transactions.

This module only registers (add, describe, delete) and renames; generation, versions and currency belong elsewhere.
Renaming/deletion are exceptions because image coordinates contain derivative names. Renaming moves images, history and
manifest keys (``lib.project.asset_derivative_rename``); deletion moves all three into project trash
(``lib.project.asset_trash``), committed together with registration writes."""

from __future__ import annotations

import logging
from collections.abc import Callable
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from lib.infra.api_errors import NotFoundError, UnprocessableError
from lib.infra.async_thread import run_sync_transaction
from lib.project import asset_trash
from lib.project.asset_derivatives import ensure_derivative_table, find_derivative_name
from lib.project.asset_rename import (
    AssetRenameConflictError,
    AssetRenameFileCollisionError,
    AssetRenameHistoryCollisionError,
    AssetRenameNotFoundError,
)
from lib.project.asset_types import (
    ASSET_SPECS,
    DERIVATIVES_FIELD,
    AssetSpec,
    resolve_asset_key,
    validate_asset_name,
)
from lib.project.project_change_hints import project_change_source
from lib.project.project_manager import ProjectManager
from server.i18n import Translator

logger = logging.getLogger(__name__)


class _DerivativeCreateRequest(BaseModel):
    """Add a derivative: the name is unique within its base and the description contains appearance changes relative to the base."""

    name: str
    description: str = ""


class _DerivativeUpdateRequest(BaseModel):
    """Update a derivative description. Renaming uses ``/rename`` and generation writes image paths; neither belongs here."""

    description: str


class _DerivativeRenameRequest(BaseModel):
    new_name: str


class _DerivativeExists(Exception):
    """Another derivative under the same base already uses the target name, including case-only differences."""

    def __init__(self, name: str):
        self.name = name
        super().__init__(name)


class _DerivativeMissing(Exception):
    """The named derivative does not exist in its base’s derivative table."""

    def __init__(self, name: str):
        self.name = name
        super().__init__(name)


def _validated_name(raw: str) -> str:
    """Validate derivative names like asset names, because they also form a single filename component and route parameter."""
    try:
        return validate_asset_name(raw)
    except ValueError as exc:
        raise UnprocessableError("asset_derivative_invalid_name", name=raw) from exc


def _resolve_owner_key(manager: ProjectManager, asset_type: str, project_name: str, entry_name: str) -> str | None:
    """Get the base entry’s actual persisted name; return ``None`` if absent so subsequent writes uniformly report 404."""
    bucket = manager.load_project(project_name).get(ASSET_SPECS[asset_type].bucket_key)
    return resolve_asset_key(bucket, entry_name)


def register_derivative_routes(
    router: APIRouter,
    *,
    spec: AssetSpec,
    not_found_key: str,
    pm_getter: Callable[[], ProjectManager],
) -> None:
    """Register the four derivative subresource endpoints on this asset type’s router."""

    base = f"/projects/{{project_name}}/{spec.subdir}/{{entry_name}}/{DERIVATIVES_FIELD}"
    asset_type = spec.asset_type

    async def _run(
        project_name: str,
        entry_name: str,
        write: Callable[[ProjectManager, Callable[[Path], None]], dict[str, Any]],
        _t: Translator,
        on_commit: Callable[[ProjectManager, str], None] | None = None,
    ) -> dict[str, Any]:
        """Execute a derivative write and map domain exceptions to user-facing responses.

        Renaming uses cascading ``rename_asset_derivative`` transactions; the other three endpoints use ``update_asset_entry``.
        Both paths share exception mapping rather than duplicating it per endpoint. ``on_commit`` receives the actual persisted
        base name in the same commit, letting deletion clean up its image."""
        try:

            def _sync():
                manager = pm_getter()
                with project_change_source("webui"):
                    # Resolve the base’s persisted name once before writing: cascading cleanup builds derivative directories from it,
                    # while request names may use another equivalent encoding form.
                    owner_key = _resolve_owner_key(manager, asset_type, project_name, entry_name)

                    def _commit(_project_file: Path) -> None:
                        if on_commit is not None and owner_key is not None:
                            on_commit(manager, owner_key)

                    return write(manager, _commit)

            return await run_sync_transaction(_sync)
        except _DerivativeExists as exc:
            raise UnprocessableError("asset_derivative_already_exists", name=exc.name) from exc
        except _DerivativeMissing as exc:
            raise NotFoundError("asset_derivative_not_found", name=exc.name) from exc
        except AssetRenameFileCollisionError as exc:
            # Derivative and base images share these conflict messages: refusal shapes and intended user actions are identical.
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

    @router.post(base)
    async def add_derivative(
        project_name: str,
        entry_name: str,
        req: _DerivativeCreateRequest,
        _t: Translator,
    ):
        name = _validated_name(req.name)

        def _mutate(entry: dict[str, Any]) -> None:
            table = ensure_derivative_table(entry)
            existing = find_derivative_name(table, name)
            if existing is not None:
                raise _DerivativeExists(existing)
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
    async def update_derivative(
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
    async def rename_derivative(
        project_name: str,
        entry_name: str,
        derivative_name: str,
        req: _DerivativeRenameRequest,
        _t: Translator,
    ):
        """Renaming is one cascading transaction covering the base-entry key and ``本体名/旧衍生名`` references in all scripts/drafts."""
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
    async def delete_derivative(
        project_name: str,
        entry_name: str,
        derivative_name: str,
        _t: Translator,
    ):
        deleted: list[str] = []
        before: dict[str, bytes] = {}

        def _write(manager: ProjectManager, commit: Callable[[Path], None]) -> dict[str, Any]:
            project_dir = manager.get_project_path(project_name)

            def _mutate(entry: dict[str, Any]) -> None:
                table = ensure_derivative_table(entry)
                key = resolve_asset_key(table, derivative_name)
                if key is None:
                    raise _DerivativeMissing(derivative_name)
                before.update(asset_trash.state_before(project_dir))
                del table[key]
                deleted.append(key)

            return manager.update_asset_entry(
                asset_type,
                project_name,
                entry_name,
                _mutate,
                on_commit=commit,
                formal_paths=asset_trash.state_paths(project_dir),
            )

        def _trash(manager: ProjectManager, owner_key: str) -> None:
            asset_trash.trash_asset_derivative(manager.get_project_path(project_name), owner_key, deleted[0], before)

        await _run(project_name, entry_name, _write, _t, _trash)
        return {"success": True, "message": _t("asset_derivative_deleted", name=derivative_name)}
