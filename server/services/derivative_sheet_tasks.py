"""角色衍生资产图的生成任务执行层（见 ``docs/adr/0072``）。

一次衍生资产图生成就是对**本体资产图**的一次图片编辑：本体图是唯一参考图，衍生的外观
变化描述加固定守卫是唯一 prompt（守卫要求保持三视图版式与其余外观不变）。因此它必然
i2i，与 ``image_edit`` 同属入队即知 capability 的例外。

产物坐标与本体资产图同一族：清单键 ``asset_sheet("character", "本体/衍生")``、版本资源类型
``character_derivatives``、落盘 ``characters/derivatives/{本体}/{衍生}.png``。正式产物的
提交、版本活化与清单登记全部复用资产图任务的既有缝，本模块只提供「衍生自己的元数据写回」
与「以本体资产图为输入」这两处差异。

指令与依据都在执行时按当前项目状态重算，不吃入队时的快照：两者必须同源，否则衍生图的
登记依据会与它实际收到的指令不符，规范状态比对随即恒判过期。
"""

from __future__ import annotations

import asyncio
import copy
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

from lib.api_errors import BadRequestError
from lib.artifact_activation import (
    ArtifactInputClaim,
    active_artifact_currency_resolver,
    artifact_input_is_usable,
    assert_artifact_input_claims_usable,
)
from lib.artifact_manifest import ArtifactBasis, ArtifactBasisDescriptor, ArtifactKey
from lib.asset_derivatives import (
    DERIVATIVE_ASSET_TYPE,
    DerivativeSheetTarget,
    build_derivative_sheet_basis,
    derivative_source_reference,
    resolve_derivative_sheet_source,
    split_derivative_artifact_id,
)
from lib.asset_types import ASSET_SPECS, DERIVATIVES_FIELD, resolve_asset_key
from lib.db.base import DEFAULT_USER_ID
from lib.image_reference_snapshot import freeze_image_references
from lib.project_manager import ProjectManager
from lib.prompt_builders import build_character_derivative_prompt
from lib.resource_paths import CHARACTER_DERIVATIVE_RESOURCE_TYPE
from server.services.generation_tasks import (
    _CancellationReceipt,
    _finalize_formal_image_task,
    _FormalImageCommitOutcome,
    _FormalImagePlan,
    _MetadataCompensator,
    _run_formal_image_task,
    _staged_formal_image_callback,
    _StagedImageCommit,
    get_aspect_ratio,
    get_project_manager,
)
from server.services.image_artifact_currency import OptimisticMappingPatch

_SPEC = ASSET_SPECS[DERIVATIVE_ASSET_TYPE]


def build_derivative_sheet_instruction(description: str) -> str:
    """把衍生的外观变化描述包成发往图像模型的编辑指令。"""
    return build_character_derivative_prompt(description)


def _locate_derivative(project: Mapping[str, Any], owner_name: str, derivative_name: str) -> dict[str, Any]:
    """在项目内按落盘真名取到衍生条目本身；任一层缺失抛 ``KeyError``。"""
    bucket = project.get(_SPEC.bucket_key)
    owner_key = resolve_asset_key(bucket, owner_name)
    entry = bucket[owner_key] if isinstance(bucket, dict) and owner_key is not None else None
    if not isinstance(entry, dict):
        raise KeyError(f"{_SPEC.label_zh} '{owner_name}' 不存在")
    table = entry.get(DERIVATIVES_FIELD)
    derivative_key = resolve_asset_key(table, derivative_name)
    derivative = table[derivative_key] if isinstance(table, dict) and derivative_key is not None else None
    if not isinstance(derivative, dict):
        raise KeyError(f"衍生 '{derivative_name}' 不存在")
    return derivative


def _derivative_sheet_metadata_mutator(
    *,
    owner_name: str,
    derivative_name: str,
    sheet_path: str,
    mutation_box: list[OptimisticMappingPatch],
) -> Callable[[dict[str, Any]], None]:
    """Point one derivative at its new sheet and record the patch for compensation."""

    def _mutate(project: dict[str, Any]) -> None:
        derivative = _locate_derivative(project, owner_name, derivative_name)
        before = copy.deepcopy(derivative)
        derivative[_SPEC.sheet_field] = sheet_path
        mutation_box.append(OptimisticMappingPatch.capture(before, derivative))

    return _mutate


