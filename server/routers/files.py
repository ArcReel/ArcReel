"""File-management routes.

Handle uploads and static resource serving."""

import asyncio
import json
import logging
import os
import shutil
import stat
import tempfile
from collections.abc import AsyncIterator, Callable
from contextlib import ExitStack
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated, Literal

logger = logging.getLogger(__name__)

from fastapi import APIRouter, Body, Depends, File, HTTPException, Query, Request, UploadFile
from fastapi.responses import PlainTextResponse

from lib.bgm.library import BGM_DIR, BGM_EXTENSIONS, BGM_MAX_BYTES, BgmTrack
from lib.bgm.service import BgmError, BgmLibraryService
from lib.catalog.errors import ModelNotInCatalogError
from lib.config.resolver import NoAvailableProviderError, VisionCapabilityError
from lib.episode.episode_ledger import is_derived_episode_name
from lib.episode.episode_paths import (
    REFERENCE_VIDEO_SCRIPT_PLAN_FILENAME,
    REFERENCE_VIDEO_SCRIPT_PLAN_LEGACY_FILENAME,
    SCRIPT_PLAN_FILENAMES,
    episode_drafts_dir,
    episode_source_relpath,
    script_plan_read_candidates,
)
from lib.episode.episode_source_commands import (
    EpisodeSourceError,
    add_own_source_episode,
    register_whole_source_file,
    unregister_source_file,
)
from lib.episode.episode_sources import whole_source_files
from lib.episode.source_file_changes import (
    SourceFileChangeError,
    SourceFileChangeOutcome,
    delete_whole_source_file,
    edit_whole_source_file,
    insert_whole_source_file,
    replace_whole_source_file,
)
from lib.episode.source_kinds import SourceKind
from lib.episode.source_trash import trash_source_file
from lib.infra.api_errors import ApiError, BadRequestError, NotFoundError
from lib.infra.async_thread import run_sync_transaction
from lib.infra.data_root_layout import GLOBAL_ASSETS_DIRNAME
from lib.infra.image_thumbnails import ensure_image_thumbnail, is_thumbnail_source, snap_thumbnail_width
from lib.infra.image_utils import IMAGE_FILE_EXTENSIONS, normalize_uploaded_image, validate_image_bytes
from lib.infra.json_io import atomic_write_bytes
from lib.infra.path_safety import PathTraversalError, safe_join
from lib.material.formal_objects import formal_commit
from lib.material.global_library import ensure_global_image
from lib.material.work_files import ensure_work_file, hold_work_file
from lib.project.asset_types import ASSET_SPECS, GLOBAL_LIBRARY_ASSET_TYPES, resolve_asset_key, validate_asset_name
from lib.project.project_change_hints import (
    build_change_label,
    emit_project_change_batch,
    project_change_source,
)
from lib.project.project_manager import ProjectManager, get_project_manager
from lib.project.project_migration_guard import assert_project_migration_ok
from lib.script import script_review
from lib.script.source_loader import (
    ConflictError,
    CorruptFileError,
    FileSizeExceededError,
    NormalizeResult,
    OnConflict,
    SourceDecodeError,
    SourceLoader,
    UnsupportedFormatError,
)
from lib.speech.audio_utils import (
    AUDIO_REFERENCE_MAX_BYTES,
    AUDIO_REFERENCE_MAX_SECONDS,
    AUDIO_REFERENCE_MIN_SECONDS,
    probe_audio_duration_seconds,
)
from server.dependencies import require_project_migration_ok
from server.i18n import Translator
from server.routers._episode_source_errors import episode_source_http_error
from server.routers._file_responses import cached_file_response, guess_media_type
from server.routers._script_review_errors import raise_review_error
from server.routers._source_file_changes import (
    ensure_no_displaced_tasks,
    source_file_change_http_error,
    source_file_change_payload,
)
from server.routers._source_uploads import extract_uploaded_source_text, source_loader_http_error
from server.services.currency.upload_finalize import install_manual_asset_sheet_upload
from server.services.project.script_review import ScriptReviewError, ScriptReviewService

router = APIRouter()

#: Source-upload registration: a whole-source file or a source-owning episode.
SourceUploadRole = Literal["whole_source", "episode"]

_IMAGE_EXTS: tuple[str, ...] = IMAGE_FILE_EXTENSIONS

# Public endpoints: frontend <img src> / <video src> / <audio src> native browser requests cannot carry
# Authorization headers. ADR 0071 keeps static media anonymously readable; public endpoints allow only media:
# safe_join confines paths to roots, then both directory and extension allowlists below must match.
public_router = APIRouter()

# Sole implementation definition of public static-media scope under ADR 0071.
# Directories: generated/uploaded project media referenced directly by frontend elements, including nested directories
# （characters/refs、characters/refs_audio、characters/derivatives、products/refs、
# reference_videos/thumbnails, renders/episode_N/<剪辑时间线>, etc.; bgm is project-level BGM. versions/ permits only their corresponding snapshot buckets.
PUBLIC_MEDIA_DIRS: frozenset[str] = frozenset(
    {
        "storyboards",
        "end_frames",
        "videos",
        "reference_videos",
        "thumbnails",
        "characters",
        "scenes",
        "props",
        "products",
        "grids",
        "audio",
        "bgm",
        "renders",
    }
)
PUBLIC_VERSIONS_DIR = "versions"
# Sole root-level media file: style reference (image extensions only).
PUBLIC_ROOT_MEDIA_STEM = "style_reference"
# Extensions: images/video/audio only, case-insensitive; metadata such as .json in the same directories is excluded.
# Directory and root filenames are case-sensitive.
PUBLIC_MEDIA_EXTENSIONS: frozenset[str] = frozenset({*_IMAGE_EXTS, ".mp4", ".wav", ".mp3", ".m4a"})
# All public file responses prohibit browser MIME sniffing.
_PUBLIC_FILE_HEADERS: dict[str, str] = {"X-Content-Type-Options": "nosniff"}


def _regular_file_stat(path: Path) -> os.stat_result | None:
    """Return regular-file stat, or None if absent/nonregular (same criterion as ``Path.is_file``)."""
    try:
        stat_result = path.stat()
    except (OSError, ValueError):
        return None
    return stat_result if stat.S_ISREG(stat_result.st_mode) else None


def is_public_media_path(relative_parts: tuple[str, ...]) -> bool:
    """Whether a project-relative path split into segments is media readable through public endpoints."""
    if not relative_parts or any(part.startswith(".") for part in relative_parts):
        return False
    name = Path(relative_parts[-1])
    if name.suffix.lower() not in PUBLIC_MEDIA_EXTENSIONS:
        return False
    if len(relative_parts) == 1:
        return name.stem == PUBLIC_ROOT_MEDIA_STEM and name.suffix.lower() in _IMAGE_EXTS
    top = relative_parts[0]
    if top == PUBLIC_VERSIONS_DIR:
        return len(relative_parts) >= 3 and relative_parts[1] in PUBLIC_MEDIA_DIRS
    return top in PUBLIC_MEDIA_DIRS


def _require_filename(file: UploadFile, _t: Callable[..., str]) -> str:
    if not file.filename:
        raise HTTPException(status_code=400, detail=_t("missing_filename"))
    return file.filename


# Persisted filename policies.
#   stable_png: stable single-image `{name}.png` (actual extension follows image normalization).
#   keep_ext: stable single-file name retaining uploaded extension (audio is not transcoded).
#   sequenced: accumulate images using numbered unique names.
#   delegated: dedicated type branches own naming.
Naming = Literal["stable_png", "keep_ext", "sequenced", "delegated"]

