"""Global assets-library routes.

Library images share the project-content-version storage contract (``lib.material.global_library``):
new images follow global local/object-storage settings, with entries recording digest, content identity,
and remote location. Saving copies usable provider-asset associations from source content versions;
applying formally commits new target versions and copies associations to them. Retrieve remote images
from object storage before reading when working files are absent."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import shutil
import uuid
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, File, Form, HTTPException, Query, UploadFile
from pydantic import BaseModel
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from lib.artifacts.artifact_activation import register_artifact_entries_atomically, resolve_current_artifact_target
from lib.artifacts.artifact_manifest import ArtifactKey
from lib.artifacts.version_manager import MANUAL_UPLOAD_VERSION_SOURCE, InstalledVersionCommit, VersionManager
from lib.asset_library.links import LinkHolder, copy_usable_links, delete_holder_links
from lib.db import async_session_factory
from lib.db.models.asset import Asset, AssetDerivative
from lib.db.models.asset_library import ProviderAssetLink
from lib.db.repositories.asset_repo import AssetRepository
from lib.infra.api_errors import NotFoundError
from lib.infra.async_thread import EventLoopBridge, run_noninterruptible_async, run_sync_transaction
from lib.material.formal_objects import formal_commit
from lib.material.global_library import (
    NO_IMAGE_COLUMNS,
    GlobalImage,
    GlobalImageWriter,
    ensure_global_image,
    forget_global_images,
    global_image_commit,
    library_entry_lock,
    library_holder,
    release_library_entry,
)
from lib.material.storage import CONTENT_DIGEST, CONTENT_VERSION_ID
from lib.material.work_files import ensure_work_file, hold_work_file
from lib.project.asset_derivatives import (
    derivative_artifact_id,
    derivative_artifact_key,
    derivative_sheet_relative_path,
    derivative_table,
    find_derivative_name,
)
from lib.project.asset_types import (
    ASSET_SPECS,
    BUCKET_KEY,
    DERIVATIVES_FIELD,
    GLOBAL_LIBRARY_ASSET_TYPES,
    SHEET_KEY,
    ProjectAssetNameConflictError,
    find_project_asset_name,
    localize_asset_type,
    resolve_asset_key,
    validate_asset_name,
)
from lib.project.project_change_hints import project_change_source
from lib.project.project_manager import ProjectManager, get_project_manager
from lib.project.resource_paths import CHARACTER_DERIVATIVE_RESOURCE_TYPE
from server.i18n import Translator
from server.routers._asset_router_factory import localize_project_asset_name_conflict

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/assets", tags=["全局资产库"])

MAX_UPLOAD_BYTES = 5 * 1024 * 1024
ALLOWED_EXTS = {".png", ".jpg", ".jpeg", ".webp"}


def _validate_asset_name(name: str, _t: Translator) -> str:
    """HTTP boundary wrapper: return 400 for unsafe names (separators / null bytes / ..)."""
    try:
        return validate_asset_name(name)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=_t("asset_invalid_name", name=name)) from exc


def _serialize(asset, derivatives: Sequence[AssetDerivative] = ()) -> dict:
    """Serialize one asset and its derivative child table into API shape.

    Derivatives move with the base asset as a set; consumers need only name, change description, and
    image path for project character entries. Types without derivatives and new assets with none use empty sequences."""
    return {
        "id": asset.id,
        "type": asset.type,
        "name": asset.name,
        "description": asset.description,
        "voice_style": asset.voice_style,
        "image_path": asset.image_path,
        "audio_path": asset.audio_path,
        "source_project": asset.source_project,
        "updated_at": asset.updated_at.isoformat() if asset.updated_at else None,
        "derivatives": [
            {"name": d.name, "description": d.description, "image_path": d.image_path} for d in derivatives
        ],
    }


async def _serialize_one(repo: AssetRepository, asset) -> dict:
    """Read an asset's derivatives and serialize them with the base asset."""
    return _serialize(asset, await repo.list_derivatives(asset.id))


async def _copy_into_global_pool(source: Path, asset_type: str, default_ext: str) -> str:
    """Copy a file into global-library ``{type}/`` and return its registered path relative to the data root."""
    ext = source.suffix.lower() or default_ext
    pm = get_project_manager()

    def _copy() -> Path:
        target = pm.get_global_assets_root() / asset_type / f"{uuid.uuid4().hex}{ext}"
        shutil.copyfile(source, target)
        return target

    target = await run_sync_transaction(_copy)
    return target.relative_to(pm.data_root).as_posix()


def _project_file_if_present(project_dir: Path, rel_path: str) -> Path | None:
    """Resolve a project-relative path to an existing file; treat escaping, missing, or nonfile paths as absent."""
    if not rel_path:
        return None
    try:
        ProjectManager._safe_subpath(project_dir, rel_path)
    except (ValueError, FileNotFoundError):
        return None
    candidate = project_dir / rel_path
    return candidate if candidate.exists() and candidate.is_file() else None


async def _read_upload(file: UploadFile, _t: Translator) -> tuple[bytes, str]:
    """Validate uploaded images and return bytes/extension (415/413 before any writes)."""
    ext = Path(file.filename or "").suffix.lower()
    if ext not in ALLOWED_EXTS:
        raise HTTPException(status_code=415, detail=_t("asset_unsupported_format"))

    data = await file.read()
    if len(data) > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail=_t("asset_upload_too_large"))
    return data, ext


def _link_holder(image: GlobalImage | None, holder: str, resource_type: str, resource_id: str) -> LinkHolder | None:
    """An image of library entry ``holder`` as provider-association holder; legacy images without content identity/digest hold no associations."""
    if image is None or image.content_id is None or image.digest is None:
        return None
    return LinkHolder(
        project_name=holder,
        content_version_id=image.content_id,
        content_digest=image.digest,
        resource_type=resource_type,
        resource_id=resource_id,
        version=0,
        locator_path=image.path,
    )


def _content_ids(images: Iterable[GlobalImage | None]) -> list[str]:
    return [image.content_id for image in images if image is not None and image.content_id]