def _derivative_sheet_metadata_compensator(
    *,
    pm: ProjectManager,
    project_name: str,
    owner_name: str,
    derivative_name: str,
    mutation: OptimisticMappingPatch,
) -> _MetadataCompensator:
    """Roll the derivative entry back to the pre-write patch inside the rejecting transaction."""

    def _compensate_metadata(reject: Callable[[], None]) -> None:
        def _restore(project: dict[str, Any]) -> None:
            try:
                derivative = _locate_derivative(project, owner_name, derivative_name)
            except KeyError:
                return
            mutation.restore(derivative)

        def _reject(_project_file: Path) -> None:
            reject()

        pm.update_project(project_name, _restore, on_commit=_reject)

    return _compensate_metadata


def _write_back(
    *,
    pm: ProjectManager,
    project_name: str,
    target: DerivativeSheetTarget,
    activate: Callable[[Path], None],
) -> _MetadataCompensator:
    """Point the derivative at its new sheet inside the caller's commit."""

    mutation_box: list[OptimisticMappingPatch] = []
    pm.update_project(
        project_name,
        _derivative_sheet_metadata_mutator(
            owner_name=target.owner_key,
            derivative_name=target.derivative_key,
            sheet_path=target.sheet_path,
            mutation_box=mutation_box,
        ),
        on_commit=activate,
    )
    return _derivative_sheet_metadata_compensator(
        pm=pm,
        project_name=project_name,
        owner_name=target.owner_key,
        derivative_name=target.derivative_key,
        mutation=mutation_box[0],
    )


def point_derivative_at_sheet(
    *,
    project_name: str,
    target: DerivativeSheetTarget,
    on_commit: Callable[[Path], None],
    project_manager: ProjectManager | None = None,
) -> None:
    """把衍生条目的资产图指针指回它的规范路径（版本还原用），衍生不存在时抛 ``KeyError``。"""
    _write_back(
        pm=project_manager or get_project_manager(),
        project_name=project_name,
        target=target,
        activate=on_commit,
    )


def derivative_sheet_commit_callback(
    *,
    project_name: str,
    target: DerivativeSheetTarget,
    prompt: str,
    versions: Any,
    task_id: str | None,
    basis: ArtifactBasis | ArtifactBasisDescriptor | None,
    outcome_box: list[_FormalImageCommitOutcome],
    project_manager: ProjectManager | None = None,
) -> _StagedImageCommit:
    """衍生资产图的正式活化回调：写回衍生条目 + 版本活化 + 清单登记同一次提交。

    生成与图片编辑共用它，两条路线的写回口径因此不分叉。
    """
    pm = project_manager or get_project_manager()
    project_path = pm.get_project_path(project_name)

    def _commit_metadata(activate: Callable[[], None]) -> _MetadataCompensator | None:
        compensator = _write_back(
            pm=pm,
            project_name=project_name,
            target=target,
            activate=lambda _project_file: activate(),
        )
        # 无任务身份的直生没有终局取消窗口，不需要补偿器。
        return compensator if task_id is not None else None

    return _staged_formal_image_callback(
        versions=versions,
        project_path=project_path,
        resource_type=CHARACTER_DERIVATIVE_RESOURCE_TYPE,
        resource_id=target.artifact_id,
        script_file=None,
        artifact_path=target.sheet_path,
        prompt=prompt,
        task_id=task_id,
        basis=basis,
        outcome_box=outcome_box,
        commit_metadata=_commit_metadata,
    )


async def finalize_derivative_sheet_task(
    *,
    project_name: str,
    target: DerivativeSheetTarget,
    generator: Any,
    version: int,
    task_id: str | None,
    basis: ArtifactBasis | ArtifactBasisDescriptor | None,
    project_manager: ProjectManager | None = None,
) -> tuple[str, _CancellationReceipt | None]:
    """未走活化回调时的收尾：提交衍生条目指针并跨过终局取消窗口。"""
    pm = project_manager or get_project_manager()

    def _commit(register: Callable[[Path], None]) -> _MetadataCompensator:
        return _write_back(pm=pm, project_name=project_name, target=target, activate=register)

    def _commit_untracked(register: Callable[[Path], None]) -> None:
        _commit(register)

    return await _finalize_formal_image_task(
        project_path=pm.get_project_path(project_name),
        resource_type=CHARACTER_DERIVATIVE_RESOURCE_TYPE,
        resource_id=target.artifact_id,
        script_file=None,
        artifact_path=target.sheet_path,
        generator=generator,
        version=version,
        task_id=task_id,
        basis=basis,
        commit_current=_commit_untracked,
        commit_tracked=_commit,
        missing_receipt_error="task-aware derivative sheet registration did not return a receipt",
    )


