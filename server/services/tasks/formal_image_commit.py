"""Formal image commit: write one generated image into the project and register it as an artifact.

Storyboards, asset sheets, character derivatives, grid composites, image edits and manual
storyboard uploads share this transaction: version activation, project/script metadata and
Manifest registration commit together, and a failed commit never leaves the new selection current.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from lib.artifacts.artifact_activation import (
    register_current_resource_artifact,
    register_task_current_resource_artifact,
    resolve_current_resource_artifact_basis,
)
from lib.artifacts.artifact_manifest import ArtifactBasis, ArtifactBasisDescriptor
from lib.artifacts.artifact_version_provenance import IMAGE_ARTIFACT_BASIS_FIELD
from lib.artifacts.image_artifact_currency import reject_failed_image_selection
from lib.artifacts.image_reference_snapshot import FrozenImageReferences
from lib.artifacts.video_visual_provenance import resolve_video_aspect_ratio
from lib.infra.async_thread import run_noninterruptible_sync
from lib.project.asset_types import ASSET_SPECS, AssetSpec, resolve_asset_key
from lib.project.project_manager import ProjectManager
from lib.project.resource_paths import CHARACTER_DERIVATIVE_RESOURCE_TYPE
from lib.script.storyboard_sequence import find_storyboard_item, get_storyboard_items
from server.services.tasks.generation_context import GenerationContext, ImageLaneRequest, resolve_generation_context


def register_formal_task_artifact(
    project_path: Path,
    *,
    resource_type: str,
    resource_id: str,
    script_file: str | None,
    task_id: str | None,
    artifact_path: str | None = None,
    basis: ArtifactBasis | ArtifactBasisDescriptor | None = None,
) -> None:
    """Register a formal image claim; a task run fails closed when its target is unprovable."""

    if task_id is None:
        register_current_resource_artifact(
            project_path,
            resource_type=resource_type,
            resource_id=resource_id,
            script_file=script_file,
            artifact_path=artifact_path,
            basis=basis,
        )
        return
    register_task_current_resource_artifact(
        project_path,
        resource_type=resource_type,
        resource_id=resource_id,
        script_file=script_file,
        artifact_path=artifact_path,
        basis=basis,
    )


async def run_formal_task_finalizer[T](
    finalize: Callable[[], T],
    *,
    compensate_failure: Callable[[], None] | None = None,
) -> T:
    """Finish a task's formal-write transaction, rejecting the selection when it fails."""

    def _finalize_with_compensation() -> T:
        try:
            return finalize()
        except BaseException as failure:
            if compensate_failure is not None:
                try:
                    compensate_failure()
                except BaseException as compensation_failure:
                    failure.add_note(f"formal image selection compensation also failed: {compensation_failure}")
            raise

    # A synchronous formal-write thread cannot be stopped after cancellation.
    # Always await its durable outcome before the caller leaves this boundary.
    return await run_noninterruptible_sync(_finalize_with_compensation)


def get_aspect_ratio(project: dict, resource_type: str) -> str:
    if resource_type in ("characters", "scenes", "props", "products", CHARACTER_DERIVATIVE_RESOURCE_TYPE):
        # 资产图生成必须显式指定宽高比；四类资产与角色衍生当前均固定为 16:9。
        return "16:9"
    return resolve_video_aspect_ratio(project, resource_type)


@dataclass(frozen=True, slots=True)
class FormalImageCommitOutcome:
    """Durable result produced inside the shared image activation seam."""

    version: int
    created_at: str


# 正式图提交的公共签名：活化回调 / 元数据提交器。
type StagedImageCommit = Callable[[Path, Path, Mapping[str, Any]], int]
type MetadataCommitter = Callable[[Callable[[], None]], None]


def _created_at_for_version(versions: Any, resource_type: str, resource_id: str, version: int) -> str:
    records = versions.get_versions(resource_type, resource_id).get("versions", [])
    for record in records:
        if record.get("version") == version:
            created_at = record.get("created_at")
            if isinstance(created_at, str) and created_at:
                return created_at
    raise RuntimeError("formal image version metadata is missing its creation timestamp")


