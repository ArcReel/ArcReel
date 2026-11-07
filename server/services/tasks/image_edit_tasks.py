"""Execution layer for instruction-based image editing.

Editing semantics (``docs/adr/0050`` and GLOSSARY.md image_edit): invoke i2i with the current resource image as the sole
reference and user editing instructions as the sole prompt. New output replaces current and enters history automatically.
Do not rewrite image_prompt: editing branches the **image**, not the prompt.

Support character / scene / prop / product sheets, character derivatives and storyboard images. Current-path resolution
and resource-specific writeback reuse generation rules (asset sheet fields / script generated_assets).
:func:`resolve_current_image_rel` shares these rules between route pre-enqueue validation and executor reads."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from lib.artifacts.artifact_activation import (
    ArtifactCurrencyResolver,
    ArtifactInputClaim,
    active_artifact_currency_resolver,
    artifact_input_is_usable,
    assert_artifact_input_claims_usable,
    resolve_artifact_episode,
)
from lib.artifacts.artifact_manifest import ArtifactBasis, ArtifactKey
from lib.artifacts.image_reference_snapshot import FrozenImageReferences, freeze_image_references
from lib.artifacts.visual_artifact_provenance import (
    VisualReference,
    build_asset_sheet_visual_basis,
    build_storyboard_image_visual_basis,
)
from lib.db.base import DEFAULT_USER_ID
from lib.infra.api_errors import NotFoundError
from lib.infra.async_thread import run_noninterruptible_sync, run_sync_transaction
from lib.material.work_files import local_work_files
from lib.project.asset_derivatives import (
    DERIVATIVE_ASSET_TYPE,
    DERIVATIVE_TASK_TYPE,
    DerivativeSheetTarget,
    derivative_table,
    resolve_derivative_target,
    split_derivative_artifact_id,
)
from lib.project.asset_types import ASSET_SPECS, resolve_asset_key
from lib.project.project_manager import get_project_manager
from lib.project.resource_paths import CHARACTER_DERIVATIVE_RESOURCE_TYPE, resource_relative_path
from lib.script.script_models import get_generated_assets
from lib.script.storyboard_sequence import find_storyboard_item, get_storyboard_items
from server.services.tasks.derivative_sheet_tasks import derivative_sheet_commit_callback
from server.services.tasks.formal_image_commit import (
    FormalImageCommitOutcome,
    FormalImagePlan,
    StagedImageCommit,
    asset_sheet_formal_image_callback,
    get_aspect_ratio,
    run_formal_image_task,
    storyboard_formal_image_callback,
)

# Version source identifying instruction-based edits; the frontend displays an edit marker, like manual_upload.
IMAGE_EDIT_VERSION_SOURCE = "image_edit"


def _derivative_target(artifact_id: str) -> DerivativeSheetTarget:
    """Convert a ``本体/衍生`` ID resolved to actual persisted names back into writeback coordinates."""
    owner_key, derivative_key = split_derivative_artifact_id(artifact_id)
    return DerivativeSheetTarget(owner_key=owner_key, derivative_key=derivative_key)


#: Singular resource type for character derivatives in the editing API, alongside ASSET_SPECS keys.
#: resource_id ``本体名/衍生名`` addresses an image in the base entry’s derivative table.

# Editable resource allowlist: singular API names, derived from ASSET_SPECS except storyboards/derivatives.
EDITABLE_RESOURCE_TYPES: tuple[str, ...] = (*ASSET_SPECS.keys(), "storyboard", DERIVATIVE_TASK_TYPE)


@dataclass(frozen=True, slots=True)
class _ImageEditSource:
    """One exact current image admitted for editing and its recheck evidence."""

    resource_id: str
    artifact_path: str
    formal_claims: tuple[ArtifactInputClaim, ...]


def _build_image_edit_basis(
    *,
    resource_type: str,
    resource_id: str,
    instruction: str,
    aspect_ratio: str,
    source: VisualReference,
) -> ArtifactBasis:
    """Describe the prompt and sole image input actually used by image editing."""

    if resource_type == "storyboard":
        return build_storyboard_image_visual_basis(
            resource_id=resource_id,
            image_prompt=instruction,
            # Editing sends only the instruction; project style is not a provider input.
            style="",
            aspect_ratio=aspect_ratio,
            references=(source,),
        )
    return build_asset_sheet_visual_basis(
        # Bases/derivatives share asset-sheet basis kinds, with derivative asset IDs using base/derivative.
        asset_type=DERIVATIVE_ASSET_TYPE if resource_type == DERIVATIVE_TASK_TYPE else resource_type,
        asset_id=resource_id,
        description=instruction,
        # Asset editing likewise omits the normal generation style expansion.
        style="",
        style_description="",
        aspect_ratio=aspect_ratio,
        references=(source,),
    )


def edit_version_resource_type(resource_type: str) -> str:
    """Map singular API resource_type values to plural VersionManager/event resource types."""
    if resource_type == "storyboard":
        return "storyboards"
    if resource_type == DERIVATIVE_TASK_TYPE:
        return CHARACTER_DERIVATIVE_RESOURCE_TYPE
    return ASSET_SPECS[resource_type].bucket_key


def resolve_current_image_rel(
    project: dict[str, Any],
    resource_type: str,
    resource_id: str,
    script: dict[str, Any] | None = None,
) -> str | None:
    """Resolve the current image’s project-relative path from registered pointers only; the Artifact Manifest determines availability.

    - Assets (character / scene / prop / product): read the corresponding project.json bucket sheet field;
      missing assets raise ``KeyError``, unset sheets return ``None``.
    - Character derivatives: ``resource_id`` is ``本体名/衍生名``; read the sheet field in that derivative’s base-entry table.
      Missing bases/derivatives raise ``KeyError``; not-yet-generated images return ``None``.
    - Storyboards: read ``generated_assets.storyboard_image``, possibly ``scene_{id}_first.png`` for grids.
      No registered pointer means no artifact; never infer current from matching filenames. Missing items raise ``KeyError``."""
    return resolve_current_image_pointer(project, resource_type, resource_id, script)[1]


def resolve_current_image_pointer(
    project: dict[str, Any],
    resource_type: str,
    resource_id: str,
    script: dict[str, Any] | None,
) -> tuple[str, str | None]:
    """Resolve the canonical resource identity and its metadata-owned image pointer.

    The identity is the key the resource's versions are recorded under: the asset's on-disk
    name (NFC/NFD tolerant), ``本体/衍生`` for a derivative, the item ID for a storyboard.
    A resource that does not exist, including a malformed derivative ID, raises ``KeyError``.
    """

    if resource_type == "storyboard":
        items, id_field, _, _, _ = get_storyboard_items(script or {})
        resolved = find_storyboard_item(items, id_field, resource_id)
        if resolved is None:
            raise KeyError(f"segment not found: {resource_id}")
        item = resolved[0]
        resolved_id = str(item.get(id_field) or resource_id)
        pointer = get_generated_assets(item).get("storyboard_image")
        if isinstance(pointer, str) and pointer:
            return resolved_id, pointer
        # No registered pointer means no artifact; matching filenames do not establish storyboard ownership.
        return resolved_id, None

    if resource_type == DERIVATIVE_TASK_TYPE:
        try:
            owner_name, derivative_name = split_derivative_artifact_id(resource_id)
        except ValueError as exc:
            # IDs outside the base-name/derivative-name form identify no derivative and are treated as missing.
            raise KeyError(f"derivative not found: {resource_id}") from exc
        try:
            target = resolve_derivative_target(project, owner_name, derivative_name)
        except NotFoundError as exc:
            raise KeyError(f"derivative not found: {resource_id}") from exc
        derivative = derivative_table(project[ASSET_SPECS[DERIVATIVE_ASSET_TYPE].bucket_key][target.owner_key])[
            target.derivative_key
        ]
        sheet = derivative.get(ASSET_SPECS[DERIVATIVE_ASSET_TYPE].sheet_field)
        return target.artifact_id, sheet if isinstance(sheet, str) and sheet else None

    spec = ASSET_SPECS[resource_type]
    bucket = project.get(spec.bucket_key)
    # Request asset names and bucket keys may use NFC/NFD; resolve actual persisted keys within the comparison coordinate system.
    key = resolve_asset_key(bucket, resource_id)
    entry = bucket.get(key) if isinstance(bucket, dict) and key is not None else None
    if not isinstance(entry, dict):
        raise KeyError(f"{resource_type} not found: {resource_id}")
    sheet = entry.get(spec.sheet_field)
    return str(key), sheet if isinstance(sheet, str) and sheet else None


def resolve_usable_image_edit_source(
    *,
    project: dict[str, Any],
    project_path: Path,
    resource_type: str,
    resource_id: str,
    script: dict[str, Any] | None,
    artifact_episode: int | None,
    resolver: ArtifactCurrencyResolver,
) -> _ImageEditSource | None:
    """Select one metadata-owned current image through the active Manifest boundary.

    The exact asset-sheet or episode-storyboard claim is authoritative; missing
    claims are not selectable and blocked comparisons fail loud. The returned
    claim is retained so the executor can recheck it after async provider
    resolution and immediately before provider submission.
    """

    resolved_id, artifact_path = resolve_current_image_pointer(project, resource_type, resource_id, script)
    if not artifact_path:
        return None
    if resource_type == "storyboard":
        if type(artifact_episode) is not int or artifact_episode < 1:
            raise ValueError("artifact_episode is required for an active storyboard edit source")
        key = ArtifactKey.episode_storyboard(artifact_episode, resolved_id)
    elif resource_type == DERIVATIVE_TASK_TYPE:
        key = ArtifactKey.asset_sheet(DERIVATIVE_ASSET_TYPE, resolved_id)
    else:
        key = ArtifactKey.asset_sheet(resource_type, resolved_id)
    claims: list[ArtifactInputClaim] = []
    if not artifact_input_is_usable(
        resolver=resolver,
        key=key,
        artifact_path=artifact_path,
        claims=claims,
    ):
        return None
    return _ImageEditSource(resource_id=resolved_id, artifact_path=artifact_path, formal_claims=tuple(claims))


def _assert_selected_image_edit_source_usable(
    *,
    project_name: str,
    project_path: Path,
    resource_type: str,
    requested_resource_id: str,
    script_file: str | None,
    selected: _ImageEditSource,
) -> None:
    """Re-select the edit input from one current, canonically locked snapshot."""

    pm = get_project_manager()

    def _refresh(current_project: dict[str, Any], current_script: dict[str, Any] | None) -> _ImageEditSource | None:
        artifact_episode = (
            resolve_artifact_episode(
                project=current_project,
                script=current_script,
                script_filename=script_file,
            )
            if current_script is not None and script_file is not None
            else None
        )
        return resolve_usable_image_edit_source(
            project=current_project,
            project_path=project_path,
            resource_type=resource_type,
            resource_id=requested_resource_id,
            script=current_script,
            artifact_episode=artifact_episode,
            resolver=active_artifact_currency_resolver(project_path, current_project),
        )

    try:
        if resource_type == "storyboard":
            if script_file is None:
                raise ValueError("script_file is required for storyboard image edit admission")
            with pm.locked_project_script_snapshot(project_name, script_file) as (current_project, current_script):
                refreshed = _refresh(current_project, current_script)
        else:
            with pm.locked_project_snapshot(project_name) as current_project:
                refreshed = _refresh(current_project, None)
    except KeyError as exc:
        raise ValueError(f"selected image edit source is no longer available: {selected.artifact_path}") from exc

    if (
        refreshed is None
        or refreshed.resource_id != selected.resource_id
        or refreshed.artifact_path != selected.artifact_path
    ):
        raise ValueError(f"selected image edit source is no longer available: {selected.artifact_path}")
    if refreshed.formal_claims != selected.formal_claims:
        raise ValueError(f"selected image edit source changed since it was selected: {selected.artifact_path}")


async def execute_image_edit_task(
    project_name: str,
    resource_id: str,
    payload: dict[str, Any],
    *,
    user_id: str = DEFAULT_USER_ID,
    task_id: str | None = None,
    claimed_provider_id: str | None = None,
) -> dict[str, Any]:
    """Execute image editing: read current → i2i → new version replaces current → resource-specific writeback.

    Editing is always i2i, the image task whose type is known at enqueueing (``docs/adr/0001``): the sole reference is
    the current image, so shared execution always uses the i2i slot. Backend failure leaves current pointers/writeback
    untouched; MediaGenerator replaces output and records versions only on success. Baseline registration of the old image
    (``ensure_current_tracked`` in ``_pre_submit``) precedes the backend call, independent of its success, and is not rolled
    back on failure. This guarantees rollback to the pre-edit image and is outside edit-output version changes."""
    resource_type = str(payload.get("resource_type") or "")
    if resource_type not in EDITABLE_RESOURCE_TYPES:
        raise ValueError(f"unsupported image_edit resource_type: {resource_type}")

    instruction = payload.get("prompt")
    if not isinstance(instruction, str) or not instruction.strip():
        raise ValueError("instruction (payload.prompt) is required for image_edit task")
    instruction = instruction.strip()

    script_file = payload.get("script_file")
    if resource_type == "storyboard" and not script_file:
        raise ValueError("script_file is required for storyboard image_edit task")

    version_resource_type = edit_version_resource_type(resource_type)
    # Register snapshots immediately on creation in the thread; cancellation loses preparation returns, so registration drives cleanup.
    frozen_snapshots: list[FrozenImageReferences] = []

    def _prepare():
        pm = get_project_manager()
        _project = pm.load_project(project_name)
        _project_path = pm.get_project_path(project_name)
        _script = pm.load_script(project_name, str(script_file)) if resource_type == "storyboard" else None
        _artifact_episode = None
        if _script is not None:
            _artifact_episode = resolve_artifact_episode(
                project=_project,
                script=_script,
                script_filename=str(script_file),
            )
        _source = resolve_usable_image_edit_source(
            project=_project,
            project_path=_project_path,
            resource_type=resource_type,
            resource_id=resource_id,
            script=_script,
            artifact_episode=_artifact_episode,
            resolver=active_artifact_currency_resolver(_project_path, _project),
        )
        if _source is None:
            raise ValueError(f"no current image to edit: {resource_type}/{resource_id}")
        # Asset names may arrive as NFC/NFD; selection returns actual persisted keys, used for subsequent version records
        # and canonical paths to avoid duplicate normalization forms for the same asset’s files/history.
        return _project, _project_path, _source

    def _freeze(_project: dict[str, Any], _project_path: Path, _source: _ImageEditSource):
        _key = _source.resource_id
        _current_image = _project_path / _source.artifact_path
        _aspect_ratio = get_aspect_ratio(_project, version_resource_type)
        _frozen = freeze_image_references(
            [_current_image],
            [
                VisualReference(
                    path=_current_image,
                    role="edit_source",
                    logical_type=resource_type,
                    logical_id=_key,
                    kind="current",
                )
            ],
        )
        frozen_snapshots.append(_frozen)
        try:
            _basis = _build_image_edit_basis(
                resource_type=resource_type,
                resource_id=_key,
                instruction=instruction,
                aspect_ratio=_aspect_ratio,
                source=_frozen.visual_references[0],
            )
        except BaseException:
            _frozen.cleanup()
            raise
        return _project, _project_path, _current_image, _key, _aspect_ratio, _frozen, _basis, _source

    async def _prepare_frozen():
        # Reading scripts may persist migrations.
        prepared_project, prepared_path, source = await run_sync_transaction(_prepare)
        # Retrieve remote edit sources after cache cleanup, registering use while freezing.
        async with local_work_files(prepared_path, [source.artifact_path]):
            return await run_sync_transaction(_freeze, prepared_project, prepared_path, source)

    try:
        (
            project,
            project_path,
            current_image,
            resource_key,
            aspect_ratio,
            frozen_references,
            edit_basis,
            selected_source,
        ) = await _prepare_frozen()
    except BaseException:
        for frozen in frozen_snapshots:
            await run_noninterruptible_sync(frozen.cleanup)
        raise
    formal_claims = selected_source.formal_claims

    canonical_rel = resource_relative_path(version_resource_type, resource_key)

    async def _pre_submit(generator: Any) -> None:
        await asyncio.to_thread(assert_artifact_input_claims_usable, project_path, project, formal_claims)
        # Backfill untracked old images with neutral metadata, such as legacy-grid current images at noncanonical paths,
        # preserving pre-edit rollback and preventing generate_image_async ensure_current_tracked
        # from incorrectly attaching edit instructions/markers to old versions. Existing records make this a no-op.
        await run_sync_transaction(
            generator.versions.ensure_current_tracked,
            version_resource_type,
            resource_key,
            current_image,
            "",
        )
        await asyncio.to_thread(assert_artifact_input_claims_usable, project_path, project, formal_claims)

    async def _before_submit() -> None:
        await asyncio.to_thread(
            _assert_selected_image_edit_source_usable,
            project_name=project_name,
            project_path=project_path,
            resource_type=resource_type,
            requested_resource_id=resource_id,
            script_file=str(script_file) if script_file is not None else None,
            selected=selected_source,
        )

    def _build_commit(generator: Any, outcome_box: list[FormalImageCommitOutcome]) -> StagedImageCommit:
        if resource_type == DERIVATIVE_TASK_TYPE:
            return derivative_sheet_commit_callback(
                project_name=project_name,
                target=_derivative_target(resource_key),
                prompt=instruction,
                versions=generator.versions,
                task_id=task_id,
                basis=edit_basis,
                outcome_box=outcome_box,
                project_manager=get_project_manager(),
            )
        if resource_type == "storyboard":
            return storyboard_formal_image_callback(
                project_name=project_name,
                script_file=str(script_file),
                resource_id=resource_key,
                artifact_path=canonical_rel,
                prompt=instruction,
                versions=generator.versions,
                task_id=task_id,
                basis=edit_basis,
                outcome_box=outcome_box,
                project_manager=get_project_manager(),
            )
        return asset_sheet_formal_image_callback(
            asset_type=resource_type,
            project_name=project_name,
            resource_id=resource_key,
            sheet_path=canonical_rel,
            prompt=instruction,
            versions=generator.versions,
            task_id=task_id,
            basis=edit_basis,
            outcome_box=outcome_box,
            project_manager=get_project_manager(),
        )

    # Use only the current image as i2i reference and editing instructions as prompt, without original image_prompt or generation-path
    # reference collection. Provider and frozen basis share task-owned source-image bytes.
    return await run_formal_image_task(
        project_name=project_name,
        payload=payload,
        project=project,
        user_id=user_id,
        task_id=task_id,
        frozen_references=frozen_references,
        claimed_provider_id=claimed_provider_id,
        plan=FormalImagePlan(
            resource_type=version_resource_type,
            resource_id=resource_key,
            artifact_path=canonical_rel,
            prompt=instruction,
            aspect_ratio=aspect_ratio,
            build_commit_callback=_build_commit,
            pre_submit=_pre_submit,
            before_submit=_before_submit,
            version_metadata={"source": IMAGE_EDIT_VERSION_SOURCE},
        ),
    )