# Pre-persistence content handling.
#   normalize_image: validate/compress by threshold, possibly changing extension.
#   validate_image: check decodability only, preserving original bytes.
#   audio: validate duration without transcoding (max_bytes independently checks size).
#   delegated: dedicated type branches own content handling.
ContentCheck = Literal["normalize_image", "validate_image", "audio", "delegated"]

MetadataSetter = Callable[[ProjectManager, str, str, str], object]


@dataclass(frozen=True)
class UploadSpec:
    """Persistence rules for one upload_type: destination, extension allowlist, size cap, processing, and metadata writeback.

    Register new types only in ``UPLOAD_SPECS``, without copying branches. ``source`` is the sole exception:
    ``_handle_source_upload`` owns its flow; registration supplies only type checks/extension allowlists.

    Uploads addressed by script_file + shot_id that update script metadata use their own storyboard-level
    routes, sharing validation/persistence helpers in ``server.services.currency.upload_finalize`` rather than this table."""

    allowed_exts: tuple[str, ...]
    subdir: tuple[str, ...]
    naming: Naming
    content_check: ContentCheck
    unsupported_ext_key: str = "unsupported_image_type"
    # Request-body limit, None for unlimited; applies to all types independently of content_check.
    max_bytes: int | None = None
    metadata_setter: MetadataSetter | None = None
    # Nonempty means a host-asset field is the file's sole pointer; reject absent hosts
    # (including concurrent-deletion windows), avoiding UI-invisible orphan files. Stable single-image paths
    # tolerate later asset creation, so have no such constraint.
    host_bucket: str | None = None
    host_not_found_key: str = ""
    # Reference-audio byte replacement/field writeback use ProjectManager project-lock transactions; remove old-extension files after commit.
    tracks_stale_audio: bool = False

    def __post_init__(self) -> None:
        # Register host constraints together with 404 wording, or rejection would look up an empty translation key.
        if (self.host_bucket is None) != (not self.host_not_found_key):
            raise ValueError("host_bucket 与 host_not_found_key 必须成对登记")
        if self.tracks_stale_audio and self.host_bucket is None:
            raise ValueError("tracks_stale_audio 必须登记宿主约束")


UPLOAD_SPECS: dict[str, UploadSpec] = {
    "source": UploadSpec(
        allowed_exts=(".txt", ".md", ".docx", ".epub", ".pdf"),
        subdir=("source",),
        naming="delegated",
        content_check="delegated",
    ),
    "character": UploadSpec(
        allowed_exts=_IMAGE_EXTS,
        subdir=(ASSET_SPECS["character"].subdir,),
        naming="stable_png",
        content_check="normalize_image",
        metadata_setter=ProjectManager.update_project_character_sheet,
    ),
    "character_ref": UploadSpec(
        allowed_exts=_IMAGE_EXTS,
        subdir=(ASSET_SPECS["character"].subdir, "refs"),
        naming="stable_png",
        content_check="normalize_image",
        metadata_setter=ProjectManager.update_character_reference_image,
    ),
    "character_audio_ref": UploadSpec(
        allowed_exts=(".wav", ".mp3"),
        subdir=(ASSET_SPECS["character"].subdir, "refs_audio"),
        naming="keep_ext",
        content_check="audio",
        unsupported_ext_key="unsupported_audio_type",
        max_bytes=AUDIO_REFERENCE_MAX_BYTES,
        metadata_setter=ProjectManager.update_character_reference_audio,
        host_bucket=ASSET_SPECS["character"].bucket_key,
        host_not_found_key="character_not_found",
        tracks_stale_audio=True,
    ),
    "scene": UploadSpec(
        allowed_exts=_IMAGE_EXTS,
        subdir=(ASSET_SPECS["scene"].subdir,),
        naming="stable_png",
        content_check="normalize_image",
        metadata_setter=ProjectManager.update_scene_sheet,
    ),
    "prop": UploadSpec(
        allowed_exts=_IMAGE_EXTS,
        subdir=(ASSET_SPECS["prop"].subdir,),
        naming="stable_png",
        content_check="normalize_image",
        metadata_setter=ProjectManager.update_prop_sheet,
    ),
    "product": UploadSpec(
        allowed_exts=_IMAGE_EXTS,
        subdir=(ASSET_SPECS["product"].subdir,),
        naming="stable_png",
        content_check="normalize_image",
        metadata_setter=ProjectManager.update_product_sheet,
    ),
    # Project BGM is owned by BgmLibraryService (measured loudness, atomic persistence, byte registration); table supplies only type/extension checks.
    "bgm": UploadSpec(
        allowed_exts=BGM_EXTENSIONS,
        subdir=(BGM_DIR,),
        naming="delegated",
        content_check="delegated",
        unsupported_ext_key="unsupported_audio_type",
        max_bytes=BGM_MAX_BYTES,
    ),
    "product_ref": UploadSpec(
        allowed_exts=_IMAGE_EXTS,
        subdir=(ASSET_SPECS["product"].subdir, "refs"),
        naming="sequenced",
        # Product originals anchor fidelity acceptance (ADR 0034): check decodability only, preserving original bytes/extensions,
        # without threshold compression/reencoding. Presubmission reference compression separately guarantees request-body limits.
        content_check="validate_image",
        metadata_setter=ProjectManager.add_product_reference_image,
        host_bucket=ASSET_SPECS["product"].bucket_key,
        host_not_found_key="product_not_found",
    ),
}

_FORMAL_SHEET_UPLOAD_TYPES = frozenset(ASSET_SPECS)

# Allowed file types (frontend/src/utils/source-files.ts mirrors source).
ALLOWED_EXTENSIONS = {upload_type: list(spec.allowed_exts) for upload_type, spec in UPLOAD_SPECS.items()}


async def _media_work_files() -> AsyncIterator[ExitStack]:
    # FileResponse opens files after route return; retain usage registration until the entire response ends.
    with ExitStack() as holds:
        yield holds


@public_router.get("/files/{project_name}/{path:path}")
async def serve_project_file(
    project_name: str,
    path: str,
    request: Request,
    _t: Translator,
    holds: Annotated[ExitStack, Depends(_media_work_files, scope="request")],
):
    """Serve project media (images/video/audio), scoped by ``is_public_media_path``.

    Images accept ``?w=`` for WebP thumbnails (tiers/fallbacks in ``lib.infra.image_thumbnails``).
    Cache headers and conditional requests also apply to thumbnails, with ETags calculated from thumbnail files."""
    try:

        def _resolve() -> tuple[ProjectManager, Path, Path, tuple[str, ...]]:
            project_manager = get_project_manager()
            project_dir = project_manager.get_project_path(project_name)

            # Check safety before existence: all escaping paths return 403, preventing 404/403 differences from probing
            # file existence outside project directories.
            try:
                file_path = safe_join(project_dir, path)
            except PathTraversalError as exc:
                raise HTTPException(status_code=403, detail=_t("forbidden_access")) from exc
            relative_parts = file_path.relative_to(os.path.realpath(project_dir)).parts
            return project_manager, project_dir, file_path, relative_parts

        def _sync(project_manager: ProjectManager, project_dir: Path, file_path: Path, relative_parts: tuple[str, ...]):
            # Judge media scope by resolved actual paths (reject symlinks to nonmedia too);
            # nonmedia and absent files both return 404.
            stat_result = _regular_file_stat(file_path) if is_public_media_path(relative_parts) else None
            if stat_result is None:
                raise HTTPException(status_code=404, detail=_t("file_not_found", path=path))

            # ?w= applies only to raster images: cache thumbnails outside projects by resolved actual paths;
            # return originals if no wider than target or undecodable.
            if thumbnail_width is not None and is_thumbnail_source(file_path):
                thumbnail = ensure_image_thumbnail(
                    file_path,
                    stat_result,
                    cache_dir=project_manager.layout.image_thumbnail_cache_dir / project_dir.name,
                    source_key="/".join(relative_parts),
                    width=thumbnail_width,
                )
                if thumbnail is not None:
                    thumbnail_path, thumbnail_stat = thumbnail
                    return thumbnail_path, thumbnail_stat, guess_media_type(thumbnail_path)
                # Sources may change during generation; fallback originals use current lengths/ETags.
                stat_result = _regular_file_stat(file_path)
                if stat_result is None:
                    raise HTTPException(status_code=404, detail=_t("file_not_found", path=path))

            return file_path, stat_result, guess_media_type(file_path)

        thumbnail_width = snap_thumbnail_width(request.query_params.get("w"))
        project_manager, project_dir, resolved_path, relative_parts = await asyncio.to_thread(_resolve)
        holds.enter_context(hold_work_file(resolved_path))
        holds.enter_context(hold_work_file(project_dir.joinpath(*relative_parts)))
        if is_public_media_path(relative_parts):
            # Retrieve cleaned remote-version working files from object storage before serving; explicit errors on retrieval failure.
            await ensure_work_file(project_dir, "/".join(relative_parts))
        file_path, stat_result, media_type = await asyncio.to_thread(
            _sync, project_manager, project_dir, resolved_path, relative_parts
        )

        # Content-addressed cache: ?v= or versions/ paths are immutable; otherwise revalidate every time.
        immutable = bool(request.query_params.get("v")) or path.startswith("versions/")
        return cached_file_response(
            file_path,
            stat_result,
            request,
            immutable=immutable,
            headers=_PUBLIC_FILE_HEADERS,
            media_type=media_type,
        )
    except FileNotFoundError as exc:
        raise NotFoundError("project_not_found", name=project_name) from exc