def _commit_staged_formal_image(
    *,
    versions: Any,
    project_path: Path,
    resource_type: str,
    resource_id: str,
    script_file: str | None,
    artifact_path: str,
    prompt: str,
    staged_file: Path,
    current_file: Path,
    version_metadata: Mapping[str, Any],
    task_id: str | None,
    basis: ArtifactBasis | ArtifactBasisDescriptor | None,
    commit_metadata: MetadataCommitter,
) -> FormalImageCommitOutcome:
    """Commit metadata → selected bytes/version → Manifest through one nested transaction.

    Every formal image entry point supplies only its metadata mutation.  Version
    activation and registration stay centralized so no caller can publish a
    canonical file before all dependent state is ready to commit.
    """

    version_box: list[int] = []
    created_at_box: list[str] = []
    registered_version_box: list[int] = []
    resolved_basis_box: list[ArtifactBasis | ArtifactBasisDescriptor | None] = []

    def _register() -> None:
        registered_version = versions.get_current_version(resource_type, resource_id)
        if type(registered_version) is not int or registered_version < 1:
            raise RuntimeError("formal image staged activation has no selected version")
        registered_version_box.append(registered_version)
        created_at_box.append(_created_at_for_version(versions, resource_type, resource_id, registered_version))
        register_formal_task_artifact(
            project_path,
            resource_type=resource_type,
            resource_id=resource_id,
            script_file=script_file,
            task_id=task_id,
            artifact_path=artifact_path,
            basis=resolved_basis_box[0],
        )

    def _activate() -> None:
        resolved_basis = basis
        if resolved_basis is None:
            resolved_basis = resolve_current_resource_artifact_basis(
                project_path,
                resource_type=resource_type,
                resource_id=resource_id,
                script_file=script_file,
            )
        resolved_basis_box.append(resolved_basis)
        committed_metadata = dict(version_metadata)
        if IMAGE_ARTIFACT_BASIS_FIELD in committed_metadata:
            raise ValueError(f"{IMAGE_ARTIFACT_BASIS_FIELD} is reserved for formal image activation")
        if isinstance(resolved_basis, ArtifactBasis):
            committed_metadata[IMAGE_ARTIFACT_BASIS_FIELD] = resolved_basis.to_evidence_dict()
        version_box.append(
            versions.commit_staged_version(
                resource_type=resource_type,
                resource_id=resource_id,
                prompt=prompt,
                staged_file=staged_file,
                current_file=current_file,
                on_commit=_register,
                **committed_metadata,
            )
        )

    commit_metadata(_activate)
    if (
        len(version_box) != 1
        or len(registered_version_box) != 1
        or version_box != registered_version_box
        or len(created_at_box) != 1
        or len(resolved_basis_box) != 1
    ):
        raise RuntimeError("formal image metadata commit skipped staged activation")
    return FormalImageCommitOutcome(version=version_box[0], created_at=created_at_box[0])


def staged_formal_image_callback(
    *,
    versions: Any,
    project_path: Path,
    resource_type: str,
    resource_id: str,
    script_file: str | None,
    artifact_path: str,
    prompt: str,
    task_id: str | None,
    basis: ArtifactBasis | ArtifactBasisDescriptor | None,
    outcome_box: list[FormalImageCommitOutcome],
    commit_metadata: MetadataCommitter,
) -> StagedImageCommit:
    """Wrap one metadata committer into the staged activation callback of an image task."""

    def _commit(staged_file: Path, current_file: Path, version_metadata: Mapping[str, Any]) -> int:
        outcome = _commit_staged_formal_image(
            versions=versions,
            project_path=project_path,
            resource_type=resource_type,
            resource_id=resource_id,
            script_file=script_file,
            artifact_path=artifact_path,
            prompt=prompt,
            staged_file=staged_file,
            current_file=current_file,
            version_metadata=version_metadata,
            task_id=task_id,
            basis=basis,
            commit_metadata=commit_metadata,
        )
        outcome_box.append(outcome)
        return outcome.version

    return _commit


def _asset_sheet_metadata_mutator(
    *,
    spec: AssetSpec,
    resource_id: str,
    sheet_path: str,
) -> Callable[[dict[str, Any]], None]:
    """Point one asset entry at its new sheet."""

    def _mutate(project: dict[str, Any]) -> None:
        bucket = project.get(spec.bucket_key)
        key = resolve_asset_key(bucket, resource_id)
        if not isinstance(bucket, dict) or key is None:
            raise KeyError(f"{spec.label_zh} '{resource_id}' 不存在")
        entry = bucket[key]
        if not isinstance(entry, dict):
            raise ValueError(f"{spec.label_zh} '{resource_id}' metadata must be an object")
        entry[spec.sheet_field] = sheet_path

    return _mutate


