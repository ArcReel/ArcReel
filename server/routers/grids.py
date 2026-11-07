"""Grid-image generation API routes.

Handle generation, listing, individual queries and regeneration. All generation requests enter GenerationQueue
and execute asynchronously through GenerationWorker."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from pathlib import Path

from fastapi import APIRouter, File, HTTPException, Request, UploadFile
from pydantic import BaseModel

from lib.artifacts.artifact_activation import (
    register_current_resource_artifact,
    resolve_artifact_episode,
    resolve_current_resource_artifact_basis,
)
from lib.artifacts.artifact_version_provenance import IMAGE_ARTIFACT_BASIS_FIELD
from lib.artifacts.version_manager import VersionManager
from lib.generation.generation_queue import get_generation_queue
from lib.generation.generation_result import GenerationProblemCode
from lib.infra.api_errors import BadRequestError, ConflictError, NotFoundError
from lib.infra.async_thread import run_noninterruptible_sync, run_sync_transaction
from lib.infra.image_utils import MAX_UPLOAD_PIXELS, ImagePixelLimitError, normalize_storyboard_upload
from lib.infra.json_io import domain_error_on_value_error
from lib.material.formal_objects import formal_commit
from lib.material.work_files import material_file_present
from lib.project.project_change_hints import project_change_source
from lib.project.project_manager import get_project_manager
from lib.script.grid.grid_access import ensure_grid_writable
from lib.script.grid.grid_manager import GridManager
from lib.script.grid.grid_resolution import resolve_large_grid_allowed
from lib.script.grid.layout import grid_aspect_ratio_for, max_cell_count, video_aspect_ratio_of
from lib.script.grid.models import GridGeneration, build_grid_task_payload
from lib.script.grid.prompt_builder import pending_grid_prompt_ids
from lib.script.storyboard_sequence import get_storyboard_items
from server.auth import CurrentUser
from server.i18n import Translator
from server.services.admission.reference_admission import require_admitted_storyboard_references
from server.services.currency.upload_finalize import (
    UPLOAD_VERSION_SOURCE,
    UploadTooLargeError,
    UploadValidationError,
    stage_uploaded_bytes,
    validate_upload,
)
from server.services.grid.grid_split import GridImageNotReadyError, apply_grid_split
from server.services.grid.grid_submission import (
    BlockedScene,
    GridSubmissionPlan,
    commit_grid_submission,
    ensure_grid_submittable,
    grid_is_in_flight,
    grid_submission_section,
    plan_grid_submission,
    queue_active_grid_tasks,
)
from server.services.project.episode_display import present_request_diagnostics

router = APIRouter(prefix="/projects/{project_name}", tags=["grids"])


# ==================== Request/response models ====================


class GenerateGridRequest(BaseModel):
    script_file: str
    scene_ids: list[str] | None = None


class GenerateGridResponse(BaseModel):
    success: bool
    grid_ids: list[str]
    task_ids: list[str]
    # Return each grid’s own task rows so optimistic occupancy waits independently; a full-batch list would make every
    # grid wait for all persistence. Groups without grids are absent from the map, so callers do not mark them.
    task_ids_by_grid: dict[str, str]
    # Ready unsplit composites skipped by missing-only generation.
    unsplit_grid_ids: list[str]
    # Batch semantics: True only when every enqueue reuses existing tasks and creates none.
    deduped: bool
    message: str


def _load_admitted_grid_script(
    project_name: str,
    project: dict,
    script_file: str,
    episode: int,
) -> dict:
    """Load a grid script and prove its active project/episode binding."""

    with domain_error_on_value_error(lambda _exc: BadRequestError("invalid_script_file", name=script_file)):
        script = get_project_manager().load_script(project_name, script_file)
        script_episode = resolve_artifact_episode(
            project=project,
            script=script,
            script_filename=script_file,
        )
        if script_episode != episode:
            raise ValueError(f"script episode {script_episode!r} does not match requested episode {episode}")
    return script


# ==================== Grid generation ====================


@router.post("/generate/grid/{episode}", response_model=GenerateGridResponse)
async def generate_grid(
    project_name: str,
    episode: int,
    req: GenerateGridRequest,
    user: CurrentUser,
    _t: Translator,
):
    """Enqueue grid-composite generation and immediately return grid_ids, task_ids and per-grid mappings.

    Without scene_ids, generate only groups missing storyboard images and skip ready unsplit composites
    (``unsplit_grid_ids``). With scene_ids, regenerate containing grids. Reuse in-flight records for identical shot
    groups, deduplicating tasks without duplicate charges. Tasks create composites only; users separately initiate
    splitting after review.

    See :mod:`server.services.grid.grid_submission` for planning/persistence. Admission is whole-batch; any blocked
    shot prevents every task."""

    def _prepare() -> tuple[dict, dict, Path]:
        # Reject ad/short-film and grid-disabled projects here as defense beyond create/PATCH rejecting ad
        # grid_storyboard writes, preventing direct HTTP calls from bypassing settings to create paid tasks.
        project = _load_project_for_grid_write(project_name)
        # Traversal, unbinding and episode mismatch are bad requests returning 400 rather than fallback 500. Corrupt scripts
        # (JSONDecodeError) must not look like invalid script_file; let app catch-all return generic 500.
        script = _load_admitted_grid_script(project_name, project, req.script_file, episode)
        return project, script, get_project_manager().get_project_path(project_name)

    # Reading scripts may persist migrations.
    project, script, project_path = await run_sync_transaction(_prepare)
    queue = get_generation_queue()

    grid_ids: list[str] = []
    task_ids: list[str] = []
    task_ids_by_grid: dict[str, str] = {}
    deduped_flags: list[bool] = []
    async with grid_submission_section(project_name) as section:
        plan = await plan_grid_submission(
            project=project,
            project_path=project_path,
            script=script,
            script_file=req.script_file,
            episode=episode,
            # Empty lists mean omitted selection: missing-only generation.
            scene_ids=req.scene_ids or None,
            section=section,
            active_grid_tasks=queue_active_grid_tasks(
                queue, project_name=project_name, script_file=req.script_file, user_id=user.id
            ),
            large_grid_gate=resolve_large_grid_allowed,
        )
        _raise_for_refused_submission(project, plan)
        for submission in await run_sync_transaction(commit_grid_submission, plan, project_path):
            task = await queue.enqueue_task(
                project_name=project_name,
                task_type="grid",
                media_type="image",
                resource_id=submission.grid.id,
                payload=submission.payload,
                script_file=req.script_file,
                source="webui",
                user_id=user.id,
            )
            grid_ids.append(submission.grid.id)
            task_ids.append(task["task_id"])
            task_ids_by_grid[submission.grid.id] = task["task_id"]
            deduped_flags.append(bool(task.get("deduped", False)))

    unsplit_grid_ids = [chunk.grid.id for chunk in plan.unsplit if chunk.grid is not None]
    return GenerateGridResponse(
        success=True,
        grid_ids=grid_ids,
        task_ids=task_ids,
        task_ids_by_grid=task_ids_by_grid,
        unsplit_grid_ids=unsplit_grid_ids,
        deduped=bool(task_ids) and all(deduped_flags),
        message=_submission_message(_t, submitted=len(grid_ids), unsplit=len(unsplit_grid_ids)),
    )


def _submission_message(_t: Callable[..., str], *, submitted: int, unsplit: int) -> str:
    if submitted and unsplit:
        return _t("grid_task_submitted_with_unsplit", count=submitted, unsplit=unsplit)
    if submitted:
        return _t("grid_task_submitted", count=submitted)
    if unsplit:
        return _t("grid_unsplit_awaiting_split", unsplit=unsplit)
    return _t("grid_nothing_to_generate")


def _raise_for_refused_submission(project: dict, plan: GridSubmissionPlan) -> None:
    """Report one categorized error for whole-batch blocking, including every gap in that category."""

    if not plan.refused:
        return
    by_code: dict[str, list[BlockedScene]] = {}
    for blocked in plan.blocked:
        by_code.setdefault(blocked.problem.code, []).append(blocked)
    if missing := by_code.get(GenerationProblemCode.UNIT_NOT_FOUND):
        raise BadRequestError("segment_not_found", id=", ".join(b.scene_id for b in missing))
    if by_code.get(GenerationProblemCode.ARTIFACT_STATE_UNAVAILABLE):
        raise ConflictError("generation_artifact_state_unavailable")
    if conflicts := by_code.get(GenerationProblemCode.ACTIVE_TASK_CONFLICT):
        grid_ids = dict.fromkeys(gid for b in conflicts for gid in b.problem.params.get("grid_ids", []))
        raise ConflictError("grid_generation_in_progress", grid_id=", ".join(grid_ids))
    require_admitted_storyboard_references(project, plan.admission_items)
    _require_grid_prompts_written(list(plan.admission_items), plan.id_field)
    raise RuntimeError(f"grid submission refused without a reportable reason: {sorted(by_code)}")


# ==================== Grid-tier capabilities ====================


class GridCapabilityResponse(BaseModel):
    large_grid_allowed: bool
    max_cell_count: int


@router.get("/grid-capability", response_model=GridCapabilityResponse)
async def get_grid_capability(project_name: str):
    """Current project grid-tier limit.

    Frontend batch previews mirror backend tiers from this value; separate 4K gating would cause batch-count drift.
    The path stays outside ``/grids/`` to avoid matching conflicts with ``/grids/{grid_id}``."""

    def _load() -> dict:
        with domain_error_on_value_error(lambda _exc: BadRequestError("invalid_project_name", name=project_name)):
            return get_project_manager().load_project(project_name)

    project = await asyncio.to_thread(_load)
    allowed = await resolve_large_grid_allowed(project)
    return GridCapabilityResponse(
        large_grid_allowed=allowed,
        max_cell_count=max_cell_count(allow_large_grid=allowed),
    )


# ==================== Grid listing ====================


@router.get("/grids")
async def list_grids(project_name: str, request: Request, _t: Translator):
    """List all grid records in the project."""

    def _list() -> list[dict]:
        return [g.to_dict() for g in GridManager(_grid_project_path(project_name)).list_all()]

    return await present_request_diagnostics(await asyncio.to_thread(_list), request, _t)


# ==================== Grid details ====================


def _grid_project_path(project_name: str) -> Path:
    try:
        return get_project_manager().get_project_path(project_name)
    except ValueError as exc:
        raise BadRequestError("invalid_project_name", name=project_name) from exc


def _load_grid_or_404(project_path: Path, grid_id: str) -> GridGeneration:
    """Read a grid record by ID; invalid ID formats and missing records both return 404 without format details."""
    try:
        grid = GridManager(project_path).get(grid_id)
    except ValueError as exc:
        raise NotFoundError("grid_not_found", grid_id=grid_id) from exc
    if grid is None:
        raise NotFoundError("grid_not_found", grid_id=grid_id)
    return grid


@router.get("/grids/{grid_id}")
async def get_grid(project_name: str, grid_id: str, request: Request, _t: Translator):
    """Get one grid record."""
    grid = await asyncio.to_thread(lambda: _load_grid_or_404(_grid_project_path(project_name), grid_id))
    return await present_request_diagnostics(grid.to_dict(), request, _t)


# ==================== Grid regeneration ====================


def _require_grid_prompts_written(items: list[dict], id_field: str) -> None:
    """Reject any pending ``image_prompt``: composites combine all cell prompts, so one missing cell prevents the whole image."""
    pending_ids = pending_grid_prompt_ids(items, id_field)
    if pending_ids:
        raise ConflictError("script_prompt_pending", segment_id=", ".join(pending_ids))


def _load_project_for_grid_write(project_name: str) -> dict:
    """Load the project and enforce grid-write gates, sharing ``ensure_grid_writable`` with version restoration."""
    # Corrupt project.json (JSONDecodeError) is not an invalid project name; let app catch-all return generic 500.
    with domain_error_on_value_error(lambda _exc: BadRequestError("invalid_project_name", name=project_name)):
        project = get_project_manager().load_project(project_name)
    ensure_grid_writable(project)
    return project


def _grid_write_target(project_name: str) -> tuple[dict, Path]:
    """Project and directory for grid writes."""
    project = _load_project_for_grid_write(project_name)
    return project, get_project_manager().get_project_path(project_name)


async def _ensure_grid_idle(project_name: str, grid: GridGeneration, user_id: str) -> None:
    """Reject splitting/uploads for in-flight pending/generating grids: worker completion overwrites composites and
    would race uploads or splits of old content. Only active queued tasks establish in-flight state. User actions
    may take over orphan intermediate records after cancellation, restart loss or enqueue failure."""
    if await grid_is_in_flight(
        grid,
        queue=get_generation_queue(),
        project_name=project_name,
        user_id=user_id,
    ):
        raise ConflictError("grid_generation_in_progress", grid_id=grid.id)


@router.post("/grids/{grid_id}/regenerate")
async def regenerate_grid(project_name: str, grid_id: str, user: CurrentUser):
    """Reset grid state and requeue composite generation, without implicit splitting, which requires separate explicit execution.

    Read records, set pending and enqueue inside one submission section, preventing other submissions from removing
    the record midway or observing pending without a task."""
    project, project_path = await asyncio.to_thread(_grid_write_target, project_name)
    queue = get_generation_queue()
    async with grid_submission_section(project_name):

        def _load_grid_and_script() -> tuple[GridGeneration, dict]:
            grid = _load_grid_or_404(project_path, grid_id)
            return grid, _load_admitted_grid_script(project_name, project, grid.script_file, grid.episode)

        # Reading scripts may persist migrations.
        grid, script = await run_sync_transaction(_load_grid_and_script)
        ensure_grid_submittable(project, script)
        items, id_field, _, _, _ = get_storyboard_items(script)
        # Regeneration is another paid image: share initial admission using the record’s frozen storyboard set.
        # If scripts changed since generation, use current gaps, matching worker request reconstruction.
        scene_ids = set(grid.scene_ids)
        members = [item for item in items if str(item.get(id_field, "")) in scene_ids]
        require_admitted_storyboard_references(project, members)
        _require_grid_prompts_written(members, id_field)

        # Regeneration retains frozen rows/cols/ratio. Workers rebuild provider prompts and provenance basis from
        # the same current script/style/frozen layout; queued prompts are compatibility-only, never
        # a second authority detached from current basis. Legacy records without frozen ratios use
        # current ratios and backfill them; users wanting new-ratio layouts must rerun planning.
        aspect_ratio = grid.video_aspect_ratio or video_aspect_ratio_of(project)
        grid_aspect_ratio = grid_aspect_ratio_for(grid.rows, grid.cols, aspect_ratio)

        grid.status = "pending"
        grid.error_message = None
        # Clear old metadata; execute_grid_task repopulates according to needs_i2i.
        grid.provider = ""
        grid.model = ""
        # Backfill frozen legacy values; existing values make this an identity write.
        grid.video_aspect_ratio = aspect_ratio
        await run_sync_transaction(lambda: GridManager(project_path).save(grid))
        task = await queue.enqueue_task(
            project_name=project_name,
            task_type="grid",
            media_type="image",
            resource_id=grid.id,
            payload=build_grid_task_payload(
                prompt=grid.prompt,
                script_file=grid.script_file,
                scene_ids=grid.scene_ids,
                grid_size=grid.grid_size,
                rows=grid.rows,
                cols=grid.cols,
                grid_aspect_ratio=grid_aspect_ratio,
                video_aspect_ratio=aspect_ratio,
            ),
            script_file=grid.script_file,
            source="webui",
            user_id=user.id,
        )

    return {"success": True, "task_id": task["task_id"], "deduped": task.get("deduped", False)}


# ==================== Splitting into cells ====================


@router.post("/grids/{grid_id}/split")
async def split_grid(project_name: str, grid_id: str, user: CurrentUser):
    """Split the current composite and overwrite storyboard cells, the only cell-overwrite operation; execute directly without confirmation.

    Register old versions before each overwrite and new versions afterward. Skip frame_chain scene IDs absent from
    the script, returning missing_scene_ids. Bad splits can be rolled back per cell through version history."""
    _, project_path = await asyncio.to_thread(_grid_write_target, project_name)
    async with grid_submission_section(project_name):
        grid = await asyncio.to_thread(_load_grid_or_404, project_path, grid_id)
        await _ensure_grid_idle(project_name, grid, user.id)
        # Remote composites with cleared caches are also ready; retrieve through the service before splitting.
        if not grid.grid_image_path or not await asyncio.to_thread(
            lambda: material_file_present(GridManager(project_path).image_path(grid_id))
        ):
            raise BadRequestError("grid_image_not_ready", grid_id=grid_id)

        try:
            with project_change_source("webui"):
                result = await apply_grid_split(project_name, grid)
        except GridImageNotReadyError as exc:
            # Service fallback covers concurrent file deletion after preflight.
            raise BadRequestError("grid_image_not_ready", grid_id=grid_id) from exc

    return {
        "success": True,
        "split_at": grid.split_at,
        "updated_scene_ids": result.updated_scene_ids,
        "missing_scene_ids": result.missing_scene_ids,
        "asset_fingerprints": result.asset_fingerprints,
    }


# ==================== Composite uploads ====================


@router.post("/grids/{grid_id}/upload")
async def upload_grid_image(
    project_name: str,
    grid_id: str,
    user: CurrentUser,
    _t: Translator,
    file: UploadFile = File(...),
):
    """Upload a composite to replace the current grid image.

    Only normalize format: PNG conversion/EXIF orientation, without resizing or validating rows×cols layout, which
    remains the user’s responsibility. Register a new grids version without splitting or touching storyboard cells."""

    def _prepare() -> tuple[dict, Path, GridGeneration]:
        project, project_path = _grid_write_target(project_name)
        return project, project_path, _load_grid_or_404(project_path, grid_id)

    project, project_path, grid = await asyncio.to_thread(_prepare)
    await _ensure_grid_idle(project_name, grid, user.id)
    aspect_ratio = video_aspect_ratio_of(project)

    try:
        max_bytes = validate_upload(file.filename, file.size, kind="image")
        # Bound in-memory bytes to prevent OOM when Content-Length is absent/bypassed.
        content = await file.read(max_bytes + 1)
        if len(content) > max_bytes:
            raise UploadTooLargeError(max_bytes)
    except UploadValidationError as e:
        raise HTTPException(status_code=e.status_code, detail=_t(e.key, **e.params)) from e
    try:
        png_bytes = await asyncio.to_thread(normalize_storyboard_upload, content, max_long_edge=None)
    except ImagePixelLimitError as exc:
        raise BadRequestError("image_pixels_too_large", max_megapixels=MAX_UPLOAD_PIXELS // 1_000_000) from exc
    except ValueError as exc:
        raise BadRequestError("invalid_image_file") from exc

    # Register staging immediately; cancellation loses staging returns, so finally cleans via registration.
    staged_files: list[Path] = []

    def _stage() -> Path:
        target = GridManager(project_path).image_path(grid_id)
        staged_files.append(stage_uploaded_bytes(png_bytes, target))
        return target

    try:
        with project_change_source("webui"):
            target = await run_sync_transaction(_stage)
        staged_file = staged_files[0]
        async with grid_submission_section(project_name):
            # Enqueueing/record writes share one critical section; reread and probe again after acquiring it
            # to prevent regeneration between an earlier orphan decision and submission.
            current_grid = await asyncio.to_thread(_load_grid_or_404, project_path, grid_id)
            await _ensure_grid_idle(project_name, current_grid, user.id)

            def _commit() -> int:
                grid_manager = GridManager(project_path)
                versions = VersionManager(project_path)
                version_box: list[int] = []

                def _replace_record(current_grid: GridGeneration) -> None:
                    # Manual replacement equals successful composite output, returning failed records to ready;
                    # changed content clears split_at, marking it awaiting explicit splitting.
                    current_grid.status = "completed"
                    current_grid.error_message = None
                    current_grid.mark_composite_replaced()
                    # Upload layout uses the current project ratio and updates frozen values; retaining old values after
                    # a ratio change would center-crop new images with the old ratio.
                    current_grid.video_aspect_ratio = aspect_ratio

                def _activate() -> None:
                    basis = resolve_current_resource_artifact_basis(
                        project_path,
                        resource_type="grids",
                        resource_id=grid_id,
                    )
                    metadata: dict[str, object] = {"source": UPLOAD_VERSION_SOURCE}
                    if file.filename:
                        metadata["original_filename"] = file.filename
                    if basis is not None:
                        metadata[IMAGE_ARTIFACT_BASIS_FIELD] = basis.to_evidence_dict()

                    def _register() -> None:
                        register_current_resource_artifact(
                            project_path,
                            resource_type="grids",
                            resource_id=grid_id,
                            basis=basis,
                        )

                    version_box.append(
                        versions.commit_staged_version(
                            resource_type="grids",
                            resource_id=grid_id,
                            prompt="",
                            staged_file=staged_file,
                            current_file=target,
                            on_commit=_register,
                            **metadata,
                        )
                    )

                with project_change_source("webui"):
                    committed = grid_manager.update_formal(grid_id, _replace_record, on_commit=_activate)
                    if committed is None or len(version_box) != 1:
                        raise RuntimeError("grid upload metadata commit skipped staged activation")
                    return version_box[0]

            # Save new versions according to global storage; unconfirmed remote persistence publishes nothing, preserving records/selection.
            async with formal_commit(project_name):
                version = await run_noninterruptible_sync(_commit)

        with project_change_source("webui"):
            from server.services.tasks.generation_tasks import emit_generation_success_batch

            # Reading scripts may persist migrations.
            fingerprints = await run_sync_transaction(
                emit_generation_success_batch,
                task_type="grid",
                project_name=project_name,
                resource_id=grid_id,
                payload={"script_file": grid.script_file},
            )
    finally:
        for path in staged_files:
            await asyncio.to_thread(path.unlink, missing_ok=True)  # hard-delete: temp -- staged upload file

    return {
        "success": True,
        "path": f"grids/{grid_id}.png",
        "version": version,
        "asset_fingerprints": fingerprints,
    }