@public_router.get("/global-assets/{asset_type}/{filename}")
async def serve_global_asset(
    asset_type: str,
    filename: str,
    request: Request,
    _t: Translator,
    holds: Annotated[ExitStack, Depends(_media_work_files, scope="request")],
):
    """Serve global_assets media (global-library types only: character/scene/prop)."""
    if asset_type not in GLOBAL_LIBRARY_ASSET_TYPES:
        raise HTTPException(status_code=400, detail=_t("invalid_asset_type"))
    if "/" in filename or ".." in filename:
        raise HTTPException(status_code=400, detail=_t("invalid_asset_filename"))

    def _resolve() -> tuple[Path, Path]:
        project_manager = get_project_manager()
        root = project_manager.get_global_assets_root()
        # Defensive check: even string-validated filenames must resolve inside root
        # (symlink/URL-encoding edge cases).
        try:
            return project_manager.data_root, safe_join(root, asset_type, filename)
        except PathTraversalError as exc:
            raise HTTPException(status_code=403, detail=_t("forbidden_access")) from exc

    def _sync(path: Path) -> tuple[Path, os.stat_result, str]:
        stat_result = _regular_file_stat(path) if path.suffix.lower() in PUBLIC_MEDIA_EXTENSIONS else None
        if stat_result is None:
            raise HTTPException(status_code=404, detail=_t("file_not_found", path=filename))
        return path, stat_result, guess_media_type(path)

    data_root, resolved_path = await asyncio.to_thread(_resolve)
    holds.enter_context(hold_work_file(resolved_path))
    holds.enter_context(hold_work_file(data_root / GLOBAL_ASSETS_DIRNAME / asset_type / filename))
    # Retrieve cleaned remote-library working images from object storage before serving.
    await ensure_global_image(data_root, f"{GLOBAL_ASSETS_DIRNAME}/{asset_type}/{filename}")
    path, stat_result, media_type = await asyncio.to_thread(_sync, resolved_path)

    # UUID filenames use frontend ?fp=<updated_at> version keys (?v= equivalent); versioned responses are immutable.
    query = request.query_params
    immutable = bool(query.get("fp") or query.get("v"))
    return cached_file_response(
        path, stat_result, request, immutable=immutable, headers=_PUBLIC_FILE_HEADERS, media_type=media_type
    )