def _write_storyboard_image_metadata(
    *,
    pm: ProjectManager,
    project_name: str,
    script_file: str,
    resource_id: str,
    artifact_path: str,
    on_commit: Callable[[Path], None],
) -> None:
    """Point one storyboard item at its new image."""

    with pm.locked_script(project_name, script_file, validate=False, on_commit=on_commit) as script:
        items, id_field, _char_field, _scene_field, _prop_field = get_storyboard_items(script)
        resolved = find_storyboard_item(items, id_field, resource_id)
        if resolved is None:
            raise KeyError(f"场景 '{resource_id}' 不存在")
        item, _index = resolved
        pm._set_scene_asset_in_script(script, resource_id, "storyboard_image", artifact_path)
        if not isinstance(item.get("generated_assets"), Mapping):
            raise RuntimeError("storyboard metadata commit did not produce generated_assets")


def asset_sheet_formal_image_callback(
    *,
    asset_type: str,
    project_name: str,
    resource_id: str,
    sheet_path: str,
    prompt: str,
    versions: Any,
    task_id: str | None,
    basis: ArtifactBasis | ArtifactBasisDescriptor | None,
    outcome_box: list[FormalImageCommitOutcome],
    project_manager: ProjectManager,
) -> StagedImageCommit:
    """Build the shared staged activation callback for every asset-sheet task."""

    spec = ASSET_SPECS[asset_type]
    project_path = project_manager.get_project_path(project_name)

    def _commit_metadata(activate: Callable[[], None]) -> None:
        def _activate(_project_file: Path) -> None:
            activate()

        project_manager.update_project(
            project_name,
            _asset_sheet_metadata_mutator(spec=spec, resource_id=resource_id, sheet_path=sheet_path),
            on_commit=_activate,
        )

    return staged_formal_image_callback(
        versions=versions,
        project_path=project_path,
        resource_type=spec.bucket_key,
        resource_id=resource_id,
        script_file=None,
        artifact_path=sheet_path,
        prompt=prompt,
        task_id=task_id,
        basis=basis,
        outcome_box=outcome_box,
        commit_metadata=_commit_metadata,
    )


async def finalize_formal_image_task(
    *,
    project_path: Path,
    resource_type: str,
    resource_id: str,
    script_file: str | None,
    artifact_path: str,
    generator: Any,
    version: int,
    task_id: str | None,
    basis: ArtifactBasis | ArtifactBasisDescriptor | None,
    commit_current: Callable[[Callable[[Path], None]], None],
    commit_tracked: Callable[[Callable[[Path], None]], None],
) -> str:
    """Commit one image's metadata plus selection; returns the selected version's creation time.

    ``commit_current`` serves direct calls without a queued task; ``commit_tracked`` serves task
    runs, whose registration fails closed when the target is unprovable.
    """

    def _finalize() -> str:
        created_at = generator.versions.get_versions(resource_type, resource_id)["versions"][-1]["created_at"]

        def _register(_committed_file: Path) -> None:
            register_formal_task_artifact(
                project_path,
                resource_type=resource_type,
                resource_id=resource_id,
                script_file=script_file,
                task_id=task_id,
                artifact_path=artifact_path,
                basis=basis,
            )

        commit = commit_current if task_id is None else commit_tracked
        commit(_register)
        return created_at

    def _compensate_failed_selection() -> None:
        reject_failed_image_selection(
            versions=generator.versions,
            resource_type=resource_type,
            resource_id=resource_id,
            version=version,
            current_file=project_path / artifact_path,
        )

    return await run_formal_task_finalizer(
        _finalize,
        compensate_failure=_compensate_failed_selection,
    )


async def finalize_asset_sheet_task(
    *,
    asset_type: str,
    project_name: str,
    resource_id: str,
    sheet_path: str,
    generator: Any,
    version: int,
    task_id: str | None,
    basis: ArtifactBasis | ArtifactBasisDescriptor | None = None,
    project_manager: ProjectManager,
) -> str:
    """Commit one asset sheet and its selection."""

    spec = ASSET_SPECS[asset_type]

    def _commit_current(register: Callable[[Path], None]) -> None:
        project_manager._update_asset_sheet(
            asset_type,
            project_name,
            resource_id,
            sheet_path,
            on_commit=register,
        )

    def _commit_tracked(register: Callable[[Path], None]) -> None:
        project_manager.update_project(
            project_name,
            _asset_sheet_metadata_mutator(spec=spec, resource_id=resource_id, sheet_path=sheet_path),
            on_commit=register,
        )

    return await finalize_formal_image_task(
        project_path=project_manager.get_project_path(project_name),
        resource_type=spec.bucket_key,
        resource_id=resource_id,
        script_file=None,
        artifact_path=sheet_path,
        generator=generator,
        version=version,
        task_id=task_id,
        basis=basis,
        commit_current=_commit_current,
        commit_tracked=_commit_tracked,
    )


