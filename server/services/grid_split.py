"""宫格切分服务：把宫格当前联合图切割落格到各分镜。

切分是覆写分镜格的唯一步骤，与联合图的产生（生成任务 / 手动上传 / 版本还原）解耦：
联合图内容变更只刷新联合图自身，落格必须经本服务显式执行。HTTP 路由与 SDK 工具共用。
"""

from __future__ import annotations

import asyncio
import logging
import os
import tempfile
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from lib.artifact_activation import ArtifactCurrencyResolver, register_artifact_entries_atomically
from lib.artifact_manifest import (
    ArtifactEntryRekeyReceipt,
    ArtifactKey,
    ArtifactManifestEntry,
    ArtifactStatus,
    ProjectArtifactManifestAdapter,
)
from lib.artifact_version_provenance import IMAGE_ARTIFACT_BASIS_FIELD
from lib.async_thread import run_noninterruptible_sync
from lib.grid.models import GridGeneration
from lib.grid_manager import GridManager
from lib.path_safety import safe_join
from lib.project_manager import get_project_manager
from lib.version_manager import StagedVersionCommit, VersionManager
from lib.visual_artifact_provenance import (
    GridStoryboardVisual,
    VisualReference,
    build_grid_member_storyboard_visual_basis,
    build_stale_grid_member_storyboard_visual_basis,
    snapshot_visual_references,
    visual_file_digest,
    visual_references_match_snapshot,
)
from server.services.image_artifact_currency import OptimisticMappingMemberPatch

logger = logging.getLogger(__name__)


def _register_split_entries_atomically(
    project_path: Path,
    *,
    entries: Mapping[ArtifactKey, ArtifactManifestEntry | None],
    expected_entries: Mapping[ArtifactKey, ArtifactManifestEntry | None],
) -> None:
    """Registration boundary for all cells selected by one split."""

    register_artifact_entries_atomically(project_path, entries, expected_entries=expected_entries)


class GridImageNotReadyError(Exception):
    """宫格尚无联合图（未生成完成且未上传），无法切分。"""


@dataclass
class GridSplitResult:
    updated_scene_ids: list[str]
    missing_scene_ids: list[str]
    asset_fingerprints: dict[str, int]
    _cancellation_receipt: _GridSplitCancellationReceipt | None = field(default=None, repr=False)

    def compensate_cancelled(self) -> None:
        if self._cancellation_receipt is not None:
            self._cancellation_receipt.compensate_cancelled()


@dataclass(frozen=True, slots=True)
class _GridSplitCancellationReceipt:
    versions: VersionManager
    rejections: Mapping[tuple[str, str], tuple[int, Path]]
    project_manager: Any
    project_name: str
    script_file: str
    script_patches: Mapping[str, OptimisticMappingMemberPatch]
    grid_manager: GridManager
    grid_id: str
    grid_before: Mapping[str, Any]
    grid_after: Mapping[str, Any]
    frame_indexes: Mapping[str, int]
    manifest_receipts: Mapping[str, ArtifactEntryRekeyReceipt]

    def compensate_cancelled(self) -> None:
        from lib.script_editor import resolve_items

        def _restore_sidecars(restored: frozenset[tuple[str, str]]) -> None:
            restored_ids = {resource_id for resource_type, resource_id in restored if resource_type == "storyboards"}

            def _restore_grid_and_manifest(_script_path: Path) -> None:
                def _restore_grid(current: GridGeneration) -> None:
                    before_frames = {frame["index"]: frame for frame in self.grid_before["frame_chain"]}
                    after_frames = {frame["index"]: frame for frame in self.grid_after["frame_chain"]}
                    for resource_id in restored_ids:
                        index = self.frame_indexes[resource_id]
                        frame = next((candidate for candidate in current.frame_chain if candidate.index == index), None)
                        if frame is not None and frame.image_path == after_frames[index].get("image_path"):
                            frame.image_path = before_frames[index].get("image_path")
                    if restored_ids == set(self.frame_indexes) and current.split_at == self.grid_after.get("split_at"):
                        current.split_at = self.grid_before.get("split_at")

                def _restore_manifests() -> None:
                    for resource_id in restored_ids:
                        self.manifest_receipts[resource_id].compensate()

                self.grid_manager.update(
                    self.grid_id,
                    _restore_grid,
                    on_commit=_restore_manifests,
                )

            with self.project_manager.locked_script(
                self.project_name,
                self.script_file,
                validate=False,
                on_commit=_restore_grid_and_manifest,
            ) as script:
                items, id_field, _kind = resolve_items(script)
                by_id = {str(item.get(id_field)): item for item in items if isinstance(item, dict)}
                for resource_id in restored_ids:
                    item = by_id.get(resource_id)
                    if item is not None:
                        self.script_patches[resource_id].restore(item)

        self.versions.reject_current_versions(self.rejections, on_reject=_restore_sidecars)
        remaining = [
            resource_id
            for (resource_type, resource_id), (version, _path) in self.rejections.items()
            if resource_type == "storyboards"
            and self.versions.get_current_version(resource_type, resource_id) == version
        ]
        if remaining:
            raise RuntimeError(f"grid split selections remain current after compensation: {sorted(remaining)}")