def _row_images(asset: Asset, derivatives: Iterable[AssetDerivative]) -> list[GlobalImage | None]:
    return [GlobalImage.of(asset), *(GlobalImage.of(d) for d in derivatives)]


async def _forget_after_commit(holder: str, images: Iterable[GlobalImage | None]) -> None:
    """The entry no longer uses these images: release its object references. Changes are committed; log release failures only, since extra references merely retain objects longer."""
    try:
        await forget_global_images(holder, images)
    except Exception:
        logger.warning("撤下全局资产库条目的对象引用失败 holder=%s", holder, exc_info=True)


async def _release_after_delete(holder: str) -> None:
    """The entry is deleted: release all its object references. Deletion is committed; log release failures only, since extra references merely retain objects longer."""
    try:
        await release_library_entry(holder)
    except Exception:
        logger.warning("撤下已删除库条目的对象引用失败 holder=%s", holder, exc_info=True)


async def _discard(writer: GlobalImageWriter) -> None:
    """Entry creation failed: release references and delete files persisted by this operation. Finish even on cancellation to avoid ownerless files/references."""
    await run_noninterruptible_async(writer.discard())


def _delete_global_asset_file(rel_path: str) -> None:
    path = get_project_manager().data_root / rel_path
    try:
        path.unlink()  # hard-delete: out-of-scope -- Global-library files are outside projects.
    except FileNotFoundError:
        # Already-absent files (concurrent deletion/create rollback) count as success; ignore.
        return
    except OSError:
        logger.warning("delete global asset file failed: %s", rel_path)


async def _delete_global_asset_files(rel_paths: Iterable[str]) -> None:
    """Delete library files sequentially in a thread; log individual failures only, as in ``_delete_global_asset_file``."""
    paths = list(rel_paths)
    if not paths:
        return

    def _delete_all() -> None:
        for rel_path in paths:
            _delete_global_asset_file(rel_path)

    await run_sync_transaction(_delete_all)


@router.get("")
async def list_assets(
    _t: Translator,
    type: str | None = None,
    q: str | None = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 100,
    offset: Annotated[int, Query(ge=0)] = 0,
):
    """List assets with offset pagination.

    ``total`` counts matches for current type/search to determine next-page availability. ``counts`` follows
    search only, not type filtering, so every type tab shows search matches, including unopened tabs."""
    async with async_session_factory() as s:
        repo = AssetRepository(s)
        items = await repo.list(type=type, q=q, limit=limit, offset=offset)
        by_asset = await repo.list_derivatives_by_asset_ids([a.id for a in items])
        matched = await repo.count_by_type(q=q)
    counts = {asset_type: matched.get(asset_type, 0) for asset_type in sorted(GLOBAL_LIBRARY_ASSET_TYPES)}
    return {
        "items": [_serialize(a, by_asset.get(a.id, ())) for a in items],
        "total": matched.get(type, 0) if type else sum(counts.values()),
        "counts": counts,
    }


@router.get("/{asset_id}")
async def get_asset(asset_id: str, _t: Translator):
    async with async_session_factory() as s:
        repo = AssetRepository(s)
        a = await repo.get_by_id(asset_id)
        if not a:
            raise HTTPException(status_code=404, detail=_t("asset_not_found", name=asset_id))
        return {"asset": await _serialize_one(repo, a)}


@router.post("")
async def create_asset(
    _t: Translator,
    type: str = Form(...),
    name: str = Form(...),
    description: str = Form(""),
    voice_style: str = Form(""),
    image: UploadFile | None = File(None),
):
    if type not in GLOBAL_LIBRARY_ASSET_TYPES:
        raise HTTPException(status_code=400, detail=_t("asset_invalid_type"))
    name = _validate_asset_name(name, _t)

    upload = await _read_upload(image, _t) if image is not None and image.filename else None

    # Save images before create; failures before entry commit release references/files, while postcommit response failures preserve images.
    # Allocate entry ID first, registering image object references under it.
    asset_id = str(uuid.uuid4())
    holder = library_holder(asset_id)
    data_root = get_project_manager().data_root
    async with global_image_commit(data_root, holder, saves_images=upload is not None) as writer:
        try:
            saved = await run_sync_transaction(writer.save_bytes, upload[0], type, upload[1]) if upload else None
            await writer.hold()
            async with async_session_factory() as s:
                repo = AssetRepository(s)
                try:
                    a = await repo.create(
                        asset_id=asset_id,
                        type=type,
                        name=name,
                        description=description,
                        voice_style=voice_style,
                        source_project=None,
                        **(saved.columns() if saved else NO_IMAGE_COLUMNS),
                    )
                    await writer.commit(s)
                    await s.refresh(a)
                except IntegrityError as exc:
                    await s.rollback()
                    raise HTTPException(status_code=409, detail=_t("asset_already_exists", name=name)) from exc
        except BaseException:
            await _discard(writer)
            raise

    return {"asset": _serialize(a)}


class UpdateAssetRequest(BaseModel):
    name: str | None = None
    description: str | None = None
    voice_style: str | None = None


@router.patch("/{asset_id}")
async def update_asset(
    asset_id: str,
    req: UpdateAssetRequest,
    _t: Translator,
):
    patch = {k: v for k, v in req.model_dump().items() if v is not None}
    if "name" in patch:
        patch["name"] = _validate_asset_name(patch["name"], _t)
    async with async_session_factory() as s:
        repo = AssetRepository(s)
        a = await repo.get_by_id(asset_id)
        if not a:
            raise HTTPException(status_code=404, detail=_t("asset_not_found", name=asset_id))
        if "name" in patch and patch["name"] != a.name and await repo.exists(a.type, patch["name"]):
            raise HTTPException(status_code=409, detail=_t("asset_already_exists", name=patch["name"]))
        try:
            a = await repo.update(asset_id, **patch)
            await s.commit()
            await s.refresh(a)
        except IntegrityError as exc:
            await s.rollback()
            raise HTTPException(status_code=409, detail=_t("asset_already_exists", name=patch.get("name", ""))) from exc
        payload = await _serialize_one(repo, a)
    return {"asset": payload}