def storyboard_formal_image_callback(
    *,
    project_name: str,
    script_file: str,
    resource_id: str,
    artifact_path: str,
    prompt: str,
    versions: Any,
    task_id: str | None,
    basis: ArtifactBasis | ArtifactBasisDescriptor | None,
    outcome_box: list[FormalImageCommitOutcome],
    project_manager: ProjectManager,
) -> StagedImageCommit:
    """Build a staged storyboard activation using the shared formal image seam."""

    project_path = project_manager.get_project_path(project_name)

    def _commit_metadata(activate: Callable[[], None]) -> None:
        def _activate(_script_path: Path) -> None:
            activate()

        _write_storyboard_image_metadata(
            pm=project_manager,
            project_name=project_name,
            script_file=script_file,
            resource_id=resource_id,
            artifact_path=artifact_path,
            on_commit=_activate,
        )

    return staged_formal_image_callback(
        versions=versions,
        project_path=project_path,
        resource_type="storyboards",
        resource_id=resource_id,
        script_file=script_file,
        artifact_path=artifact_path,
        prompt=prompt,
        task_id=task_id,
        basis=basis,
        outcome_box=outcome_box,
        commit_metadata=_commit_metadata,
    )


async def finalize_storyboard_image_task(
    *,
    project_name: str,
    script_file: str,
    resource_id: str,
    artifact_path: str,
    generator: Any,
    version: int,
    task_id: str | None,
    basis: ArtifactBasis | ArtifactBasisDescriptor | None = None,
    project_manager: ProjectManager,
) -> str:
    """Commit storyboard metadata and image selection through one shared seam."""

    def _commit_current(register: Callable[[Path], None]) -> None:
        project_manager.update_scene_asset(
            project_name=project_name,
            script_filename=script_file,
            scene_id=resource_id,
            asset_type="storyboard_image",
            asset_path=artifact_path,
            on_commit=register,
        )

    def _commit_tracked(register: Callable[[Path], None]) -> None:
        _write_storyboard_image_metadata(
            pm=project_manager,
            project_name=project_name,
            script_file=script_file,
            resource_id=resource_id,
            artifact_path=artifact_path,
            on_commit=register,
        )

    return await finalize_formal_image_task(
        project_path=project_manager.get_project_path(project_name),
        resource_type="storyboards",
        resource_id=resource_id,
        script_file=script_file,
        artifact_path=artifact_path,
        generator=generator,
        version=version,
        task_id=task_id,
        basis=basis,
        commit_current=_commit_current,
        commit_tracked=_commit_tracked,
    )


@dataclass(frozen=True, slots=True)
class FormalImagePlan:
    """Per-task differences of the shared formal image submit/activate pipeline."""

    resource_type: str
    resource_id: str
    artifact_path: str
    prompt: str
    aspect_ratio: str
    build_commit_callback: Callable[[Any, list[FormalImageCommitOutcome]], StagedImageCommit]
    finalize: Callable[[Any, int], Awaitable[str]]
    pre_submit: Callable[[], Awaitable[None]] | None = None
    before_submit: Callable[[], Awaitable[None]] | None = None
    #: 写进任务 ``result.warnings`` 的非阻断提示（如参考图超限裁剪）；为空时结果不带该键。
    warnings: tuple[dict[str, Any], ...] = ()