async def apply_grid_split(
    project_name: str,
    grid: GridGeneration,
    *,
    only_scene_ids: frozenset[str] | None = None,
    register_entries: Callable[..., None] | None = None,
    task_aware: bool = False,
) -> GridSplitResult:
    """按 ``grid`` 当前联合图切割并覆写各分镜格。

    - 每格覆写前旧文件先补登版本、覆写后登记新版本（source="grid_split"）；
    - frame_chain 中已不在剧本内的 scene id 跳过并告警；
    - 完成后写 ``grid.split_at`` 并广播项目变更事件（含逐格指纹供前端 cache-bust）。
    - ``only_scene_ids`` 非 None 时只落格该集合内的 scene：宫格覆盖一组分镜，但组内已有
      current/stale 分镜图的场景不该被联合图的重新渲染悄悄覆盖——``None``（HTTP 路由的整
      张重切场景）保持覆写全部 frame_chain 成员的既有行为不变。
    """
    from PIL import Image

    from lib.grid.splitter import split_grid_image
    from server.services.generation_tasks import emit_generation_success_batch, get_aspect_ratio

    pm = get_project_manager()
    project_path = await asyncio.to_thread(pm.get_project_path, project_name)

    grid_manager = GridManager(project_path)
    grid_image_file = grid_manager.image_path(grid.id)
    grid_image_path = grid.grid_image_path
    if not grid_image_path or not grid_image_file.exists():
        raise GridImageNotReadyError(f"grid {grid.id} has no grid image to split")

    versions = VersionManager(project_path)
    script_file = grid.script_file

    def _registered_grid_source() -> tuple[ArtifactKey, ArtifactManifestEntry, ArtifactStatus]:
        """联合图的登记条目与时效；准入口径是产物清单登记，盘上有图不算。

        预检与最终事务内各取一次：两次都要拿到同一条稳定的条目，才能据它派生落格产物的
        basis。取完复读一次 entry 是为了排掉取值与比对之间被改写的条目。
        """

        key = ArtifactKey.episode_grid(grid.episode, grid.id)
        adapter = ProjectArtifactManifestAdapter(project_path)
        entry = adapter.get_entry(key)
        comparison = ArtifactCurrencyResolver(project_path).compare(key, artifact_path=grid_image_path)
        if entry is None or not comparison.usable or adapter.get_entry(key) != entry:
            raise GridImageNotReadyError(f"grid {grid.id} has no registered grid image to split")
        return key, entry, comparison.status

    def _split_and_assign() -> tuple[list[str], list[str], _GridSplitCancellationReceipt | None]:
        from lib.script_editor import resolve_items

        source_status: ArtifactStatus | None = None
        source_key: ArtifactKey | None = None
        source_entry: ArtifactManifestEntry | None = None
        with pm.locked_project_script_snapshot(project_name, script_file) as (frozen_project, script):
            source_key, source_entry, source_status = _registered_grid_source()
            project_snapshot = frozen_project
        initial_items, initial_id_field, _kind = resolve_items(script)
        initial_item_by_id = {
            str(item.get(initial_id_field)): item for item in initial_items if isinstance(item, Mapping)
        }

        # 比例取记录冻结值：项目 aspect_ratio 改过之后再切历史联合图，按新比例中心裁切
        # 会把每格削掉大半（横版图按竖版切）。存量记录无该字段，回退到项目当前设置。
        video_aspect_ratio = grid.video_aspect_ratio or get_aspect_ratio(project_snapshot, "videos")

        def _snapshot_and_split() -> tuple[Path, list[Any]]:
            fd, snapshot_name = tempfile.mkstemp(
                prefix=f".{grid.id}.",
                suffix=".split-source.png",
                dir=grid_image_file.parent,
            )
            snapshot = Path(snapshot_name)
            try:
                with os.fdopen(fd, "wb") as handle:
                    handle.write(grid_image_file.read_bytes())
                # Image.open 惰性读取并持有文件句柄。先复制联合图的完整字节，再从同一快照
                # 切格与建 basis；上传/还原可在随后替换 canonical PNG，不会混入半新半旧证据。
                with Image.open(snapshot) as src:
                    src.load()
                    return snapshot, split_grid_image(src, grid.rows, grid.cols, video_aspect_ratio)
            except BaseException:
                snapshot.unlink(missing_ok=True)
                raise

        composite_snapshot, cells = _snapshot_and_split()
        staged_commits: list[StagedVersionCommit] = []
        staged_paths: list[Path] = []
        version_metadata_by_resource: dict[str, dict[str, Any]] = {}
        try:
            composite_digest = visual_file_digest(composite_snapshot)
            storyboards_dir = project_path / "storyboards"
            storyboards_dir.mkdir(parents=True, exist_ok=True)

            # batch_update_scene_assets 在任一 scene_id 未命中时整批 fail-loud 回滚——避免
            # cell.save() 已写 PNG 落盘后又因 KeyError 整批回滚留下 orphan PNG,这里先 load
            # 当前剧本拿 valid id 集合,frame_chain 中已不存在的分镜(grid plan 生成后 agent
            # split/remove 改动了剧本)跳过 cell PNG 保存 + 收集到 missing 列表 + warning。
            items, id_field, _kind = resolve_items(script)
            valid_ids = {str(item.get(id_field)) for item in items if isinstance(item, dict)}

            asset_updates: list[tuple[str, str, Any]] = []
            updated_ids: list[str] = []
            missing_ids: list[str] = []
            cell_assignments: list[tuple[int, str, str]] = []

            # Cells stay invisible until the script, complete version batch, grid
            # record, and complete Manifest claim set can all commit.
            for cell, frame in zip(cells, grid.frame_chain, strict=True):
                if frame.frame_type == "placeholder":
                    continue
                if frame.frame_type not in ("first", "transition"):
                    continue
                if not frame.next_scene_id:
                    continue

                resource_id = str(frame.next_scene_id)
                if only_scene_ids is not None and resource_id not in only_scene_ids:
                    continue
                if resource_id not in valid_ids:
                    missing_ids.append(resource_id)
                    continue

                cell_rel = f"storyboards/scene_{resource_id}.png"
                cell_path = storyboards_dir / f"scene_{resource_id}.png"
                fd, staged_name = tempfile.mkstemp(
                    prefix=f".{cell_path.stem}.",
                    suffix=f".grid-split{cell_path.suffix}",
                    dir=storyboards_dir,
                )
                staged_path = Path(staged_name)
                staged_paths.append(staged_path)
                os.close(fd)
                staged_path.unlink()
                cell.save(staged_path, format="PNG")
                version_metadata = {"source": "grid_split", "grid_id": grid.id}
                version_metadata_by_resource[resource_id] = version_metadata
                staged_commits.append(
                    StagedVersionCommit(
                        resource_type="storyboards",
                        resource_id=resource_id,
                        prompt="",
                        staged_file=staged_path,
                        current_file=cell_path,
                        metadata=version_metadata,
                    )
                )
                cell_assignments.append((frame.index, resource_id, cell_rel))
                updated_ids.append(resource_id)
                asset_updates.append((resource_id, "storyboard_image", cell_rel))
                asset_updates.append((resource_id, "grid_id", grid.id))
                asset_updates.append((resource_id, "grid_cell_index", frame.index))

            if missing_ids:
                logger.warning(
                    "grid %s: frame_chain 中以下分镜在剧本 %s 已不存在,跳过 cell 保存: %s",
                    grid.id,
                    script_file,
                    sorted(set(missing_ids)),
                )

            manifest_entries: dict[ArtifactKey, ArtifactManifestEntry | None] = {}
            manifest_receipts: dict[str, ArtifactEntryRekeyReceipt] = {}
            script_patches: dict[str, OptimisticMappingMemberPatch] = {}
            selected_versions: dict[tuple[str, str], int] = {}
            references: tuple[VisualReference, ...] | None = ()
            reference_list: list[VisualReference] = []
            for reference in grid.reference_images or []:
                try:
                    reference_path = safe_join(project_path, reference.path)
                    if not reference_path.is_file():
                        references = None
                        break
                    reference_list.append(
                        VisualReference(
                            path=reference_path,
                            role="asset_sheet",
                            logical_type=reference.ref_type,
                            logical_id=reference.name,
                            kind="sheet",
                        )
                    )
                except (OSError, TypeError, ValueError):
                    references = None
                    break
            if references is not None:
                try:
                    references = snapshot_visual_references(reference_list)
                except OSError:
                    references = None
            member_ratio = video_aspect_ratio

            def _prepare_manifest_state(current_project: dict[str, Any], current_script: dict[str, Any]) -> None:
                """Refresh the registered grid source and derived bases inside the final transaction."""

                nonlocal source_entry, source_key, source_status
                source_key, source_entry, source_status = _registered_grid_source()

                current_items, current_id_field, _kind = resolve_items(current_script)
                item_by_id = {
                    str(item.get(current_id_field)): item for item in current_items if isinstance(item, Mapping)
                }
                members: tuple[GridStoryboardVisual, ...] | None = None
                if len(set(grid.scene_ids)) == len(grid.scene_ids) and all(
                    resource_id in item_by_id for resource_id in grid.scene_ids
                ):
                    members = tuple(
                        GridStoryboardVisual(
                            resource_id=resource_id,
                            image_prompt=item_by_id[resource_id].get("image_prompt"),
                            video_prompt=item_by_id[resource_id].get("video_prompt"),
                        )
                        for resource_id in grid.scene_ids
                    )

                manifest_entries.clear()
                manifest_entries.update(
                    {ArtifactKey.episode_storyboard(grid.episode, resource_id): None for resource_id in updated_ids}
                )
                for metadata in version_metadata_by_resource.values():
                    metadata.pop(IMAGE_ARTIFACT_BASIS_FIELD, None)

                if source_status is ArtifactStatus.STALE and source_entry is not None:
                    for cell_index, resource_id, cell_rel in cell_assignments:
                        try:
                            basis = build_stale_grid_member_storyboard_visual_basis(
                                group_id=grid.id,
                                resource_id=resource_id,
                                cell_index=cell_index,
                                composite_image=composite_snapshot,
                                rows=grid.rows,
                                columns=grid.cols,
                                member_aspect_ratio=member_ratio,
                                source_grid_basis_digest=source_entry.basis_digest,
                                source_composite_digest=composite_digest,
                            )
                        except (OSError, TypeError, ValueError):
                            continue
                        manifest_entries[ArtifactKey.episode_storyboard(grid.episode, resource_id)] = (
                            ArtifactManifestEntry(
                                artifact_path=cell_rel,
                                basis_digest=basis.digest,
                            )
                        )
                        version_metadata_by_resource[resource_id][IMAGE_ARTIFACT_BASIS_FIELD] = basis.to_evidence_dict()
                elif members is not None and references is not None:
                    for cell_index, resource_id, cell_rel in cell_assignments:
                        try:
                            basis = build_grid_member_storyboard_visual_basis(
                                group_id=grid.id,
                                members=members,
                                cell_index=cell_index,
                                composite_image=composite_snapshot,
                                rows=grid.rows,
                                columns=grid.cols,
                                style=str(current_project.get("style") or ""),
                                member_aspect_ratio=member_ratio,
                                references=references,
                                source_composite_digest=composite_digest,
                            )
                        except (OSError, TypeError, ValueError):
                            continue
                        manifest_entries[ArtifactKey.episode_storyboard(grid.episode, resource_id)] = (
                            ArtifactManifestEntry(
                                artifact_path=cell_rel,
                                basis_digest=basis.digest,
                            )
                        )
                        version_metadata_by_resource[resource_id][IMAGE_ARTIFACT_BASIS_FIELD] = basis.to_evidence_dict()

            split_at = datetime.now(UTC).isoformat()
            initial_grid = grid.to_dict()
            committed_grid_box: list[GridGeneration] = []

            def _register() -> None:
                if source_key is not None and source_status is not None:
                    try:
                        source_unchanged = visual_file_digest(grid_image_file) == composite_digest
                    except OSError:
                        source_unchanged = False
                    references_unchanged = (
                        source_status is not ArtifactStatus.CURRENT
                        or references is None
                        or visual_references_match_snapshot(references)
                    )
                    latest = ArtifactCurrencyResolver(project_path).compare(
                        source_key,
                        artifact_path=grid_image_path,
                    )
                    if (
                        not source_unchanged
                        or not references_unchanged
                        or not latest.usable
                        or latest.status is not source_status
                    ):
                        raise GridImageNotReadyError(f"grid {grid.id} changed while being split")
                expected_entries = (
                    {source_key: source_entry} if source_key is not None and source_entry is not None else {}
                )
                register = register_entries or _register_split_entries_atomically
                adapter = ProjectArtifactManifestAdapter(project_path)
                manifest_before = {key: adapter.get_entry(key) for key in manifest_entries}
                register(
                    project_path,
                    entries=manifest_entries,
                    expected_entries=expected_entries,
                )
                if task_aware:
                    for resource_id in updated_ids:
                        key = ArtifactKey.episode_storyboard(grid.episode, resource_id)
                        after = adapter.get_entry(key)
                        manifest_receipts[resource_id] = ArtifactEntryRekeyReceipt(
                            adapter=adapter,
                            before={key: manifest_before[key]},
                            after={key: after},
                            changed=manifest_before[key] != after,
                        )

            def _commit_grid() -> None:
                assignment_by_index = {index: path for index, _resource_id, path in cell_assignments}

                def _mutate(current: GridGeneration) -> None:
                    if current.to_dict() != initial_grid:
                        raise RuntimeError("grid changed while its composite was being split")
                    for frame in current.frame_chain:
                        if frame.index in assignment_by_index:
                            frame.image_path = assignment_by_index[frame.index]
                    current.split_at = split_at

                committed = grid_manager.update(grid.id, _mutate, on_commit=_register)
                if committed is None:
                    raise RuntimeError(f"grid disappeared while being split: {grid.id}")
                committed_grid_box.append(committed)

            if staged_commits:

                def _activate_versions(_script_path: Path) -> None:
                    selected_versions.update(versions.commit_staged_versions(staged_commits, on_commit=_commit_grid))

                def _prepare_versions(current_script: dict[str, Any]) -> Callable[[Path], None]:
                    _prepare_manifest_state(
                        pm.load_project_readonly(project_name),
                        current_script,
                    )
                    current_items, current_id_field, _kind = resolve_items(current_script)
                    current_by_id = {
                        str(item.get(current_id_field)): item for item in current_items if isinstance(item, Mapping)
                    }
                    if task_aware:
                        for resource_id in updated_ids:
                            current_item = current_by_id[resource_id]
                            selected_assets = current_item.get("generated_assets")
                            if not isinstance(selected_assets, Mapping):
                                raise RuntimeError(f"grid split did not create generated_assets for {resource_id}")
                            script_patches[resource_id] = OptimisticMappingMemberPatch.capture(
                                initial_item_by_id[resource_id],
                                "generated_assets",
                                selected_assets,
                            )
                    return _activate_versions

                pm.batch_update_scene_assets(
                    project_name=project_name,
                    script_filename=script_file,
                    updates=asset_updates,
                    prepare_on_commit=_prepare_versions,
                )
            else:
                _commit_grid()

            if len(committed_grid_box) != 1:
                raise RuntimeError("grid split transaction skipped its grid record commit")
            committed_grid = committed_grid_box[0]
            grid.frame_chain = committed_grid.frame_chain
            grid.split_at = committed_grid.split_at
            cancellation_receipt = None
            if task_aware and selected_versions:
                cancellation_receipt = _GridSplitCancellationReceipt(
                    versions=versions,
                    rejections={
                        identity: (version, project_path / "storyboards" / f"scene_{identity[1]}.png")
                        for identity, version in selected_versions.items()
                    },
                    project_manager=pm,
                    project_name=project_name,
                    script_file=script_file,
                    script_patches=script_patches,
                    grid_manager=grid_manager,
                    grid_id=grid.id,
                    grid_before=initial_grid,
                    grid_after=committed_grid.to_dict(),
                    frame_indexes={resource_id: index for index, resource_id, _path in cell_assignments},
                    manifest_receipts=manifest_receipts,
                )
            return updated_ids, missing_ids, cancellation_receipt
        finally:
            for staged_path in staged_paths:
                staged_path.unlink(missing_ok=True)
            composite_snapshot.unlink(missing_ok=True)

    updated_ids, missing_ids, cancellation_receipt = await run_noninterruptible_sync(_split_and_assign)

    fingerprints = await run_noninterruptible_sync(
        emit_generation_success_batch,
        task_type="grid_split",
        project_name=project_name,
        resource_id=grid.id,
        payload={"script_file": script_file},
    )

    return GridSplitResult(
        updated_scene_ids=updated_ids,
        missing_scene_ids=sorted(set(missing_ids)),
        asset_fingerprints=fingerprints,
        _cancellation_receipt=cancellation_receipt,
    )