@router.delete("/{asset_id}", status_code=204)
async def delete_asset(asset_id: str, _t: Translator):
    # Serialize with image replacement: deletion reads the final image, avoiding orphaned concurrent replacements in library storage.
    async with library_entry_lock(asset_id), async_session_factory() as s:
        repo = AssetRepository(s)
        a = await repo.get_by_id(asset_id)
        if a:
            # repo.delete removes derivative rows too, but only this code knows their image paths;
            # read them before deleting the base asset, or files become unreachable and permanently remain in library storage.
            derivatives = await repo.list_derivatives(asset_id)
            stored_files = [d.image_path for d in derivatives if d.image_path]
            stored_files.extend(path for path in (a.image_path, a.audio_path) if path)
            # Delete library association copies with entries; project copies and provider assets are unaffected.
            holder = library_holder(asset_id)
            await delete_holder_links(s, project_name=holder)
            await repo.delete(asset_id)
            await s.commit()
            # Delete files/release object references after commit: these are irreversible, and doing them first leaves missing-content rows on transaction failure.
            # Release only this entry's references: projects applying the image retain their own; collect objects only after every reference is gone.
            await _release_after_delete(holder)
            await _delete_global_asset_files(stored_files)
    return


@router.post("/{asset_id}/image")
async def replace_image(
    asset_id: str,
    _t: Translator,
    image: UploadFile = File(...),
):
    # Serialize image replacement per entry: read old images under lock, then release exactly those after commit.
    async with library_entry_lock(asset_id):
        # 1) Load asset and validate existence.
        async with async_session_factory() as s:
            repo = AssetRepository(s)
            a = await repo.get_by_id(asset_id)
            if not a:
                raise HTTPException(status_code=404, detail=_t("asset_not_found", name=asset_id))
            old_image = GlobalImage.of(a)
            asset_type = a.type

        # 2) Validate/save the new image first (415/413 checks), leaving old files intact.
        data, ext = await _read_upload(image, _t)

        # 3) Update DB; failure releases new references/files and preserves the old image.
        holder = library_holder(asset_id)
        async with global_image_commit(get_project_manager().data_root, holder) as writer:
            try:
                new_image = await run_sync_transaction(writer.save_bytes, data, asset_type, ext)
                await writer.hold()
                async with async_session_factory() as s:
                    repo = AssetRepository(s)
                    # Replacing images replaces content: old-image association copies do not belong to new images.
                    await delete_holder_links(s, project_name=holder, content_version_ids=_content_ids([old_image]))
                    a = await repo.update(asset_id, **new_image.columns())
                    await writer.commit(s)
                    await s.refresh(a)
                    payload = await _serialize_one(repo, a)
            except BaseException:
                await _discard(writer)
                raise

        # 4) Release old references/delete old files only after DB update succeeds.
        await _forget_after_commit(holder, [old_image])
        if old_image is not None and old_image.path != new_image.path:
            await _delete_global_asset_files([old_image.path])

    return {"asset": payload}


class FromProjectRequest(BaseModel):
    project_name: str
    resource_type: str
    resource_id: str
    override_name: str | None = None
    overwrite: bool = False


@router.post("/from-project")
async def from_project(
    req: FromProjectRequest,
    _t: Translator,
):
    # 1) Validate type.
    if req.resource_type not in GLOBAL_LIBRARY_ASSET_TYPES:
        raise HTTPException(status_code=400, detail=_t("asset_invalid_type"))

    # 2) Load project.
    try:
        project = await asyncio.to_thread(get_project_manager().load_project, req.project_name)
    except FileNotFoundError as exc:
        raise NotFoundError("asset_target_project_not_found", project=req.project_name) from exc
    except Exception as exc:
        logger.exception("Failed to load project '%s' for from-project", req.project_name)
        raise HTTPException(status_code=500, detail=_t("asset_load_project_failed")) from exc

    # 3) Read resource from its bucket.
    bucket_key = BUCKET_KEY[req.resource_type]
    bucket = project.get(bucket_key) or {}
    # Existing keys/request names can be NFC or NFD; resolve in the coordinate system.
    resource_key = resolve_asset_key(bucket, req.resource_id)
    resource = bucket.get(resource_key) if resource_key is not None else None
    if resource_key is None or resource is None:
        raise HTTPException(
            status_code=404,
            detail=_t(
                "asset_source_resource_not_found",
                project=req.project_name,
                kind=localize_asset_type(req.resource_type, _t),
                name=req.resource_id,
            ),
        )

    # Remote-version working files may have been cleaned; retrieve before locating and register use until copying finishes, preventing cleanup deletion.
    sheet_key = SHEET_KEY[req.resource_type]
    source_rels = [resource.get(sheet_key)]
    if ASSET_SPECS[req.resource_type].supports_derivatives:
        source_rels.extend(raw.get(sheet_key) for raw in derivative_table(resource).values() if isinstance(raw, dict))
    source_project_dir = await asyncio.to_thread(_project_dir_or_none, req.project_name)
    with contextlib.ExitStack() as holds:
        if source_project_dir is not None:
            for rel in source_rels:
                if isinstance(rel, str) and rel:
                    holds.enter_context(hold_work_file(source_project_dir / rel))
                    await ensure_work_file(source_project_dir, rel)
        located = await asyncio.to_thread(
            _locate_sources, source_project_dir, req.resource_type, resource_key, resource
        )
        return await _store_from_project(req, _t, resource, located)


def _project_dir_or_none(project_name: str) -> Path | None:
    try:
        return get_project_manager().get_project_path(project_name)
    except FileNotFoundError:
        return None