@router.post("/projects/{project_name}/upload/{upload_type}")
async def upload_file(
    project_name: str,
    upload_type: str,
    _t: Translator,
    file: UploadFile = File(...),
    name: str | None = None,
    on_conflict: OnConflict = "fail",
    role: SourceUploadRole = "whole_source",
    insert_at: Annotated[int | None, Query(ge=0)] = None,
    source_kind: SourceKind | None = None,
    revision: str | None = None,
):
    """Upload a file.

    Args:
        project_name: Project name.
        upload_type: Upload type (source/character/character_ref/character_audio_ref/scene/prop/product/product_ref/bgm).
        file: Uploaded file.
        name: Optional character/scene/prop/product name (automatically updates metadata); required for product_ref.
            Storyboard/video uploads use shot_uploads routes.
        on_conflict: source only: fail / replace / rename.
        role: source only: whole_source registers a whole-source file at the end of its list;
            episode registers a source-owning episode at the end of broadcast order.
        insert_at: role=whole_source only: registered index in the whole-source file list; omitted/out-of-end appends.
        source_kind: source only: original source-file type for drama projects, novel by default; ignored by other content modes.
        revision: role=whole_source only: approved affected-episode-list revision. If insertion falls inside a cross-file
            episode, or on_conflict=replace overwrites a registered whole-source file, and episodes are affected,
            missing/stale revision prevents writes and returns ``status=confirmation_required`` with affected episodes."""
    spec = UPLOAD_SPECS.get(upload_type)
    if spec is None:
        raise HTTPException(status_code=400, detail=_t("invalid_upload_type", upload_type=upload_type))

    original_filename = _require_filename(file, _t)

    # Check file extension.
    ext = Path(original_filename).suffix.lower()
    if ext not in spec.allowed_exts:
        raise HTTPException(
            status_code=400,
            detail=_t(spec.unsupported_ext_key, ext=ext, allowed=", ".join(spec.allowed_exts)),
        )

    # Names enter persisted paths; reject unsafe separators / .. / controls at the boundary.
    # Omitted names fall back to original stems without validation.
    if name:
        try:
            name = validate_asset_name(name)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=_t("asset_invalid_name", name=name)) from exc

    # Source branch returns early through SourceLoader normalization.
    if upload_type == "source":
        # Source registration writes current-schema fields; writing before migrations complete would omit old whole-source registrations on migration retries.
        await asyncio.to_thread(assert_project_migration_ok, project_name)
        if role == "episode":
            return await _handle_episode_source_upload(
                project_name=project_name, file=file, source_kind=source_kind, _t=_t
            )
        return await _handle_source_upload(
            project_name=project_name,
            file=file,
            on_conflict=on_conflict,
            insert_at=insert_at,
            source_kind=source_kind,
            revision=revision,
            _t=_t,
        )

    try:
        content = await file.read()

        if spec.max_bytes is not None and len(content) > spec.max_bytes:
            raise HTTPException(
                status_code=400,
                detail=_t("upload_too_large", max_mb=spec.max_bytes // (1024 * 1024)),
            )

        if upload_type == "bgm":
            return await _handle_bgm_upload(project_name, original_filename, content, _t)

        if spec.content_check == "audio":
            try:
                duration = await probe_audio_duration_seconds(content, ext)
            except ValueError as exc:
                raise HTTPException(status_code=400, detail=_t("invalid_audio_file")) from exc
            if duration is not None and not (AUDIO_REFERENCE_MIN_SECONDS <= duration <= AUDIO_REFERENCE_MAX_SECONDS):
                raise HTTPException(
                    status_code=400,
                    detail=_t(
                        "audio_duration_out_of_range",
                        min_seconds=int(AUDIO_REFERENCE_MIN_SECONDS),
                        max_seconds=int(AUDIO_REFERENCE_MAX_SECONDS),
                    ),
                )

        def _sync():
            manager = get_project_manager()
            project_dir = manager.get_project_path(project_name)

            if spec.host_bucket is not None:
                hosts = manager.load_project(project_name).get(spec.host_bucket) or {}
                # validate_asset_name normalized name to NFC; existing host keys may be NFD, so resolve existence in the coordinate system.
                if not name or resolve_asset_key(hosts, name) is None:
                    raise HTTPException(status_code=404, detail=_t(spec.host_not_found_key, name=name or ""))

            target_dir = project_dir.joinpath(*spec.subdir)
            # Stable filenames avoid jpg/png version-restoration/reference mismatches; omitted names use original stems.
            stem = name or Path(original_filename).stem
            filename = f"{stem}.png" if spec.naming == "stable_png" else f"{stem}{ext}"

            target_dir.mkdir(parents=True, exist_ok=True)

            # Save files (compress to JPEG above 2MB, otherwise validate and preserve).
            nonlocal content
            if spec.content_check == "normalize_image":
                try:
                    content, normalized_ext = normalize_uploaded_image(content, ext)
                except ValueError as exc:
                    raise HTTPException(status_code=400, detail=_t("invalid_image_file")) from exc
                filename = Path(filename).with_suffix(normalized_ext).name
            elif spec.content_check == "validate_image":
                try:
                    validate_image_bytes(content)
                except ValueError as exc:
                    raise HTTPException(status_code=400, detail=_t("invalid_image_file")) from exc

            if spec.naming == "sequenced":
                # Allocate numbered unique names using atomic exclusive placeholders: concurrent uploads for one host
                # receive distinct numbers without silently overwriting each other.
                seq = 1
                while True:
                    candidate = target_dir / f"{stem}_{seq}{ext or '.png'}"
                    try:
                        candidate.touch(exist_ok=False)
                        break
                    except FileExistsError:
                        seq += 1
                filename = candidate.name

            target_path = target_dir / filename
            relative_path = "/".join((*spec.subdir, filename))

            if spec.tracks_stale_audio and name:
                try:
                    with project_change_source("webui"):
                        manager.install_character_reference_audio(project_name, name, relative_path, content)
                except KeyError as exc:
                    raise HTTPException(status_code=404, detail=_t(spec.host_not_found_key, name=name)) from exc
            else:
                if upload_type in _FORMAL_SHEET_UPLOAD_TYPES and name:
                    with project_change_source("webui"):
                        install_manual_asset_sheet_upload(
                            project_manager=manager,
                            project_name=project_name,
                            asset_type=upload_type,
                            name=name,
                            sheet_path=relative_path,
                            content=content,
                            original_filename=original_filename,
                        )
                else:
                    with open(target_path, "wb") as f:
                        f.write(content)

                    # Update metadata.
                    if spec.metadata_setter is not None and name:
                        try:
                            with project_change_source("webui"):
                                spec.metadata_setter(manager, project_name, name, relative_path)
                        except KeyError as exc:
                            if spec.host_bucket is not None:
                                # Entry validation established host existence; concurrent-deletion races return 404,
                                # cleaning persisted files to avoid orphans.
                                # hard-delete: rollback -- Remove new uploads when host assets disappear.
                                target_path.unlink(missing_ok=True)
                                raise HTTPException(
                                    status_code=404, detail=_t(spec.host_not_found_key, name=name)
                                ) from exc
                            # Single-image types ignore absent assets: deterministic paths remain referencable when assets are created later.

            return {
                "success": True,
                "filename": filename,
                "path": relative_path,
                "url": f"/api/v1/files/{project_name}/{relative_path}",
            }

        if upload_type in _FORMAL_SHEET_UPLOAD_TYPES and name:
            # Finished sheets follow global new-content-version storage settings (local by default).
            async with formal_commit(project_name):
                return await run_sync_transaction(_sync)
        return await run_sync_transaction(_sync)

    except FileNotFoundError as exc:
        raise NotFoundError("project_not_found", name=project_name) from exc
    except (HTTPException, ApiError):
        raise
    except Exception as exc:
        logger.exception("请求处理失败")
        raise HTTPException(status_code=500, detail=_t("internal_server_error")) from exc


_BGM_ERROR_STATUS: dict[str, tuple[int, str]] = {
    "bgm_unsupported_type": (400, "unsupported_audio_type"),
    "bgm_too_large": (400, "upload_too_large"),
    "bgm_invalid_audio": (400, "invalid_audio_file"),
    "bgm_silent": (400, "bgm_silent"),
    "bgm_ffmpeg_unavailable": (503, "bgm_ffmpeg_unavailable"),
}


def _bgm_http_error(exc: BgmError, _t: Translator) -> HTTPException:
    if exc.code == "project_not_found":
        return HTTPException(status_code=404, detail=_t("project_not_found", name=exc.params.get("project", "")))
    status, key = _BGM_ERROR_STATUS[exc.code]
    return HTTPException(status_code=status, detail=_t(key, **exc.params))


async def _handle_bgm_upload(project_name: str, filename: str, content: bytes, _t: Translator) -> dict[str, object]:
    """Register project-level BGM and return its BGM-list shape."""
    await asyncio.to_thread(assert_project_migration_ok, project_name)
    try:
        track = await BgmLibraryService(get_project_manager()).upload(project_name, filename=filename, content=content)
    except BgmError as exc:
        raise _bgm_http_error(exc, _t) from exc
    return {"success": True, "bgm": _bgm_view(project_name, track)}


def _bgm_view(project_name: str, track: BgmTrack) -> dict[str, object]:
    return {
        "id": track.id,
        "name": track.name,
        "duration": track.duration,
        "gain": round(track.gain, 6),
        "path": track.file,
        "url": f"/api/v1/files/{project_name}/{track.file}",
    }


@router.get("/projects/{project_name}/bgm")
async def list_bgm(project_name: str, _t: Translator):
    """Uploaded project BGM in upload order; ``gain`` is linear gain normalizing loudness to −16 LUFS."""
    try:
        tracks = await BgmLibraryService(get_project_manager()).list(project_name)
    except BgmError as exc:
        raise _bgm_http_error(exc, _t) from exc
    return {"bgm": [_bgm_view(project_name, track) for track in tracks]}


@router.delete("/projects/{project_name}/characters/{name}/reference-audio")
async def delete_character_reference_audio(project_name: str, name: str, _t: Translator):
    """Delete character reference audio: clear its project.json field and remove the file."""
    try:

        def _sync():
            manager = get_project_manager()
            try:
                with project_change_source("webui"):
                    manager.clear_character_reference_audio(project_name, name)
            except KeyError as exc:
                raise HTTPException(status_code=404, detail=_t("character_not_found", name=name)) from exc

            return {"success": True}

        return await run_sync_transaction(_sync)

    except FileNotFoundError as exc:
        raise NotFoundError("project_not_found", name=project_name) from exc
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("请求处理失败")
        raise HTTPException(status_code=500, detail=_t("internal_server_error")) from exc


async def _handle_source_upload(
    *,
    project_name: str,
    file: UploadFile,
    on_conflict: OnConflict,
    insert_at: int | None,
    source_kind: SourceKind | None,
    revision: str | None,
    _t: Translator,
):
    """Source branch: normalize through SourceLoader into UTF-8 .txt, back up original bytes as needed, and register as whole-source files.

    Whole-source insertion and registered-file replacement use :mod:`lib.episode.source_file_changes`,
    remapping affected derived episodes."""
    original_filename = _require_filename(file, _t)
    normalized_name = f"{Path(original_filename).stem}.txt"
    if is_derived_episode_name(normalized_name):
        raise HTTPException(status_code=400, detail=_t("source_name_reserved"))

    try:
        manager = get_project_manager()
        project_dir = await asyncio.to_thread(manager.get_project_path, project_name)
    except FileNotFoundError as exc:
        raise NotFoundError("project_not_found", name=project_name) from exc

    project = await asyncio.to_thread(manager.load_project, project_name)
    if on_conflict == "replace" and f"source/{normalized_name}" in whole_source_files(project):
        text = await asyncio.to_thread(extract_uploaded_source_text, file, _t)

        def _replace() -> SourceFileChangeOutcome:
            with project_change_source("webui"):
                return replace_whole_source_file(
                    manager, project_name, normalized_name, text, source_kind=source_kind, revision=revision
                )

        try:
            await ensure_no_displaced_tasks(
                project_name,
                revision,
                lambda: replace_whole_source_file(
                    manager,
                    project_name,
                    normalized_name,
                    text,
                    source_kind=source_kind,
                    revision=revision,
                    dry_run=True,
                ),
                _t,
            )
            outcome = await run_sync_transaction(_replace)
        except SourceFileChangeError as exc:
            raise source_file_change_http_error(exc, _t) from exc
        payload = source_file_change_payload(outcome, project, _t)
        if not outcome.applied:
            return {"success": False, **payload}
        relative_path = f"source/{normalized_name}"
        return {
            "success": True,
            **payload,
            "filename": normalized_name,
            "path": relative_path,
            "url": f"/api/v1/files/{project_name}/{relative_path}",
            "normalized": True,
            "original_kept": False,
            "original_filename": original_filename,
        }

    loaded: dict[str, NormalizeResult] = {}

    def _sync() -> SourceFileChangeOutcome:
        # Stream into tmp, avoiding whole upload bodies in Python heaps;
        # UploadFile.file is SpooledTemporaryFile and already contains the complete request body.
        # Wrap with in try/finally: even if copyfileobj fails (e.g. full disks),
        # clean created tmp files to prevent /tmp leaks (delete=False does not clean automatically).
        with tempfile.NamedTemporaryFile(suffix=Path(original_filename).suffix, delete=False) as tmp:
            tmp_path = Path(tmp.name)
        try:
            with tmp_path.open("wb") as out:
                shutil.copyfileobj(file.file, out)

            def _write(source_dir: Path, undo: ExitStack) -> str:
                if on_conflict == "replace":
                    # Replaced files and original uploads enter project trash together, restored on transaction failure.
                    trash_source_file(project_dir, f"source/{normalized_name}", undo)
                result = SourceLoader.load(
                    tmp_path,
                    source_dir,
                    original_filename=original_filename,
                    on_conflict=on_conflict,
                )
                # hard-delete: rollback -- Undo this source-file write on failure.
                undo.callback(result.normalized_path.unlink, missing_ok=True)
                if result.raw_path is not None:
                    # hard-delete: rollback -- Undo this source-file write on failure.
                    undo.callback(result.raw_path.unlink, missing_ok=True)
                loaded["result"] = result
                return f"source/{result.normalized_path.name}"

            with project_change_source("webui"):
                outcome, _rel = insert_whole_source_file(
                    manager, project_name, _write, index=insert_at, source_kind=source_kind, revision=revision
                )
            return outcome
        finally:
            tmp_path.unlink(missing_ok=True)  # hard-delete: temp -- Upload temporary file.

    try:
        outcome = await run_sync_transaction(_sync)
    except FileNotFoundError as exc:
        # Race: project deletion after get_project_path succeeds, before SourceLoader writes.
        # Without mapping, generic app-level resource_not_found loses project semantics.
        # Convert only if the project directory truly disappeared; missing tmp/loader-internal files are not project errors and propagate unchanged.
        if await asyncio.to_thread(project_dir.exists):
            raise
        raise NotFoundError("project_not_found", name=project_name) from exc
    except (UnsupportedFormatError, FileSizeExceededError, SourceDecodeError, CorruptFileError) as exc:
        raise source_loader_http_error(exc, _t) from exc
    except SourceFileChangeError as exc:
        raise source_file_change_http_error(exc, _t) from exc
    except ConflictError as exc:
        raise HTTPException(
            status_code=409,
            detail={
                "existing": exc.existing,
                "suggested_name": exc.suggested_name,
                "message": _t(
                    "source_conflict",
                    existing=exc.existing,
                    suggested=exc.suggested_name,
                ),
            },
        ) from exc

    payload = source_file_change_payload(outcome, project, _t)
    if not outcome.applied:
        return {"success": False, **payload}
    result = loaded["result"]
    relative_path = f"source/{result.normalized_path.name}"
    return {
        "success": True,
        **payload,
        "filename": result.normalized_path.name,
        "path": relative_path,
        "url": f"/api/v1/files/{project_name}/{relative_path}",
        "normalized": True,
        "original_kept": result.raw_path is not None,
        "original_filename": result.original_filename,
        "used_encoding": result.used_encoding,
        "chapter_count": result.chapter_count,
    }


async def _handle_episode_source_upload(
    *, project_name: str, file: UploadFile, source_kind: SourceKind | None, _t: Translator
):
    """Episode source: normalize to text and register a source-owning episode at the end of broadcast order."""
    original_filename = _require_filename(file, _t)

    try:
        manager = get_project_manager()
        project_dir = await asyncio.to_thread(manager.get_project_path, project_name)
    except FileNotFoundError as exc:
        raise NotFoundError("project_not_found", name=project_name) from exc

    def _sync() -> int:
        with tempfile.NamedTemporaryFile(suffix=Path(original_filename).suffix, delete=False) as tmp:
            tmp_path = Path(tmp.name)
        try:
            with tmp_path.open("wb") as out:
                shutil.copyfileobj(file.file, out)
            extracted = SourceLoader.extract(tmp_path, original_filename=original_filename)
        finally:
            tmp_path.unlink(missing_ok=True)  # hard-delete: temp -- Upload temporary file.
        with project_change_source("webui"), manager.locked_source_registration(project_name) as (_dir, project, undo):
            return add_own_source_episode(project_dir, project, extracted.text, undo=undo, source_kind=source_kind)

    try:
        episode = await run_sync_transaction(_sync)
    except UnsupportedFormatError as exc:
        raise HTTPException(status_code=400, detail=_t("source_unsupported_format", ext=exc.ext)) from exc
    except FileSizeExceededError as exc:
        raise HTTPException(
            status_code=413,
            detail=_t(
                "source_too_large",
                filename=exc.filename,
                size_mb=round(exc.size_bytes / 1024 / 1024, 1),
                limit_mb=round(exc.limit_bytes / 1024 / 1024, 1),
            ),
        ) from exc
    except SourceDecodeError as exc:
        raise HTTPException(
            status_code=422,
            detail=_t("source_decode_failed", filename=exc.filename, tried=", ".join(exc.tried_encodings)),
        ) from exc
    except CorruptFileError as exc:
        raise HTTPException(
            status_code=422, detail=_t("source_corrupt_file", filename=exc.filename, reason=exc.reason)
        ) from exc
    except EpisodeSourceError as exc:
        raise episode_source_http_error(exc, _t) from exc

    relative_path = episode_source_relpath(episode)
    return {
        "success": True,
        "episode": episode,
        "filename": Path(relative_path).name,
        "path": relative_path,
        "original_filename": original_filename,
    }


@router.get("/projects/{project_name}/files")
async def list_project_files(project_name: str, _t: Translator):
    """List every project file."""
    try:

        def _sync():
            project_dir = get_project_manager().get_project_path(project_name)

            files = {
                "source": [],
                "characters": [],
                "scenes": [],
                "props": [],
                "products": [],
                "storyboards": [],
                "videos": [],
                "output": [],
            }

            for subdir, file_list in files.items():
                subdir_path = project_dir / subdir
                if not subdir_path.exists():
                    continue
                # Source subdirectories additionally list raw-backup mappings.
                raw_by_stem: dict[str, str] = {}
                if subdir == "source":
                    raw_dir = subdir_path / "raw"
                    if raw_dir.exists():
                        # sorted ensures deterministic same-stem raw handling (later overwrites earlier; lexically last wins).
                        for raw_f in sorted(raw_dir.iterdir()):
                            if raw_f.is_file():
                                raw_by_stem[raw_f.stem] = raw_f.name
                for f in subdir_path.iterdir():
                    if f.is_file() and not f.name.startswith("."):
                        entry = {
                            "name": f.name,
                            "size": f.stat().st_size,
                            "url": f"/api/v1/files/{project_name}/{subdir}/{f.name}",
                        }
                        if subdir == "source":
                            entry["raw_filename"] = raw_by_stem.get(Path(f.name).stem)
                        file_list.append(entry)

            return {"files": files}

        return await asyncio.to_thread(_sync)

    except FileNotFoundError as exc:
        raise NotFoundError("project_not_found", name=project_name) from exc
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("请求处理失败")
        raise HTTPException(status_code=500, detail=_t("internal_server_error")) from exc


@router.get("/projects/{project_name}/source/{filename}")
async def get_source_file(project_name: str, filename: str, _t: Translator):
    """Get source-file text content."""
    try:

        def _sync():
            project_dir = get_project_manager().get_project_path(project_name)

            # Safety: confine paths to project directories.
            try:
                source_path = safe_join(project_dir, "source", filename)
            except PathTraversalError as exc:
                raise HTTPException(status_code=403, detail=_t("forbidden_access")) from exc

            if not source_path.exists():
                raise HTTPException(status_code=404, detail=_t("file_not_found", path=filename))

            return source_path.read_text(encoding="utf-8")

        content = await asyncio.to_thread(_sync)
        return PlainTextResponse(content)

    except FileNotFoundError as exc:
        raise NotFoundError("project_not_found", name=project_name) from exc
    except UnicodeDecodeError as exc:
        raise HTTPException(status_code=400, detail=_t("invalid_encoding")) from exc
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("请求处理失败")
        raise HTTPException(status_code=500, detail=_t("internal_server_error")) from exc


async def _ensure_no_displaced_tasks_for_whole_source(
    project_name: str,
    filename: str,
    revision: str | None,
    preview: Callable[[ProjectManager], SourceFileChangeOutcome],
    _t: Translator,
) -> None:
    """For registered whole-source ``source/<filename>``, block in-flight tasks on episodes about to be removed or made sourceless before execution."""
    if revision is None:
        return
    manager = get_project_manager()
    project = await asyncio.to_thread(manager.load_project, project_name)
    if f"source/{filename}" in whole_source_files(project):
        await ensure_no_displaced_tasks(project_name, revision, lambda: preview(manager), _t)


@router.put("/projects/{project_name}/source/{filename}", dependencies=[Depends(require_project_migration_ok)])
async def update_source_file(
    project_name: str,
    filename: str,
    _t: Translator,
    content: str = Body(..., media_type="text/plain"),
    revision: str | None = None,
):
    """Update/create source files; register new files at the end of the whole-source list.

    Edit registered whole-source files via :func:`lib.episode.source_file_changes.edit_whole_source_file`:
    if affected episodes exist and ``revision`` is missing/stale, do not write; return
    ``status=confirmation_required`` with affected episodes. Reject ``episode_N.txt`` episode-source
    filenames here: episode pages edit episode-owned sources, while planning derives cut-episode sources."""
    if is_derived_episode_name(filename):
        raise HTTPException(status_code=400, detail=_t("source_name_reserved"))
    try:
        await _ensure_no_displaced_tasks_for_whole_source(
            project_name,
            filename,
            revision,
            lambda manager: edit_whole_source_file(
                manager, project_name, filename, content, revision=revision, dry_run=True
            ),
            _t,
        )

        def _sync():
            manager = get_project_manager()
            project_dir = manager.get_project_path(project_name)
            project = manager.load_project(project_name)
            if f"source/{filename}" in whole_source_files(project):
                outcome = edit_whole_source_file(manager, project_name, filename, content, revision=revision)
                payload = source_file_change_payload(outcome, project, _t)
                return {"success": outcome.applied, **payload, "path": f"source/{filename}"}

            with manager.locked_source_registration(project_name) as (_source_dir, project, _undo):
                # Safety: confine paths to projects, permitting nonexistent paths for creation.
                try:
                    source_path = safe_join(project_dir, "source", filename)
                except PathTraversalError as exc:
                    raise HTTPException(status_code=403, detail=_t("forbidden_access")) from exc

                created = not source_path.exists()
                source_path.write_text(content, encoding="utf-8")
                rel = f"source/{filename}"
                if created:
                    register_whole_source_file(project, rel)
            return {"success": True, "path": f"source/{filename}"}

        # Both branches mark webui: new unregistered files update registration through locked_source_registration and also broadcast.
        with project_change_source("webui"):
            return await run_sync_transaction(_sync)

    except SourceFileChangeError as exc:
        raise source_file_change_http_error(exc, _t) from exc
    except FileNotFoundError as exc:
        raise NotFoundError("project_not_found", name=project_name) from exc
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("请求处理失败")
        raise HTTPException(status_code=500, detail=_t("internal_server_error")) from exc


@router.delete("/projects/{project_name}/source/{filename}", dependencies=[Depends(require_project_migration_ok)])
async def delete_source_file(project_name: str, filename: str, _t: Translator, revision: str | None = None):
    """Delete/unregister source files: remove from whole source; source-owning episode files leave their episode sourceless.

    Delete registered whole-source files via :func:`lib.episode.source_file_changes.delete_whole_source_file`:
    if affected episodes exist and ``revision`` is missing/stale, do not delete; return
    ``status=confirmation_required`` with affected episodes. Reject deletion of planning-derived cut-episode files here."""
    try:
        await _ensure_no_displaced_tasks_for_whole_source(
            project_name,
            filename,
            revision,
            lambda manager: delete_whole_source_file(manager, project_name, filename, revision=revision, dry_run=True),
            _t,
        )

        def _sync():
            manager = get_project_manager()
            project_dir = manager.get_project_path(project_name)
            project = manager.load_project(project_name)
            if f"source/{filename}" in whole_source_files(project):
                outcome = delete_whole_source_file(manager, project_name, filename, revision=revision)
                return {"success": outcome.applied, **source_file_change_payload(outcome, project, _t)}

            with manager.locked_source_registration(project_name) as (_source_dir, project, undo):
                # Safety: confine paths to project directories.
                try:
                    source_path = safe_join(project_dir, "source", filename)
                except PathTraversalError as exc:
                    raise HTTPException(status_code=403, detail=_t("forbidden_access")) from exc
                if not source_path.exists():
                    raise HTTPException(status_code=404, detail=_t("file_not_found", path=filename))
                unregister_source_file(project, f"source/{filename}")
                # Source and same-stem original upload form one deletion record, withdrawn if registration writeback fails.
                trash_source_file(project_dir, f"source/{filename}", undo)
            return {"success": True}

        # Both branches mark webui: deleting source-owning episode files changes metadata, and deletion-record provenance likewise identifies the user.
        with project_change_source("webui"):
            return await run_sync_transaction(_sync)

    except SourceFileChangeError as exc:
        raise source_file_change_http_error(exc, _t) from exc
    except EpisodeSourceError as exc:
        raise episode_source_http_error(exc, _t, filename=filename) from exc
    except FileNotFoundError as exc:
        raise NotFoundError("project_not_found", name=project_name) from exc
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("请求处理失败")
        raise HTTPException(status_code=500, detail=_t("internal_server_error")) from exc


# ==================== Draft-file management ====================

#: Draft-endpoint stage segments. Only script planning reads/writes drafts; prompt authoring has no directly editable intermediate state.
_SCRIPT_PLAN_STAGE = "script_plan"


def _stage_files(content_mode: str, generation_mode: str | None = None) -> dict[str, str]:
    """Map draft stage names to filenames by generation_mode / content_mode.

    Ad excludes structured script_plan explicitly, as _resolve_script_plan_path / script_review.script_plan_path
    do, including reference_video projects. Return empty mappings before checking generation_mode, so
    callers report no such stage rather than wrong drama/reference filenames. reference_video uses
    generate_script_plan → script_plan_reference_units.json; others use content_mode plan filenames
    (unknown content_mode falls back to drama). Structured names come from sole source
    SCRIPT_PLAN_FILENAMES, automatically covering new content modes."""
    if content_mode == "ad":
        return {}
    if generation_mode == "reference_video":
        return {_SCRIPT_PLAN_STAGE: REFERENCE_VIDEO_SCRIPT_PLAN_FILENAME}
    return {_SCRIPT_PLAN_STAGE: SCRIPT_PLAN_FILENAMES.get(content_mode, SCRIPT_PLAN_FILENAMES["drama"])}


# Mode-aware preferred candidates by primary filename: own-mode fallbacks first, then other-mode legacy files.
# Structured .json + legacy .md candidates per mode come from one source; reference_video prefers structured .json then legacy .md
# (reading/browsing recognizes existing work in progress, writing/generation does not, matching narration/drama legacy semantics).
_SCRIPT_PLAN_FAMILY: dict[str, list[str]] = {
    SCRIPT_PLAN_FILENAMES[mode]: list(script_plan_read_candidates(mode)) for mode in SCRIPT_PLAN_FILENAMES
}
_SCRIPT_PLAN_FAMILY[REFERENCE_VIDEO_SCRIPT_PLAN_FILENAME] = [
    REFERENCE_VIDEO_SCRIPT_PLAN_FILENAME,
    REFERENCE_VIDEO_SCRIPT_PLAN_LEGACY_FILENAME,
]

# Actual script_plan candidates for fallback probing when primary is missing.
# Structured .json and old .md candidates derive from one source; retain legacy .md for browsing existing work.
# Cross-mode fallback order remains reference_video → narration → drama (historical tie-break),
# preventing changed legacy selections after consolidation; append future unlisted content modes.
_SCRIPT_PLAN_PROBE_ORDER = [
    REFERENCE_VIDEO_SCRIPT_PLAN_FILENAME,
    SCRIPT_PLAN_FILENAMES["narration"],
    SCRIPT_PLAN_FILENAMES["drama"],
]
_SCRIPT_PLAN_CANDIDATES = list(
    dict.fromkeys(
        name for key in [*_SCRIPT_PLAN_PROBE_ORDER, *_SCRIPT_PLAN_FAMILY] for name in _SCRIPT_PLAN_FAMILY[key]
    )
)


def _load_project_modes(project_name: str) -> tuple[str, str | None]:
    """Read (content_mode, generation_mode) through ProjectManager.load_project.

    Both are project-level, so draft names do not vary by episode. Missing projects return ("drama", None)
    for callers' content_mode-only branches."""
    try:
        data = get_project_manager().load_project(project_name)
    except FileNotFoundError:
        return "drama", None
    return data.get("content_mode", "drama"), data.get("generation_mode")


def _resolve_script_plan_path(drafts_dir: Path, stage: str, primary: Path) -> Path:
    """If primary is absent, prefer fallbacks from its own mode, then other-mode legacy files (mode-aware).

    For non-plan stages or existing primary paths, return primary unchanged; callers check exists() themselves."""
    if stage != _SCRIPT_PLAN_STAGE or primary.exists():
        return primary
    family = _SCRIPT_PLAN_FAMILY.get(primary.name, [primary.name])
    for candidate in dict.fromkeys([*family, *_SCRIPT_PLAN_CANDIDATES]):
        alt = drafts_dir / candidate
        if alt.exists():
            return alt
    return primary


@router.get("/projects/{project_name}/drafts/{episode}/{stage}")
async def get_draft_content(project_name: str, episode: int, stage: str, _t: Translator):
    """Get draft content for a particular step."""
    try:

        def _sync():
            project_dir = get_project_manager().get_project_path(project_name)
            content_mode, generation_mode = _load_project_modes(project_name)
            stage_files = _stage_files(content_mode, generation_mode)

            if stage not in stage_files:
                raise HTTPException(status_code=400, detail=_t("invalid_draft_stage", stage=stage))

            drafts_dir = episode_drafts_dir(project_dir, episode)
            draft_path = _resolve_script_plan_path(drafts_dir, stage, drafts_dir / stage_files[stage])

            if not draft_path.exists():
                raise HTTPException(status_code=404, detail=_t("draft_file_not_found"))

            return draft_path.read_text(encoding="utf-8")

        content = await asyncio.to_thread(_sync)
        return PlainTextResponse(content)

    except FileNotFoundError as exc:
        raise NotFoundError("project_not_found", name=project_name) from exc


@router.put("/projects/{project_name}/drafts/{episode}/{stage}")
async def update_draft_content(
    project_name: str,
    episode: int,
    stage: str,
    _t: Translator,
    content: str = Body(..., media_type="text/plain"),
):
    """Update draft content."""
    try:

        def _resolve_target() -> tuple[Path, str, Path]:
            project_dir = get_project_manager().get_project_path(project_name)
            content_mode, generation_mode = _load_project_modes(project_name)
            stage_files = _stage_files(content_mode, generation_mode)

            if stage not in stage_files:
                raise HTTPException(status_code=400, detail=_t("invalid_draft_stage", stage=stage))

            # Always write current-mode destinations; fallback is for reading/deletion only (legacy plans across mode switches).
            # Writing fallback files would leave later subagents reading empty stage_files[stage] after mode switches,
            # causing successful frontend saves followed by missing-plan generation failures.
            return project_dir, content_mode, episode_drafts_dir(project_dir, episode) / stage_files[stage]

        project_dir, content_mode, draft_path = await asyncio.to_thread(_resolve_target)

        # Web saves broadcast draft and related writes (reference-mode prompt-authoring draft cleanup) as webui,
        # avoiding frontend external-change notifications/navigation.
        with project_change_source("webui"):
            if draft_path.name == REFERENCE_VIDEO_SCRIPT_PLAN_FILENAME:
                # Formal reference-to-video plans bypass raw-text writes here; centralize persistence through
                # ScriptReviewService.save_content (structural validation, references rederivation,
                # per-path locks, and prompt_authoring cleanup), preventing unchecked write-semantic bypasses.
                # This endpoint has no baseline fingerprint and skips concurrency baseline checks, like other baseline-free direct calls.
                try:
                    parsed = json.loads(content)
                except json.JSONDecodeError as exc:
                    raise BadRequestError("script_review_invalid_content").with_diagnostic(str(exc)) from exc
                # Offload existence probes with other synchronous I/O: request coroutines await this directly,
                # so bare exists() blocks concurrent requests on the event loop.
                is_new = not await asyncio.to_thread(draft_path.exists)
                try:
                    await ScriptReviewService(get_project_manager()).save_content(project_name, episode, parsed)
                except ScriptReviewError as exc:
                    raise_review_error(exc, episode, _t)
            else:
                is_new = await run_sync_transaction(
                    _write_plain_draft, project_name, project_dir, episode, draft_path, content, _t
                )

            # Emit draft events: formal script planning is outside event-service polling; this batch is its only change signal.
            action = "created" if is_new else "updated"
            draft_label_key = "draft_segment_splitting" if content_mode == "narration" else "draft_normalized_script"
            change = {
                "entity_type": "draft",
                "action": action,
                "entity_id": f"episode_{episode}_{stage}",
                **build_change_label(draft_label_key, episode=episode),
                "episode": episode,
                "focus": {
                    "pane": "episode",
                    "episode": episode,
                },
                "important": is_new,
            }
            try:
                emit_project_change_batch(project_name, [change])
            except Exception:
                logger.warning("发送 draft 事件失败 project=%s episode=%s", project_name, episode, exc_info=True)

        return {"success": True, "path": draft_path.relative_to(project_dir).as_posix()}

    except FileNotFoundError as exc:
        raise NotFoundError("project_not_found", name=project_name) from exc


def _write_plain_draft(
    project_name: str,
    project_dir: Path,
    episode: int,
    draft_path: Path,
    content: str,
    _t: Translator,
) -> bool:
    """Persist non-reference-to-video script_plan drafts synchronously, returning whether newly created.

    Drama plans use structured .json. Before writing, match _load_drama_script_plan_content reading
    contracts: valid JSON, object top level, nonempty scenes with object items and nonempty scene_id.
    Prevent arbitrary text/empty scripts/nonobject scenes/missing or empty scene_id becoming saved
    drafts that inevitably fail generation after frontend saves succeed. Trigger by destination filename,
    not content_mode: _stage_files defaults unknown modes to drama filenames, so mode-only checks
    would let dirty values bypass validation. Narration plans use different filenames and skip this validation.

    Confirmed plans are read-only, sharing ``ScriptReviewService.save_content`` criteria/error codes."""
    draft_path.parent.mkdir(parents=True, exist_ok=True)
    if draft_path.name == SCRIPT_PLAN_FILENAMES["drama"]:
        try:
            parsed = json.loads(content)
        except json.JSONDecodeError as exc:
            raise BadRequestError("draft_invalid_json").with_diagnostic(str(exc)) from exc
        scenes = parsed.get("scenes") if isinstance(parsed, dict) else None
        if (
            not isinstance(parsed, dict)
            or not isinstance(scenes, list)
            or not scenes
            or any(not isinstance(scene, dict) for scene in scenes)
            or any(not isinstance(scene.get("scene_id"), str) or not scene.get("scene_id") for scene in scenes)
        ):
            raise BadRequestError("draft_invalid_json").with_diagnostic(
                "expected a JSON object with a non-empty 'scenes' array of objects, each with a non-empty 'scene_id'"
            )

    # Share the per-path lock with ScriptGenerator / ScriptReviewService:
    # serialize draft migration read-modify-write with Web saves.
    pm = get_project_manager()
    with pm.file_lock(draft_path):
        project = pm.load_project(project_name)
        if script_review.formal_script_plan_agent_owned(project_dir, project, episode):
            raise_review_error(ScriptReviewError("draft_agent_owned"), episode, _t)
        if script_review.formal_script_plan_confirmed(project_dir, project, episode):
            raise_review_error(ScriptReviewError("script_plan_confirmed"), episode, _t)
        is_new = not draft_path.exists()
        with script_review.formal_script_plan_write_transaction(project_dir, episode, draft_path):
            atomic_write_bytes(draft_path, content.encode("utf-8"))
    return is_new


@router.delete("/projects/{project_name}/drafts/{episode}/{stage}")
async def delete_draft(project_name: str, episode: int, stage: str, _t: Translator):
    """Delete a draft file."""
    try:

        def _sync():
            project_dir = get_project_manager().get_project_path(project_name)
            content_mode, generation_mode = _load_project_modes(project_name)
            stage_files = _stage_files(content_mode, generation_mode)

            if stage not in stage_files:
                raise HTTPException(status_code=400, detail=_t("invalid_draft_stage", stage=stage))

            drafts_dir = episode_drafts_dir(project_dir, episode)
            draft_path = _resolve_script_plan_path(drafts_dir, stage, drafts_dir / stage_files[stage])

            if script_review.delete_script_plan_file(project_dir, episode, draft_path):
                return {"success": True}
            raise HTTPException(status_code=404, detail=_t("draft_file_not_found"))

        with project_change_source("webui"):
            return await run_sync_transaction(_sync)

    except FileNotFoundError as exc:
        raise NotFoundError("project_not_found", name=project_name) from exc


# ==================== Style-reference management ====================


@router.post("/projects/{project_name}/style-image")
async def upload_style_image(project_name: str, _t: Translator, file: UploadFile = File(...)):
    """Upload a style reference and analyze style.

    1. Save to projects/{project_name}/style_reference.png.
    2. Ask the text provider to analyze style.
    3. Update project.json style_image and style_description fields."""
    original_filename = _require_filename(file, _t)

    # Check file type.
    ext = Path(original_filename).suffix.lower()
    if ext not in [".png", ".jpg", ".jpeg", ".webp"]:
        raise HTTPException(
            status_code=400,
            detail=_t("unsupported_image_type", ext=ext, allowed=".png, .jpg, .jpeg, .webp"),
        )

    try:
        content = await file.read()

        def _sync_prepare():
            project_dir = get_project_manager().get_project_path(project_name)
            try:
                content_norm, new_ext = normalize_uploaded_image(content, Path(original_filename).suffix.lower())
            except ValueError as exc:
                raise HTTPException(status_code=400, detail=_t("invalid_image_file")) from exc
            style_filename = f"style_reference{new_ext}"

            return project_dir / style_filename, style_filename, content_norm

        output_path, style_filename, content_norm = await asyncio.to_thread(_sync_prepare)

        # Analyze style with TextGenerator (automatic usage tracking).
        from lib.backends.providers import CallPurpose
        from lib.backends.text_backends.base import ImageInput, TextGenerationRequest, TextTaskType
        from lib.backends.text_generator import TextGenerator
        from lib.prompts.prompt_templates.builtin import builtin_templates

        generator = await TextGenerator.create(
            TextTaskType.STYLE_ANALYSIS, project_name, purpose=CallPurpose.STYLE_ANALYSIS
        )
        await run_sync_transaction(output_path.write_bytes, content_norm)
        result = await generator.generate(
            TextGenerationRequest(
                prompt=builtin_templates.render("text/style_analysis"), images=[ImageInput(path=output_path)]
            ),
            project_name=project_name,
        )
        style_description = result.text

        def _sync_save():
            # Update project.json in one _project_lock RMW block, preserving concurrent writes to other fields.
            def _mutate(project_data: dict) -> None:
                project_data["style_image"] = style_filename
                project_data["style_description"] = style_description
                # Strong mutual exclusion: choose custom references or templates. Besides clearing template_id,
                # clear the previous template-expanded `style` prompt too, or generation
                # would feed both template prompts and style_description to LLMs, breaking exclusivity.
                project_data.pop("style_template_id", None)
                project_data["style"] = ""

            with project_change_source("webui"):
                get_project_manager().update_project(project_name, _mutate)

        await run_sync_transaction(_sync_save)

        return {
            "success": True,
            "style_image": style_filename,
            "style_description": style_description,
            "url": f"/api/v1/files/{project_name}/{style_filename}",
        }

    except FileNotFoundError as exc:
        raise NotFoundError("project_not_found", name=project_name) from exc
    except HTTPException:
        raise
    except NoAvailableProviderError as exc:
        raise BadRequestError("text_provider_not_configured").with_diagnostic(
            {"code": "text_provider_not_configured"}
        ) from exc
    except VisionCapabilityError as e:
        raise BadRequestError(
            "vision_model_required", provider=e.provider_id, model=e.model_id, task=e.task_type.value
        ) from e
    except ModelNotInCatalogError:
        raise
    except Exception as exc:
        logger.exception("请求处理失败")
        raise HTTPException(status_code=500, detail=_t("internal_server_error")) from exc
