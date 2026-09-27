"""角色衍生资产图的生成任务执行层（见 ``docs/adr/0072``）。

一次衍生资产图生成就是对**本体资产图**的一次图片编辑：本体图是唯一参考图，衍生的外观
变化描述加固定守卫是唯一 prompt（守卫要求保持三视图版式与其余外观不变）。因此它必然
i2i，与 ``image_edit`` 同属入队即知任务类型的例外。

产物坐标与本体资产图同一族：清单键 ``asset_sheet("character", "本体/衍生")``、版本资源类型
``character_derivatives``、落盘 ``characters/derivatives/{本体}/{衍生}.png``。正式产物的
提交、版本活化与清单登记全部复用资产图任务的既有缝，本模块只提供「衍生自己的元数据写回」
与「以本体资产图为输入」这两处差异。

指令与依据都在执行时按当前项目状态重算，不吃入队时的快照：两者必须同源，否则衍生图的
登记依据会与它实际收到的指令不符，规范状态比对随即恒判过期。
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from lib.artifacts.artifact_activation import (
    ArtifactInputClaim,
    active_artifact_currency_resolver,
    assert_current_artifact_input_claims_usable,
    bind_artifact_input_claims_to_content_digests,
)
from lib.artifacts.artifact_manifest import ArtifactBasis, ArtifactBasisDescriptor
from lib.artifacts.generation_input import (
    DerivativeSheetInput,
    FrozenGenerationInput,
    InputRefused,
    derivative_sheet_input,
    project_input_observation,
)
from lib.db.base import DEFAULT_USER_ID
from lib.project.asset_derivatives import (
    DERIVATIVE_ASSET_TYPE,
    DerivativeSheetTarget,
    resolve_derivative_target,
    split_derivative_artifact_id,
)
from lib.project.asset_types import ASSET_SPECS, DERIVATIVES_FIELD, resolve_asset_key
from lib.project.project_manager import ProjectManager, get_project_manager
from lib.project.resource_paths import CHARACTER_DERIVATIVE_RESOURCE_TYPE
from lib.prompts.prompt_builders import build_character_derivative_prompt
from server.services.admission.reference_admission import input_refusal_error
from server.services.tasks.formal_image_commit import (
    FormalImageCommitOutcome,
    FormalImagePlan,
    StagedImageCommit,
    run_formal_image_task,
    staged_formal_image_callback,
)
from server.services.tasks.generation_context import ImageLaneRequest, resolve_generation_context

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
) -> Callable[[dict[str, Any]], None]:
    """Point one derivative at its new sheet."""

    def _mutate(project: dict[str, Any]) -> None:
        derivative = _locate_derivative(project, owner_name, derivative_name)
        derivative[_SPEC.sheet_field] = sheet_path

    return _mutate


def _write_back(
    *,
    pm: ProjectManager,
    project_name: str,
    target: DerivativeSheetTarget,
    activate: Callable[[Path], None],
) -> None:
    """Point the derivative at its new sheet inside the caller's commit."""

    pm.update_project(
        project_name,
        _derivative_sheet_metadata_mutator(
            owner_name=target.owner_key,
            derivative_name=target.derivative_key,
            sheet_path=target.sheet_path,
        ),
        on_commit=activate,
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
    outcome_box: list[FormalImageCommitOutcome],
    project_manager: ProjectManager | None = None,
) -> StagedImageCommit:
    """衍生资产图的正式活化回调：写回衍生条目 + 版本活化 + 清单登记同一次提交。

    生成与图片编辑共用它，两条路线的写回口径因此不分叉。
    """
    pm = project_manager or get_project_manager()
    project_path = pm.get_project_path(project_name)

    def _commit_metadata(activate: Callable[[], None]) -> None:
        _write_back(
            pm=pm,
            project_name=project_name,
            target=target,
            activate=lambda _project_file: activate(),
        )

    return staged_formal_image_callback(
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


@dataclass(frozen=True, slots=True)
class _DerivativeSheetInputs:
    """衍生资产图任务在解析 image lane 之前就能备齐的输入：项目快照、落盘坐标与成立的生成输入。"""

    project: dict[str, Any]
    project_path: Path
    target: DerivativeSheetTarget
    generation_input: DerivativeSheetInput


def _load(project_name: str, owner_name: str, derivative_name: str) -> _DerivativeSheetInputs:
    pm = get_project_manager()
    project = pm.load_project(project_name)
    project_path = pm.get_project_path(project_name)
    target = resolve_derivative_target(project, owner_name, derivative_name)
    generation_input = derivative_sheet_input(
        project,
        owner=target.owner_key,
        derivative=target.derivative_key,
        observation=project_input_observation(project_path),
    )
    # 缺变化描述或本体资产图不可用时在解析供应商通道与付费之前失败，一次报出全部缺口。
    if isinstance(generation_input, InputRefused):
        raise input_refusal_error(generation_input)
    return _DerivativeSheetInputs(
        project=project,
        project_path=project_path,
        target=target,
        generation_input=generation_input,
    )


async def execute_character_derivative_task(
    project_name: str,
    resource_id: str,
    payload: dict[str, Any],
    *,
    user_id: str = DEFAULT_USER_ID,
    task_id: str | None = None,
) -> dict[str, Any]:
    """执行一次衍生资产图生成：本体图 → i2i → 新版本覆盖 current → 写回衍生条目。

    ``resource_id`` 是 ``本体名/衍生名``。缺变化描述、本体没有资产图或本体图不可用时拒绝执行，
    不提交任何付费请求。
    """
    owner_name, derivative_name = split_derivative_artifact_id(resource_id)
    inputs = await asyncio.to_thread(_load, project_name, owner_name, derivative_name)
    project, project_path, target = inputs.project, inputs.project_path, inputs.target
    context = await resolve_generation_context(
        project_name,
        payload,
        project=project,
        project_path=project_path,
        user_id=user_id,
        image=ImageLaneRequest(generation_type="i2i"),
    )
    currency_resolver = active_artifact_currency_resolver(project_path, project)

    def _bind_claims(
        claims: Sequence[ArtifactInputClaim], content_digests: Mapping[str, str]
    ) -> tuple[ArtifactInputClaim, ...]:
        return bind_artifact_input_claims_to_content_digests(
            resolver=currency_resolver,
            claims=claims,
            content_digests=content_digests,
        )

    def _freeze() -> FrozenGenerationInput:
        return inputs.generation_input.freeze(
            max_reference_images=context.image.max_reference_images,
            model=context.image.backend_model,
            bind_claims=_bind_claims,
        )

    pm = get_project_manager()
    with await asyncio.to_thread(_freeze) as frozen:

        def _build_commit(generator: Any, outcome_box: list[FormalImageCommitOutcome]) -> StagedImageCommit:
            return derivative_sheet_commit_callback(
                project_name=project_name,
                target=target,
                prompt=frozen.prompt,
                versions=generator.versions,
                task_id=task_id,
                basis=frozen.basis,
                outcome_box=outcome_box,
                project_manager=pm,
            )

        async def _assert_claims_usable() -> None:
            await asyncio.to_thread(assert_current_artifact_input_claims_usable, project_path, frozen.claims)

        async def _pre_submit(_generator: Any) -> None:
            await _assert_claims_usable()

        return await run_formal_image_task(
            project_name=project_name,
            payload=payload,
            project=project,
            user_id=user_id,
            task_id=task_id,
            frozen_references=frozen.references,
            context=context,
            plan=FormalImagePlan(
                resource_type=CHARACTER_DERIVATIVE_RESOURCE_TYPE,
                resource_id=target.artifact_id,
                artifact_path=target.sheet_path,
                prompt=frozen.prompt,
                aspect_ratio=inputs.generation_input.canvas_ratio,
                build_commit_callback=_build_commit,
                pre_submit=_pre_submit,
                before_submit=_assert_claims_usable,
                warnings=frozen.warnings,
            ),
        )


__all__ = [
    "build_derivative_sheet_instruction",
    "derivative_sheet_commit_callback",
    "execute_character_derivative_task",
    "point_derivative_at_sheet",
]