def _selected_content(project_dir: Path, resource_type: str, resource_id: str) -> tuple[str, str] | None:
    """Currently selected resource version (content-version identity, digest); ``None`` without history or for early records lacking identity."""
    with VersionManager(project_dir).locked_history() as data:
        bucket = data.get(resource_type)
        history = bucket.get(resource_id) if isinstance(bucket, dict) else None
    if not isinstance(history, dict):
        return None
    current = history.get("current_version")
    for record in history.get("versions") or ():
        if isinstance(record, dict) and record.get("version") == current:
            content_version_id, digest = record.get(CONTENT_VERSION_ID), record.get(CONTENT_DIGEST)
            if isinstance(content_version_id, str) and isinstance(digest, str):
                return content_version_id, digest
    return None


@dataclass(frozen=True)
class _Source:
    """One source-project image entering the library: file plus selected content-version (identity, digest), used to copy associations."""

    path: Path | None
    content: tuple[str, str] | None


def _locate_sources(
    source_project_dir: Path | None, resource_type: str, resource_key: str, resource: dict
) -> tuple[_Source, list[tuple[str, str, _Source]], Path | None]:
    """Locate source-project asset sheets, derivatives, and reference audio; call in a thread for individual stats/history reads."""
    sheet_key = SHEET_KEY[resource_type]
    bucket_key = BUCKET_KEY[resource_type]

    def _source(rel: object, version_type: str, version_id: str) -> _Source:
        if source_project_dir is None or not isinstance(rel, str):
            return _Source(None, None)
        # Invalid paths, missing projects/files: continue as no source image.
        path = _project_file_if_present(source_project_dir, rel)
        content = _selected_content(source_project_dir, version_type, version_id) if path is not None else None
        return _Source(path, content)

    sheet = _source(resource.get(sheet_key) or "", bucket_key, resource_key)

    # Derivatives enter the library with their base: each carries change description and its own sheet. Missing-image derivatives still enter
    # (name/description establish identity; regenerate images in targets), as with missing base sheets.
    derivative_sources: list[tuple[str, str, _Source]] = []
    if ASSET_SPECS[resource_type].supports_derivatives:
        for raw_name, raw_derivative in derivative_table(resource).items():
            if not isinstance(raw_derivative, dict):
                continue
            try:
                derivative_name = validate_asset_name(raw_name)
            except ValueError:
                logger.warning("from_project: skip derivative with unsafe name: %r", raw_name)
                continue
            raw_description = raw_derivative.get("description")
            derivative_sources.append(
                (
                    derivative_name,
                    raw_description if isinstance(raw_description, str) else "",
                    _source(
                        raw_derivative.get(sheet_key),
                        CHARACTER_DERIVATIVE_RESOURCE_TYPE,
                        derivative_artifact_id(resource_key, raw_name),
                    ),
                )
            )

    # Audio matters only for characters (reference_audio, not sheets); missing/invalid paths silently
    # degrade to no source audio, like images, without interrupting library insertion.
    audio_rel = resource.get("reference_audio") or "" if resource_type == "character" else ""
    source_audio_path: Path | None = None
    if audio_rel and source_project_dir is not None:
        try:
            ProjectManager._safe_subpath(source_project_dir, audio_rel)
            candidate = source_project_dir / audio_rel
            # Generic character PATCH may write any project string into reference_audio (extra_string_fields
            # only checks types). _safe_subpath alone cannot prevent copying project.json or other project files
            # as library audio; additionally require parent characters/refs_audio, sharing
            # server/routers/files.py::_resolve_audio_ref_path rules.
            audio_refs_dir = source_project_dir / "characters" / "refs_audio"
            if (
                candidate.exists()
                and candidate.is_file()
                and os.path.realpath(candidate.parent) == os.path.realpath(audio_refs_dir)
            ):
                source_audio_path = candidate
        except (ValueError, FileNotFoundError):
            source_audio_path = None
    return sheet, derivative_sources, source_audio_path


async def _copy_links(
    session: AsyncSession,
    project_name: str,
    source: _Source,
    image: tuple[str, GlobalImage | None],
    resource_type: str,
    resource_id: str,
) -> None:
    """When library ``holder``'s newly saved image matches selected source-version content, copy usable source associations to the library image."""
    holder, saved = image
    target = _link_holder(saved, holder, resource_type, resource_id)
    if source.content is None or target is None:
        return
    await copy_usable_links(
        session, source_project=project_name, source_content_version_id=source.content[0], target=target
    )


async def _store_from_project(
    req: FromProjectRequest,
    _t: Translator,
    resource: dict,
    located: tuple[_Source, list[tuple[str, str, _Source]], Path | None],
) -> dict:
    asset_name = _validate_asset_name(req.override_name or req.resource_id, _t)
    async with async_session_factory() as s:
        found = await AssetRepository(s).get_by_type_name(req.resource_type, asset_name)
    if found is None:
        return await _write_from_project(req, _t, resource, located, asset_name)
    # Serialize overwrite with image replacement: reread under lock and release the images actually replaced after commit.
    async with library_entry_lock(found.id):
        return await _write_from_project(req, _t, resource, located, asset_name)


