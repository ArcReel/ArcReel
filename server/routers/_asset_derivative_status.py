"""Read view for derivative asset images: image paths and stale markers.

Registration data (description, name) already accompanies the base entry. Whether a derivative image still matches
the base’s current appearance is absent from project.json: it compares the artifact manifest with canonical state.
Reading files and computing content fingerprints on the hot project-read path is costly, so expose a separate
character-addressed endpoint for views to fetch on demand."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from typing import Any

from fastapi import APIRouter, HTTPException

from lib.artifacts.artifact_activation import active_artifact_currency_resolver
from lib.artifacts.artifact_manifest import ArtifactStatus
from lib.infra.api_errors import NotFoundError
from lib.project.asset_derivatives import derivative_artifact_key, derivative_table
from lib.project.asset_types import DERIVATIVES_FIELD, AssetSpec, asset_name_comparison_key, resolve_asset_key
from lib.project.project_manager import ProjectManager
from server.i18n import Translator

logger = logging.getLogger(__name__)


def register_derivative_status_routes(
    router: APIRouter,
    *,
    spec: AssetSpec,
    not_found_key: str,
    pm_getter: Callable[[], ProjectManager],
) -> None:
    """Register an endpoint for derivative images and stale status under a base name on this asset type’s router."""

    base = f"/projects/{{project_name}}/{spec.subdir}/{{entry_name}}/{DERIVATIVES_FIELD}"

    @router.get(base)
    async def list_derivatives(
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
                artifact_status = ArtifactStatus.MISSING
                if sheet_path:
                    comparison = resolver.compare(
                        derivative_artifact_key(asset_name_comparison_key(owner_key), asset_name_comparison_key(name)),
                        artifact_path=sheet_path,
                    )
                    # Only registered images differing from canonical state are stale; missing or blocked images have their own representation
                    # (the image cannot render) and must not share this marker.
                    stale = comparison.status is ArtifactStatus.STALE
                    artifact_status = comparison.status
                derivatives[name] = {
                    "description": derivative.get("description", ""),
                    spec.sheet_field: sheet_path,
                    "stale": stale,
                    "artifact_status": artifact_status.value,
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