async def run_formal_image_task(
    *,
    project_name: str,
    payload: dict[str, Any],
    project: dict[str, Any],
    user_id: str,
    task_id: str | None,
    frozen_references: FrozenImageReferences,
    plan: FormalImagePlan,
    context: GenerationContext | None = None,
) -> dict[str, Any]:
    """Submit one formal image, then take either the staged activation or the finalizer path.

    ``context`` 供已经解析过 image lane 的调用方复用同一次解析——提示词里的参考图编号按
    backend 的上限裁剪过，重解析可能落到别的 backend、让编号与实发张数错位。不传则在提交
    前按参考图有无定 t2i / i2i 槽自行解析。
    """

    reference_images = frozen_references.reference_images
    formal_outcomes: list[FormalImageCommitOutcome] = []

    async def _submit() -> tuple[Any, tuple[Path, int]]:
        ctx = context or await resolve_generation_context(
            project_name,
            payload,
            project=project,
            user_id=user_id,
            image=ImageLaneRequest(generation_type="i2i" if reference_images else "t2i"),
        )
        if plan.pre_submit is not None:
            await plan.pre_submit()
        generator = ctx.generator
        # before_submit 只在声明了 checkpoint 的任务上出现，不声明就不落进 kwargs
        optional: dict[str, Any] = {}
        if plan.before_submit is not None:
            optional["before_submit"] = plan.before_submit
        generated = await generator.generate_image_async(
            prompt=plan.prompt,
            resource_type=plan.resource_type,
            resource_id=plan.resource_id,
            reference_images=reference_images,
            aspect_ratio=plan.aspect_ratio,
            image_size=ctx.image.resolution,
            formal_output=True,
            task_id=task_id,
            commit_formal_output=plan.build_commit_callback(generator, formal_outcomes),
            **optional,
        )
        return generator, generated

    try:
        generator, (_generated_path, version) = await _submit()
    finally:
        await run_noninterruptible_sync(frozen_references.cleanup)

    if formal_outcomes:
        outcome = formal_outcomes[0]
        version, created_at = outcome.version, outcome.created_at
    else:
        created_at = await plan.finalize(generator, version)

    result: dict[str, Any] = {
        "version": version,
        "file_path": plan.artifact_path,
        "created_at": created_at,
        "resource_type": plan.resource_type,
        "resource_id": plan.resource_id,
    }
    if plan.warnings:
        result["warnings"] = list(plan.warnings)
    return result


async def run_asset_sheet_image_task(
    *,
    asset_type: str,
    project_name: str,
    resource_id: str,
    payload: dict[str, Any],
    user_id: str,
    task_id: str | None,
    project: dict[str, Any],
    full_prompt: str,
    frozen_references: FrozenImageReferences,
    basis: ArtifactBasis | ArtifactBasisDescriptor | None,
    project_manager: ProjectManager,
) -> dict[str, Any]:
    """Run the submit/activate pipeline shared by every asset-sheet image task."""

    bucket_key = ASSET_SPECS[asset_type].bucket_key
    sheet_path = f"{bucket_key}/{resource_id}.png"

    def _build_commit(generator: Any, outcome_box: list[FormalImageCommitOutcome]) -> StagedImageCommit:
        return asset_sheet_formal_image_callback(
            asset_type=asset_type,
            project_name=project_name,
            resource_id=resource_id,
            sheet_path=sheet_path,
            prompt=full_prompt,
            versions=generator.versions,
            task_id=task_id,
            basis=basis,
            project_manager=project_manager,
            outcome_box=outcome_box,
        )

    async def _finalize(generator: Any, version: int) -> str:
        return await finalize_asset_sheet_task(
            asset_type=asset_type,
            project_name=project_name,
            resource_id=resource_id,
            sheet_path=sheet_path,
            generator=generator,
            version=version,
            task_id=task_id,
            basis=basis,
            project_manager=project_manager,
        )

    return await run_formal_image_task(
        project_name=project_name,
        payload=payload,
        project=project,
        user_id=user_id,
        task_id=task_id,
        frozen_references=frozen_references,
        plan=FormalImagePlan(
            resource_type=bucket_key,
            resource_id=resource_id,
            artifact_path=sheet_path,
            prompt=full_prompt,
            aspect_ratio=get_aspect_ratio(project, bucket_key),
            build_commit_callback=_build_commit,
            finalize=_finalize,
        ),
    )


def grid_formal_image_callback(
    *,
    project_path: Path,
    grid_manager: Any,
    grid: Any,
    resource_id: str,
    prompt: str,
    versions: Any,
    task_id: str | None,
    basis: ArtifactBasis | ArtifactBasisDescriptor,
    outcome_box: list[FormalImageCommitOutcome],
) -> StagedImageCommit:
    """Build a staged grid-composite activation through the shared image seam."""

    artifact_path = f"grids/{resource_id}.png"

    def _commit_metadata(activate: Callable[[], None]) -> None:
        def _complete(current_grid: Any) -> None:
            current_grid.grid_image_path = artifact_path
            current_grid.status = "completed"
            current_grid.split_at = None

        committed_grid = grid_manager.update_formal(resource_id, _complete, on_commit=activate)
        if committed_grid is None:
            raise ValueError(f"grid not found: {resource_id}")
        grid.grid_image_path = committed_grid.grid_image_path
        grid.status = committed_grid.status
        grid.split_at = committed_grid.split_at

    return staged_formal_image_callback(
        versions=versions,
        project_path=project_path,
        resource_type="grids",
        resource_id=resource_id,
        script_file=None,
        artifact_path=artifact_path,
        prompt=prompt,
        task_id=task_id,
        basis=basis,
        outcome_box=outcome_box,
        commit_metadata=_commit_metadata,
    )