async def _write_from_project(
    req: FromProjectRequest,
    _t: Translator,
    resource: dict,
    located: tuple[_Source, list[tuple[str, str, _Source]], Path | None],
    asset_name: str,
) -> dict:
    sheet_source, derivative_sources, source_audio_path = located
    description = resource.get("description") or ""
    voice_style = resource.get("voice_style", "") if req.resource_type == "character" else ""

    # 4) DB precheck (orphan-safe: query before copying files).
    async with async_session_factory() as s:
        repo = AssetRepository(s)
        existing = await repo.get_by_type_name(req.resource_type, asset_name)
        # overwrite replaces all derivative rows; read old image paths first, deleting only after successful commit.
        stale_derivatives = await repo.list_derivatives(existing.id) if existing is not None else []
        conflict_payload = await _serialize_one(repo, existing) if existing is not None else None

    if conflict_payload is not None and not req.overwrite:
        raise HTTPException(
            status_code=409,
            detail={
                "message": _t("asset_already_exists", name=asset_name),
                "existing": conflict_payload,
            },
        )

    # 5) Save source/derivative images to library (global local/object-storage setting); copy audio to global_assets/{type}/{uuid}.{ext}.
    # All writes share the precommit failure boundary: release references/files on failure; postcommit response failures preserve images.
    has_images = sheet_source.path is not None or any(src.path is not None for _n, _d, src in derivative_sources)
    data_root = get_project_manager().data_root
    # Overwrite reuses entry ID; creation allocates it first, registering image object references under it.
    asset_id = existing.id if existing is not None else str(uuid.uuid4())
    holder = library_holder(asset_id)
    async with global_image_commit(data_root, holder, saves_images=has_images) as writer:
        try:
            sheet_image = (
                await run_sync_transaction(writer.save, sheet_source.path, req.resource_type, ".png")
                if sheet_source.path is not None
                else None
            )
            new_audio_path: str | None = None
            if source_audio_path is not None:
                new_audio_path = await _copy_into_global_pool(source_audio_path, req.resource_type, ".wav")
                writer.written.append(new_audio_path)

            # Derivative/base images share one library pool, so persisted shapes and cleanup entry points match exactly.
            derivative_rows: list[tuple[str, str, str | None]] = []
            derivative_fields: dict[str, dict] = {}
            derivative_images: list[tuple[str, _Source, GlobalImage]] = []
            for derivative_name, derivative_description, derivative_source in derivative_sources:
                derivative_image: GlobalImage | None = None
                if derivative_source.path is not None:
                    derivative_image = await run_sync_transaction(
                        writer.save, derivative_source.path, req.resource_type, ".png"
                    )
                    derivative_images.append((derivative_name, derivative_source, derivative_image))
                columns = derivative_image.columns() if derivative_image else dict(NO_IMAGE_COLUMNS)
                derivative_rows.append((derivative_name, derivative_description, columns.pop("image_path")))
                derivative_fields[derivative_name] = columns
            await writer.hold()

            # 6) Write DB: commit entry, derivatives, and copied associations in one transaction.
            try:
                payload, stale_images = await _write_library_entry(
                    req,
                    writer,
                    asset_id,
                    existing,
                    stale_derivatives,
                    asset_name=asset_name,
                    description=description,
                    voice_style=voice_style,
                    audio_path=new_audio_path,
                    sheet=(sheet_source, sheet_image),
                    derivative_rows=derivative_rows,
                    derivative_fields=derivative_fields,
                    derivative_images=derivative_images,
                )
            except IntegrityError as exc:
                raise HTTPException(status_code=409, detail=_t("asset_already_exists", name=asset_name)) from exc
        except BaseException:
            await _discard(writer)
            raise

    if existing is not None:
        # Old images are unused by this entry: release library object references/delete old files (new UUID paths cannot overlap old paths).
        await _forget_after_commit(holder, stale_images)
        await _delete_global_asset_files(
            [path for path in (existing.image_path, existing.audio_path) if path]
            + [d.image_path for d in stale_derivatives if d.image_path]
        )

    return {"asset": payload}


async def _write_library_entry(
    req: FromProjectRequest,
    writer: GlobalImageWriter,
    asset_id: str,
    existing: Asset | None,
    stale_derivatives: Sequence[AssetDerivative],
    *,
    asset_name: str,
    description: str,
    voice_style: str,
    audio_path: str | None,
    sheet: tuple[_Source, GlobalImage | None],
    derivative_rows: list[tuple[str, str, str | None]],
    derivative_fields: dict[str, dict],
    derivative_images: list[tuple[str, _Source, GlobalImage]],
) -> tuple[dict, list[GlobalImage | None]]:
    """Write/overwrite library entries and derivatives and copy associations; return serialized results plus replaced old images."""
    sheet_source, sheet_image = sheet
    bucket_key = BUCKET_KEY[req.resource_type]
    async with async_session_factory() as s:
        repo = AssetRepository(s)
        image_columns = sheet_image.columns() if sheet_image else NO_IMAGE_COLUMNS
        stale_images: list[GlobalImage | None] = []
        if existing is not None:
            # overwrite discards old-image association copies with images; handle old files/references after successful commit.
            stale_images = _row_images(existing, stale_derivatives)
            await delete_holder_links(
                s, project_name=library_holder(asset_id), content_version_ids=_content_ids(stale_images)
            )
            a = await repo.update(
                existing.id,
                description=description,
                voice_style=voice_style,
                audio_path=audio_path,
                source_project=req.project_name,
                **image_columns,
            )
        else:
            a = await repo.create(
                asset_id=asset_id,
                type=req.resource_type,
                name=asset_name,
                description=description,
                voice_style=voice_style,
                audio_path=audio_path,
                source_project=req.project_name,
                **image_columns,
            )
        await repo.replace_derivatives(a.id, derivative_rows, derivative_fields)
        holder = library_holder(a.id)
        await _copy_links(s, req.project_name, sheet_source, (holder, sheet_image), bucket_key, a.id)
        for derivative_name, derivative_source, derivative_image in derivative_images:
            await _copy_links(
                s,
                req.project_name,
                derivative_source,
                (holder, derivative_image),
                CHARACTER_DERIVATIVE_RESOURCE_TYPE,
                derivative_artifact_id(a.id, derivative_name),
            )
        await writer.commit(s)
        await s.refresh(a)
        return await _serialize_one(repo, a), stale_images


async def _discard_copied_links(link_ids: list[int]) -> None:
    if not link_ids:
        return
    try:
        async with async_session_factory() as session:
            for link_id in link_ids:
                orphan = await session.get(ProviderAssetLink, link_id)
                if orphan is not None:
                    await session.delete(orphan)
            await session.commit()
    except Exception:
        # Remaining copies reference content versions absent from history and cannot be selected; diagnostic effects only.
        logger.warning("应用失败后未能删除复制的关联 %s", link_ids, exc_info=True)


def _applied_sheet_version(resource_type: str, resource_id: str, sheet_file: Path) -> InstalledVersionCommit:
    """Library image imported into a project: record as the selected manually uploaded version in history."""
    return InstalledVersionCommit(
        resource_type=resource_type,
        resource_id=resource_id,
        prompt="",
        current_file=sheet_file,
        metadata={"source": MANUAL_UPLOAD_VERSION_SOURCE},
    )


