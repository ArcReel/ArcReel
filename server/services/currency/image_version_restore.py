"""Image version restoration shared by the editor and project-scoped director tools."""

from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

from lib.artifacts.artifact_activation import (
    forget_current_resource_artifact,
    forget_unbound_grid_artifacts,
    forget_unbound_storyboard_artifacts,
    register_current_resource_artifact,
)
from lib.artifacts.artifact_version_provenance import parse_image_version_basis
from lib.artifacts.formal_write import project_metadata_lock
from lib.artifacts.version_manager import MANUAL_UPLOAD_VERSION_SOURCE, VersionManager
from lib.project.asset_derivatives import DerivativeSheetTarget, split_derivative_artifact_id
from lib.project.project_change_hints import project_change_source
from lib.project.project_manager import ProjectManager
from lib.project.resource_paths import CHARACTER_DERIVATIVE_RESOURCE_TYPE
from server.services.tasks.derivative_sheet_tasks import point_derivative_at_sheet


def _sync_grid_record(
    project_path: Path,
    resource_id: str,
    *,
    on_commit: Callable[[], None] | None = None,
    on_miss: Callable[[], None] | None = None,
) -> None:
    """还原联合图后复位宫格记录：内容已换回历史版本，旧的落格结果不再对应当前图。

    只动宫格记录自身（split_at / 失败态），不同步剧本或分镜——落格由切分端点显式执行；
    frame_chain 原样保留。记录缺失/损坏时跳过：联合图文件已还原成功，记录属 best-effort。

    还原本身不设在途闸门（与分镜图还原同口径），复位口径见
    ``GridGeneration.mark_composite_replaced``：生成在途时保留在途态，否则记录会谎报空闲。
    """
    from lib.script.grid.grid_manager import GridManager

    manager = GridManager(project_path)
    manager.update_formal(
        resource_id,
        lambda grid: grid.mark_composite_replaced(),
        on_commit=on_commit,
        on_miss=on_miss,
        ignore_invalid=True,
    )


# resource_type（复数，URL 段）→ asset_type（单数，ASSET_SPECS 键）
_RESOURCE_TO_ASSET_TYPE: dict[str, str] = {
    "characters": "character",
    "scenes": "scene",
    "props": "prop",
    "products": "product",
}
# 资产图、衍生资产图与分镜图：还原到手动上传记录时按上传字节重新登记。
_UPLOAD_CLAIMED_RESOURCE_TYPES = frozenset(
    {*_RESOURCE_TO_ASSET_TYPE, CHARACTER_DERIVATIVE_RESOURCE_TYPE, "storyboards"}
)


def _commit_non_typed_restore_claim(
    *,
    resource_type: str,
    resource_id: str,
    file_path: str,
    project_path: Path,
    record: Mapping[str, Any] | None,
    owner_present: bool,
) -> None:
    """Apply the claim half of a non-typed restore inside its metadata locks."""

    if not owner_present:
        if resource_type == "storyboards":
            forget_unbound_storyboard_artifacts(project_path, resource_id)
        elif resource_type == "grids":
            forget_unbound_grid_artifacts(project_path, resource_id)
        else:
            forget_current_resource_artifact(
                project_path,
                resource_type=resource_type,
                resource_id=resource_id,
            )
        return

    if resource_type in _UPLOAD_CLAIMED_RESOURCE_TYPES and (record or {}).get("source") == MANUAL_UPLOAD_VERSION_SOURCE:
        # 还原到作为成品带入的资产图、衍生资产图（作者上传或从资产库应用）或作者上传的分镜图：
        # 选中的已是这条手动上传记录，规划器按上传字节投影依据，投影不出即遗忘。
        register_current_resource_artifact(
            project_path,
            resource_type=resource_type,
            resource_id=resource_id,
        )
        return

    try:
        basis = parse_image_version_basis(resource_type, resource_id, record or {})
    except (TypeError, ValueError):
        basis = None
    if basis is None:
        forget_current_resource_artifact(
            project_path,
            resource_type=resource_type,
            resource_id=resource_id,
        )
        return
    register_current_resource_artifact(
        project_path,
        resource_type=resource_type,
        resource_id=resource_id,
        artifact_path=file_path,
        basis=basis,
    )


def restore_non_typed_version(
    *,
    versions: VersionManager,
    resource_type: str,
    project_name: str,
    resource_id: str,
    version: int,
    current_file: Path,
    file_path: str,
    project_path: Path,
    project_manager: ProjectManager,
) -> dict[str, Any]:
    """Restore metadata → version bytes/pointer → claim in canonical lock order.

    Asset rename, grid split, formal generation, and typed restore all acquire
    script/file/project locks before the versions lock. Non-typed restore enters
    the same metadata transaction first so none of those paths can form an ABBA
    cycle while their nested rollback scopes remain intact.
    """

    manager = project_manager
    restored: list[dict[str, Any]] = []

    def _restore(*, owner_present: bool) -> None:
        def _commit_claim(record: dict[str, Any]) -> None:
            _commit_non_typed_restore_claim(
                resource_type=resource_type,
                resource_id=resource_id,
                file_path=file_path,
                project_path=project_path,
                record=record,
                owner_present=owner_present,
            )

        restored.append(
            versions.restore_version(
                resource_type=resource_type,
                resource_id=resource_id,
                version=version,
                current_file=current_file,
                on_restore=_commit_claim,
            )
        )

    if resource_type == "storyboards":
        scripts_dir = project_path / "scripts"
        script_names = [path.name for path in sorted(scripts_dir.glob("*.json"))] if scripts_dir.exists() else []
        with project_change_source("webui"):
            manager.update_scene_asset_across_scripts(
                project_name,
                script_names,
                resource_id,
                "storyboard_image",
                file_path,
                on_commit=lambda: _restore(owner_present=True),
                on_miss=lambda: _restore(owner_present=False),
            )
    elif resource_type == CHARACTER_DERIVATIVE_RESOURCE_TYPE:
        owner_key, derivative_key = split_derivative_artifact_id(resource_id)
        try:
            with project_change_source("webui"):
                point_derivative_at_sheet(
                    project_name=project_name,
                    target=DerivativeSheetTarget(owner_key=owner_key, derivative_key=derivative_key),
                    on_commit=lambda _project_file: _restore(owner_present=True),
                    project_manager=manager,
                )
        except KeyError:
            with project_metadata_lock(project_path):
                _restore(owner_present=False)
    elif (asset_type := _RESOURCE_TO_ASSET_TYPE.get(resource_type)) is not None:
        try:
            with project_change_source("webui"):
                manager._update_asset_sheet(
                    asset_type,
                    project_name,
                    resource_id,
                    file_path,
                    on_commit=lambda _project_file: _restore(owner_present=True),
                )
        except KeyError:
            with project_metadata_lock(project_path):
                _restore(owner_present=False)
    elif resource_type == "grids":
        _sync_grid_record(
            project_path,
            resource_id,
            on_commit=lambda: _restore(owner_present=True),
            on_miss=lambda: _restore(owner_present=False),
        )
    else:
        with project_metadata_lock(project_path):
            _restore(owner_present=True)

    if len(restored) != 1:
        raise RuntimeError("non-typed restore metadata transaction skipped version selection")
    return restored[0]