def _prepare(project_name: str, owner_name: str, derivative_name: str):
    """Resolve the source sheet, freeze it, and build the canonical basis in one read."""

    pm = get_project_manager()
    project = pm.load_project(project_name)
    project_path = pm.get_project_path(project_name)
    source = resolve_derivative_sheet_source(project, owner_name, derivative_name)

    claims: list[ArtifactInputClaim] = []
    if not artifact_input_is_usable(
        resolver=active_artifact_currency_resolver(project_path, project),
        key=ArtifactKey.asset_sheet(DERIVATIVE_ASSET_TYPE, source.owner_key),
        artifact_path=source.owner_sheet_path,
        claims=claims,
    ):
        raise BadRequestError("derivative_owner_sheet_missing", name=source.owner_key)

    owner_sheet_file = project_path / source.owner_sheet_path
    frozen = freeze_image_references(
        [owner_sheet_file],
        [derivative_source_reference(source.owner_key, owner_sheet_file)],
    )
    try:
        basis = build_derivative_sheet_basis(
            owner_name=source.owner_key,
            derivative_name=source.derivative_key,
            description=source.description,
            aspect_ratio=get_aspect_ratio(project, CHARACTER_DERIVATIVE_RESOURCE_TYPE),
            source=frozen.visual_references[0],
        )
    except BaseException:
        frozen.cleanup()
        raise
    return project, project_path, source, frozen, basis, tuple(claims)


async def execute_character_derivative_task(
    project_name: str,
    resource_id: str,
    payload: dict[str, Any],
    *,
    user_id: str = DEFAULT_USER_ID,
    task_id: str | None = None,
) -> dict[str, Any]:
    """执行一次衍生资产图生成：本体图 → i2i → 新版本覆盖 current → 写回衍生条目。

    ``resource_id`` 是 ``本体名/衍生名``。本体没有资产图、或本体图当前不是可用正式产物时
    拒绝执行，不提交任何付费请求。
    """
    owner_name, derivative_name = split_derivative_artifact_id(resource_id)
    project, project_path, source, frozen, basis, formal_claims = await asyncio.to_thread(
        _prepare, project_name, owner_name, derivative_name
    )
    instruction = build_derivative_sheet_instruction(source.description)
    pm = get_project_manager()

    def _build_commit(generator: Any, outcome_box: list[_FormalImageCommitOutcome]) -> _StagedImageCommit:
        return derivative_sheet_commit_callback(
            project_name=project_name,
            target=source.target,
            prompt=instruction,
            versions=generator.versions,
            task_id=task_id,
            basis=basis,
            outcome_box=outcome_box,
            project_manager=pm,
        )

    async def _finalize(generator: Any, version: int) -> tuple[str, _CancellationReceipt | None]:
        return await finalize_derivative_sheet_task(
            project_name=project_name,
            target=source.target,
            generator=generator,
            version=version,
            task_id=task_id,
            basis=basis,
            project_manager=pm,
        )

    async def _before_submit() -> None:
        await asyncio.to_thread(assert_artifact_input_claims_usable, project_path, project, formal_claims)

    return await _run_formal_image_task(
        project_name=project_name,
        payload=payload,
        project=project,
        user_id=user_id,
        task_id=task_id,
        frozen_references=frozen,
        plan=_FormalImagePlan(
            resource_type=CHARACTER_DERIVATIVE_RESOURCE_TYPE,
            resource_id=source.target.artifact_id,
            artifact_path=source.target.sheet_path,
            prompt=instruction,
            aspect_ratio=get_aspect_ratio(project, CHARACTER_DERIVATIVE_RESOURCE_TYPE),
            build_commit_callback=_build_commit,
            finalize=_finalize,
            before_submit=_before_submit,
        ),
    )


__all__ = [
    "build_derivative_sheet_instruction",
    "derivative_sheet_commit_callback",
    "execute_character_derivative_task",
    "finalize_derivative_sheet_task",
    "point_derivative_at_sheet",
]