class ApplyToProjectRequest(BaseModel):
    asset_ids: list[str]
    target_project: str
    conflict_policy: str = "skip"  # 'skip' | 'overwrite' | 'rename'


def _rejected_by_existing_name(project: dict, asset: Asset, conflict_policy: str) -> bool:
    """Whether an entry will skip/fail based on target-project existing asset names (invalid name, skip collision, or nonoverwritable overwrite collision).

    Consider only preexisting names: planning evaluates names occupied by earlier batch entries one by
    one. Do not exclude those early here; an extra retrieval does not affect results."""
    try:
        name = validate_asset_name(asset.name)
    except ValueError:
        return True
    existing = find_project_asset_name(project, name)
    if existing is None:
        return False
    return conflict_policy == "skip" or (conflict_policy == "overwrite" and not existing.is_entry(asset.type, name))


@router.post("/apply-to-project")
async def apply_to_project(
    req: ApplyToProjectRequest,
    _t: Translator,
):
    # 1) Validate conflict policy (400 before other checks).
    if req.conflict_policy not in {"skip", "overwrite", "rename"}:
        raise HTTPException(status_code=400, detail=_t("asset_invalid_conflict_policy"))
    asset_ids = list(dict.fromkeys(req.asset_ids))

    # 2) Validate target-project existence.
    project_manager = get_project_manager()
    try:
        project = await asyncio.to_thread(project_manager.load_project, req.target_project)
    except ProjectAssetNameConflictError as exc:
        raise HTTPException(status_code=409, detail=localize_project_asset_name_conflict(exc, _t)) from exc
    except FileNotFoundError as exc:
        raise NotFoundError("asset_target_project_not_found", project=req.target_project) from exc

    succeeded: list[dict] = []
    skipped: list[dict] = []
    failed: list[dict] = []

    # 3) Batch-read all requested assets; mark missing ones failed immediately.
    async with async_session_factory() as s:
        repo = AssetRepository(s)
        assets = await repo.get_by_ids(asset_ids)
        derivatives_by_asset = await repo.list_derivatives_by_asset_ids([a.id for a in assets])
    assets_by_id = {a.id: a for a in assets}
    failed.extend({"id": asset_id, "reason": "not_found"} for asset_id in asset_ids if asset_id not in assets_by_id)

    # Remote library working files may be cleaned; retrieve before planning and register use through application to prevent cleanup deletion.
    # Do not retrieve entries known inapplicable by existing target names; skipping them needs no object-storage access.
    data_root = project_manager.data_root
    library_images = [
        image
        for a in assets
        if not _rejected_by_existing_name(project, a, req.conflict_policy)
        for image in _row_images(a, derivatives_by_asset.get(a.id, ()))
        if image is not None
    ]
    with contextlib.ExitStack() as holds:
        for image in library_images:
            holds.enter_context(hold_work_file(data_root / image.path))
            await ensure_global_image(data_root, image.path)

        # 4) Plan target names/copy requirements for each asset in memory first,
        #    then copy files and write project.json together.
        def _probe_planning_inputs() -> tuple[Path, set[str]]:
            # Planning needs only existing library files; resolve directories/stat files together in a thread.
            target_dir = project_manager.get_project_path(req.target_project)
            stored = {
                path
                for a in assets
                for path in (
                    a.image_path,
                    a.audio_path,
                    *(d.image_path for d in derivatives_by_asset.get(a.id, ())),
                )
                if path
            }
            return target_dir, {path for path in stored if (project_manager.data_root / path).is_file()}

        project_dir, present_files = await asyncio.to_thread(_probe_planning_inputs)
        # Occupied-name tables for four asset types mirror project assets. Dedup and locked application share find_project_asset_name:
        # case-only variants count as occupied; is_entry distinguishes overwritable same entries from nonoverwritable
        # conflicts (cross-type or another case-only name within the same type).
        occupied: dict[str, dict[str, None]] = {}
        for spec in ASSET_SPECS.values():
            bucket = project.get(spec.bucket_key)
            if isinstance(bucket, dict):
                occupied[spec.bucket_key] = dict.fromkeys(name for name in bucket if isinstance(name, str))
        plans: list[dict] = []
        for asset_id in asset_ids:
            a = assets_by_id.get(asset_id)
            if a is None:
                continue  # Already failed.

            bucket_key = BUCKET_KEY[a.type]
            sheet_key = SHEET_KEY[a.type]
            try:
                desired_name = _validate_asset_name(a.name, _t)
            except HTTPException:
                failed.append({"id": a.id, "reason": "invalid_name"})
                continue

            existing = find_project_asset_name(occupied, desired_name)
            if existing is not None:
                same_entry = existing.is_entry(a.type, desired_name)
                if req.conflict_policy == "skip":
                    skipped.append({"id": a.id, "name": a.name})
                    continue
                if req.conflict_policy == "rename":
                    base_name = desired_name
                    i = 2
                    while find_project_asset_name(occupied, f"{base_name} ({i})") is not None:
                        i += 1
                    desired_name = f"{base_name} ({i})"
                elif not same_entry:
                    failed.append({"id": a.id, "reason": "project_name_conflict"})
                    continue
                # overwrite can replace only same-name entries of the same type.

            # Plan image copies.
            target_sheet: str | None = None
            copy_src: Path | None = None
            copy_dst: Path | None = None
            if a.image_path:
                src = project_manager.data_root / a.image_path
                if a.image_path in present_files:
                    ext = src.suffix.lower() or ".png"
                    rel_sheet = f"{bucket_key}/{desired_name}{ext}"
                    try:
                        ProjectManager._safe_subpath(project_dir, rel_sheet)
                    except ValueError:
                        failed.append({"id": a.id, "reason": "invalid_name"})
                        continue
                    target_sheet = rel_sheet
                    copy_src = src
                    copy_dst = project_dir / rel_sheet
                else:
                    logger.warning(
                        "apply_to_project: asset %s image file missing on disk: %s",
                        a.id,
                        a.image_path,
                    )
                    failed.append({"id": a.id, "reason": "image_missing"})
                    continue

            # Plan reference-audio copies like images (missing marks the whole entry failed, not the batch); relevant only to characters.
            target_audio: str | None = None
            copy_audio_src: Path | None = None
            copy_audio_dst: Path | None = None
            if a.type == "character" and a.audio_path:
                audio_src = project_manager.data_root / a.audio_path
                if a.audio_path in present_files:
                    audio_ext = audio_src.suffix.lower() or ".wav"
                    rel_audio = f"characters/refs_audio/{desired_name}{audio_ext}"
                    try:
                        ProjectManager._safe_subpath(project_dir, rel_audio)
                    except ValueError:
                        failed.append({"id": a.id, "reason": "invalid_name"})
                        continue
                    target_audio = rel_audio
                    copy_audio_src = audio_src
                    copy_audio_dst = project_dir / rel_audio
                else:
                    logger.warning(
                        "apply_to_project: asset %s audio file missing on disk: %s",
                        a.id,
                        a.audio_path,
                    )
                    failed.append({"id": a.id, "reason": "audio_missing"})
                    continue

            # Apply derivatives with their base: always persist names/descriptions, copying images only if their library files still exist.
            # Derivative names belong only within base entries, not project namespaces; exclude from occupied reconciliation.
            derivative_plans: list[dict] = []
            if ASSET_SPECS[a.type].supports_derivatives:
                for derivative in derivatives_by_asset.get(a.id, ()):
                    derivative_src: Path | None = None
                    if derivative.image_path:
                        if derivative.image_path in present_files:
                            derivative_src = project_manager.data_root / derivative.image_path
                        else:
                            logger.warning(
                                "apply_to_project: asset %s derivative %r image file missing on disk: %s",
                                a.id,
                                derivative.name,
                                derivative.image_path,
                            )
                    derivative_plans.append(
                        {
                            "name": derivative.name,
                            "description": derivative.description,
                            "copy_src": derivative_src,
                            "image": GlobalImage.of(derivative),
                        }
                    )

            occupied.setdefault(bucket_key, {})[desired_name] = None
            plans.append(
                {
                    "asset": a,
                    "requested_name": _validate_asset_name(a.name, _t),
                    "bucket_key": bucket_key,
                    "sheet_key": sheet_key,
                    "desired_name": desired_name,
                    "target_sheet": target_sheet,
                    "copy_src": copy_src,
                    "copy_dst": copy_dst,
                    "target_audio": target_audio,
                    "copy_audio_src": copy_audio_src,
                    "copy_audio_dst": copy_audio_dst,
                    "derivatives": derivative_plans,
                    "derivative_sheet_names": [],
                    "installed_sheets": [],
                    "image": GlobalImage.of(a),
                    "link_targets": [],
                }
            )

        # 5) Persist all file replacements/bucket changes in one transaction. Outside-lock planning only enables fast failures;
        #    reapply policy from requested_name under lock, covering same-type occupancy arising after the snapshot.
        file_copies: list[tuple[Path, Path]] = []

        def _apply_all(data: dict) -> None:
            applied_plans: list[dict] = []
            for plan in plans:
                a_ = plan["asset"]
                bk = plan["bucket_key"]
                sk = plan["sheet_key"]
                name_ = plan["requested_name"]
                existing = find_project_asset_name(data, name_)
                if existing is not None:
                    if req.conflict_policy == "skip":
                        skipped.append({"id": a_.id, "name": a_.name})
                        continue
                    if req.conflict_policy == "rename":
                        base_name = name_
                        index = 2
                        while find_project_asset_name(data, f"{base_name} ({index})") is not None:
                            index += 1
                        name_ = f"{base_name} ({index})"
                        existing = None
                    elif not existing.is_entry(a_.type, name_):
                        raise ProjectAssetNameConflictError(name_, existing, a_.type)

                plan["desired_name"] = name_
                plan["derivative_sheet_names"] = []
                plan["installed_sheets"] = []
                plan["link_targets"] = []
                if plan["copy_src"] is not None:
                    extension = plan["copy_src"].suffix.lower() or ".png"
                    plan["target_sheet"] = f"{bk}/{name_}{extension}"
                    plan["copy_dst"] = project_dir / plan["target_sheet"]
                    file_copies.append((plan["copy_src"], plan["copy_dst"]))
                if plan["copy_audio_src"] is not None:
                    extension = plan["copy_audio_src"].suffix.lower() or ".wav"
                    plan["target_audio"] = f"characters/refs_audio/{name_}{extension}"
                    plan["copy_audio_dst"] = project_dir / plan["target_audio"]
                    file_copies.append((plan["copy_audio_src"], plan["copy_audio_dst"]))

                ts = plan["target_sheet"]
                ta = plan["target_audio"]
                payload: dict = {"description": a_.description or ""}
                if a_.type == "character":
                    payload["voice_style"] = a_.voice_style or ""
                    if ta:
                        payload["reference_audio"] = ta
                        # Asset presence is the switch: importing equals setting this voice; legacy-transition banner counts must detect it.
                        payload["voice_updated_at"] = datetime.now(UTC).isoformat()
                if ts:
                    payload[sk] = ts
                if bk not in data or not isinstance(data.get(bk), dict):
                    data[bk] = {}
                # Apply overwrite to actual existing keys (possibly NFD), avoiding visually identical duplicate entries.
                key = existing.name if existing is not None and existing.asset_type == a_.type else name_
                if ts:
                    plan["installed_sheets"].append(_applied_sheet_version(bk, key, project_dir / ts))
                    plan["link_targets"].append((bk, key, plan["image"]))
                # Before whole-entry replacement, inherit existing derivatives, then overlay library derivatives by name:
                # preserve existing derivatives absent from library (overwrite imports must not erase user-registered derivatives); library
                # versions win same-name conflicts. New entries start empty, as in creation/migration.
                if ASSET_SPECS[a_.type].supports_derivatives:
                    previous = data[bk].get(key)
                    inherited = previous.get(DERIVATIVES_FIELD) if isinstance(previous, dict) else None
                    table = dict(inherited) if isinstance(inherited, dict) else {}
                    # Resolve library derivatives to persisted names first: existing keys may be NFD-equivalent or case-only variants (images persist by name,
                    # so both target one file). Merge into existing entries and retain their names, avoiding duplicates. Library-provided
                    # case-only pairs likewise merge under one name, with later entries winning.
                    incoming: dict[str, dict] = {}
                    for derivative in plan["derivatives"]:
                        derivative_name = derivative["name"]
                        derivative_key = (
                            find_derivative_name(table, derivative_name)
                            or find_derivative_name(incoming, derivative_name)
                            or derivative_name
                        )
                        incoming[derivative_key] = derivative
                    for derivative_key, derivative in incoming.items():
                        derivative_sheet = ""
                        if derivative["copy_src"] is not None:
                            relative = derivative_sheet_relative_path(name_, derivative_key)
                            try:
                                ProjectManager._safe_subpath(project_dir, relative)
                            except ValueError:
                                # Both name segments passed validate_asset_name, so reaching here means the combined path still
                                # escapes: omit this image rather than fail the batch, still applying derivative name/description.
                                logger.warning(
                                    "apply_to_project: unsafe derivative sheet path %r, importing without image",
                                    relative,
                                )
                            else:
                                derivative_sheet = relative
                                file_copies.append((derivative["copy_src"], project_dir / relative))
                                plan["derivative_sheet_names"].append(derivative_key)
                        if derivative_sheet:
                            derivative_id = derivative_artifact_id(key, derivative_key)
                            plan["installed_sheets"].append(
                                _applied_sheet_version(
                                    CHARACTER_DERIVATIVE_RESOURCE_TYPE, derivative_id, project_dir / derivative_sheet
                                )
                            )
                            plan["link_targets"].append(
                                (CHARACTER_DERIVATIVE_RESOURCE_TYPE, derivative_id, derivative["image"])
                            )
                        existing_derivative = table.get(derivative_key)
                        merged = dict(existing_derivative) if isinstance(existing_derivative, dict) else {}
                        merged["description"] = derivative["description"]
                        merged[sk] = derivative_sheet
                        table[derivative_key] = merged
                    payload[DERIVATIVES_FIELD] = table
                data[bk][key] = payload
                applied_plans.append(plan)

            plans[:] = applied_plans

        if plans:

            def _register_imported_sheet_claims() -> None:
                owner_keys = {ArtifactKey.asset_sheet(plan["asset"].type, plan["desired_name"]) for plan in plans}
                derivative_keys = {
                    derivative_artifact_key(plan["desired_name"], derivative_name)
                    for plan in plans
                    for derivative_name in plan["derivative_sheet_names"]
                }
                owner_entries = {key: resolve_current_artifact_target(project_dir, key) for key in owner_keys}
                derivative_entries = {
                    key: resolve_current_artifact_target(project_dir, key, pending_entries=owner_entries)
                    for key in derivative_keys
                }
                register_artifact_entries_atomically(project_dir, owner_entries | derivative_entries)

            bridge = EventLoopBridge.capture()
            copied_links: list[int] = []

            async def _record_links(targets: list[tuple[str, str, LinkHolder]]) -> None:
                async with async_session_factory() as session:
                    for source_holder, source_content_id, holder in targets:
                        copies = await copy_usable_links(
                            session,
                            source_project=source_holder,
                            source_content_version_id=source_content_id,
                            target=holder,
                        )
                        await session.flush()
                        copied_links.extend(link.id for link in copies)
                    await session.commit()

            def _copy_links_to_applied_versions() -> None:
                # Library-associated images and newly selected target versions have identical bytes; copy usable library associations to target versions,
                # independently held there. Do this last in version commit; DB failure rolls back the entire application.
                manager = VersionManager(project_dir)
                targets: list[tuple[str, str, LinkHolder]] = []
                for plan in plans:
                    source_holder = library_holder(plan["asset"].id)
                    for resource_type, resource_id, image in plan["link_targets"]:
                        if image is None or image.content_id is None:
                            continue
                        current = manager.get_current_version(resource_type, resource_id)
                        holder = LinkHolder.of(
                            req.target_project, manager.ensure_content_version(resource_type, resource_id, current)
                        )
                        if holder is not None:
                            targets.append((source_holder, image.content_id, holder))
                if targets:
                    bridge.run(_record_links(targets))

            def _after_versions_committed() -> None:
                _register_imported_sheet_claims()
                _copy_links_to_applied_versions()

            def _select_imported_sheets(_project_file: Path) -> None:
                # Imported asset/derivative sheets are finished images: select a manually uploaded version for each in the same commit;
                # planners project/register basis from the images themselves, independent of descriptions/styles.
                commits = [commit for plan in plans for commit in plan["installed_sheets"]]
                if not commits:
                    _register_imported_sheet_claims()
                    return
                VersionManager(project_dir).commit_installed_versions(commits, on_commit=_after_versions_committed)

            try:
                # Applied images are new target-project content versions, stored locally or in object storage by global settings.
                async with formal_commit(req.target_project):
                    with project_change_source("webui"):
                        await run_noninterruptible_async(
                            run_sync_transaction(
                                project_manager.update_project_with_file_copies,
                                req.target_project,
                                _apply_all,
                                file_copies,
                                on_commit=_select_imported_sheets,
                            )
                        )
            except ProjectAssetNameConflictError as exc:
                await _discard_copied_links(copied_links)
                raise HTTPException(status_code=409, detail=localize_project_asset_name_conflict(exc, _t)) from exc
            except BaseException:
                # If formal writes still fail after association persistence, rolled-back history lacks those versions and copies are unselectable; delete best-effort.
                await _discard_copied_links(copied_links)
                raise

        succeeded.extend({"id": plan["asset"].id, "name": plan["desired_name"]} for plan in plans)

        return {"succeeded": succeeded, "skipped": skipped, "failed": failed}
