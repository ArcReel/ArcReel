"""Host-neutral tool for instruction-based image editing (see ``docs/adr/0050``).

Editing forks the **image**, not the prompt: the current image is the sole reference,
the user's instruction is the sole prompt, ``image_prompt`` is never rewritten. This is
the tool-facing entry point for that flow — the fail-fast i2i check and resource
resolution reuse the same helpers the HTTP endpoint (``server/routers/generate.py``)
uses, so the two entry points can't diverge (see ``server/services/tasks/image_edit_tasks.py``).
"""

from __future__ import annotations

from typing import Any, Literal, Self, get_args

from pydantic import BaseModel, ConfigDict, Field, model_validator
from pydantic.json_schema import SkipJsonSchema

from lib.artifacts.artifact_activation import (
    ArtifactCurrencyResolver,
    active_artifact_currency_resolver,
    resolve_artifact_episode,
)
from lib.artifacts.artifact_manifest import ArtifactManifestError
from lib.config.resolver import ConfigResolver
from lib.generation.generation_queue_client import TaskSpec
from lib.generation.generation_result import (
    GenerationAction,
    GenerationCandidate,
    GenerationProblem,
    GenerationProblemCode,
    GenerationResultBuilder,
    GenerationSelectionMode,
    GenerationTargetState,
    record_batch_outcomes,
)
from server.media_tools.context import (
    GenerationToolValue,
    ScriptFilename,
    generation_batch_submission_outcome,
    generation_result_outcome,
    tool_error,
    tool_problem,
)
from server.services.tasks.image_edit_tasks import EDITABLE_RESOURCE_TYPES, resolve_usable_image_edit_source
from server.tool_runtime import CallerContext, ProjectScope, Services, ToolOutcome, ToolRequest, submit_media_generation

# 编辑始终是显式选择：一次编辑必须携带自己的指令，没有可由 Manifest 推导的
# "缺失的编辑"，因此本工具不提供 missing-only 选择。
_OPERATION = "edit_images"

# Display label for tool output only; storyboard isn't an ASSET_SPECS member so this
# can't reuse that dict directly (mirrors assets._EMOJI's separate table).
_LABEL_ZH: dict[str, str] = {
    "character": "角色",
    "scene": "场景",
    "prop": "道具",
    "product": "商品",
    "storyboard": "分镜图",
    "character_derivative": "角色衍生图",
}

EditableResourceType = Literal["character", "scene", "prop", "product", "storyboard", "character_derivative"]
if get_args(EditableResourceType) != EDITABLE_RESOURCE_TYPES:
    raise RuntimeError("EditableResourceType 须与 EDITABLE_RESOURCE_TYPES 逐项一致")


async def _i2i_provider_available(project: dict[str, Any], *, config_resolver: ConfigResolver) -> bool:
    """项目 i2i 槽解析不出可用供应商时返回 False——与 HTTP 端点入队前 fail-fast 同一判断点
    （见 ``server/routers/generate.py::_require_i2i_image_provider_configured``），批量编辑
    只需要一次「是否可用」的项目级判断，不像端点那样需要拿到 provider_id 传给入队。
    """
    try:
        await config_resolver.resolve_image_backend(project, None, generation_type="i2i")
    except ValueError:
        return False
    return True


def _build_specs(
    *,
    project: dict[str, Any],
    project_path: Any,
    resource_type: str,
    edits: list[ImageEdit],
    script: dict[str, Any] | None,
    script_filename: str | None,
    artifact_episode: int | None,
    resolver: ArtifactCurrencyResolver,
    warnings: list[str],
    builder: GenerationResultBuilder,
    states: dict[str, GenerationTargetState],
    caller_source: str,
) -> list[TaskSpec]:
    """Turn the requested edits into task specs, blocking the ones that cannot run.

    Entries with a blank ID and repeated IDs stay in ``warnings``: they have no
    unit ID of their own to report against, so they cannot enter the per-ID
    contract. ``states`` is filled in with each resolved edit source's pre-edit
    path so that, if the edit task itself later fails, the per-ID result still
    reports the untouched source image instead of ``None`` — the edit never
    landed, but the image it would have overwritten is still there.
    """

    label = _LABEL_ZH[resource_type]
    specs: list[TaskSpec] = []
    seen_ids: set[str] = set()
    for edit in edits:
        resource_id = edit.id.strip()
        instruction = edit.instruction.strip()
        if not resource_id:
            warnings.append("⚠️  edits 中存在缺少 id 的条目，跳过")
            continue
        if resource_id in seen_ids:
            warnings.append(f"⚠️  {label} '{resource_id}' 在 edits 中重复出现，仅保留第一条编辑指令")
            continue
        seen_ids.add(resource_id)
        if not instruction:
            builder.block(
                resource_id,
                problem=GenerationProblem(
                    code=GenerationProblemCode.UNIT_REQUEST_INVALID,
                    detail=f"{label} '{resource_id}' 缺少编辑指令",
                    action=GenerationAction.FIX_INPUT,
                ),
            )
            continue
        try:
            edit_source = resolve_usable_image_edit_source(
                project=project,
                project_path=project_path,
                resource_type=resource_type,
                resource_id=resource_id,
                script=script,
                artifact_episode=artifact_episode,
                resolver=resolver,
            )
        except KeyError:
            builder.block(
                resource_id,
                problem=GenerationProblem(
                    code=GenerationProblemCode.UNIT_NOT_FOUND,
                    detail=f"{label} '{resource_id}' 不存在",
                    action=GenerationAction.FIX_INPUT,
                ),
            )
            continue
        except ArtifactManifestError as exc:
            # Manifest 判定本条编辑的产物状态时 fail-loud：这是单条编辑自己的问题，
            # 不该把整批已经算出的其它 ID 结果一起吞进 handler 级文本错误。
            builder.block(
                resource_id,
                problem=GenerationProblem(
                    code=GenerationProblemCode.ARTIFACT_STATE_UNAVAILABLE,
                    detail=str(exc),
                    action=GenerationAction.REPAIR_ARTIFACT_STATE,
                ),
            )
            continue
        if edit_source is None:
            builder.block(
                resource_id,
                problem=GenerationProblem(
                    code=GenerationProblemCode.UNIT_INPUT_UNUSABLE,
                    detail=f"{label} '{resource_id}' 没有可编辑的当前图",
                    action=GenerationAction.GENERATE_DEPENDENCY,
                ),
            )
            continue
        states[resource_id] = GenerationTargetState(
            candidate=GenerationCandidate(unit_id=resource_id, artifact_path=edit_source.artifact_path)
        )
        specs.append(
            TaskSpec.from_request(
                task_type="image_edit",
                media_type="image",
                resource_id=resource_id,
                prompt=instruction,
                script_file=script_filename if resource_type == "storyboard" else None,
                extra_payload={"resource_type": resource_type},
                unit_id=resource_id,
                source=caller_source,
            )
        )
    return specs


class ImageEdit(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str = Field(
        description="编辑目标 ID：资产名称；storyboard 为分镜的 segment_id / scene_id；"
        "character_derivative 为「本体名/衍生名」"
    )
    instruction: str = Field(description="编辑指令：用自然语言描述要修改的部分")


class EditImagesRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    resource_type: EditableResourceType = Field(description="编辑目标类型；一次调用只编辑同一类型")
    edits: list[ImageEdit] = Field(
        min_length=1, description="批量编辑列表，每项一个 id 与一条 instruction；同一 id 只保留第一条"
    )
    script_file: ScriptFilename | SkipJsonSchema[None] = Field(
        default=None, description="剧本纯文件名（不含目录），如 episode_1.json；resource_type=storyboard 时必填"
    )

    @model_validator(mode="after")
    def _storyboard_needs_script(self) -> Self:
        if self.resource_type == "storyboard" and self.script_file is None:
            raise ValueError("resource_type=storyboard 时 script_file 必填")
        return self


async def edit_images(
    request: ToolRequest[EditImagesRequest],
    scope: ProjectScope,
    caller: CallerContext,
    services: Services,
) -> ToolOutcome[GenerationToolValue]:
    try:
        resource_type = request.value.resource_type
        edits = request.value.edits

        script_filename = request.value.script_file if resource_type == "storyboard" else None
        script: dict[str, Any] | None = None
        if script_filename is not None:
            script = services.projects.load_script(scope.project_name, script_filename)

        project = services.projects.load_project(scope.project_name)
        project_path = services.projects.get_project_path(scope.project_name)
        artifact_episode = None
        if script is not None and script_filename is not None:
            artifact_episode = resolve_artifact_episode(
                project=project,
                script=script,
                script_filename=script_filename,
            )
        resolver = active_artifact_currency_resolver(project_path, project)

        warnings: list[str] = []
        builder = GenerationResultBuilder(_OPERATION, GenerationSelectionMode.EXPLICIT)
        states: dict[str, GenerationTargetState] = {}

        if not await _i2i_provider_available(project, config_resolver=services.capabilities):
            # 拦截在入队前：不是某个 ID 的产物问题，是整批共享的前置条件不满足，
            # 但调用方仍按逐 ID 契约读结果，因此每个请求到的 ID 各记一条 blocked，
            # 而不是只回一段无法编程消费的文本。
            seen: set[str] = set()
            for edit in edits:
                resource_id = edit.id.strip()
                if not resource_id or resource_id in seen:
                    continue
                seen.add(resource_id)
                builder.block(
                    resource_id,
                    # 复用 lib.generation.task_failure 已登记的 i2i 缺能力码（同一失败在
                    # HTTP 端点的执行期路径下就是这个码），不新造未登记码。
                    problem=GenerationProblem(
                        code="image_capability_missing_i2i",
                        detail="当前项目图片供应商不支持图生图（i2i），无法执行编辑；请提示用户前往设置更换支持 i2i 的供应商",
                        action=GenerationAction.CONFIGURE_PROVIDER,
                    ),
                )
            specs = []
        else:
            specs = _build_specs(
                project=project,
                project_path=project_path,
                resource_type=resource_type,
                edits=edits,
                script=script,
                script_filename=script_filename,
                artifact_episode=artifact_episode,
                resolver=resolver,
                warnings=warnings,
                builder=builder,
                states=states,
                caller_source=caller.source,
            )
        if not specs and not builder.recorded_ids:
            return tool_problem("\n".join([*warnings, "没有可执行的编辑任务"]))

        submitted = await submit_media_generation(
            scope=scope,
            caller=caller,
            services=services,
            operation=_OPERATION,
            preflight=builder.build(),
            pending_ids=list(states),
            specs=specs,
            states=states,
        )
        if submitted.successes is None or submitted.failures is None:
            return generation_batch_submission_outcome(submitted.batch)
        if specs:
            # 编辑产物不写回 Manifest（编辑意图不可推导，见模块顶部说明），
            # 因此这里不带 resolver：产物时效轴如实留空而不是假装已知。states 只
            # 用来在失败时把未被触碰的编辑源图路径带回结果，不参与时效判断。
            record_batch_outcomes(
                builder,
                successes=submitted.successes,
                failures=submitted.failures,
                states=states,
                fallback_path=lambda rid: rid,
            )

        return generation_result_outcome(builder.build(), warnings, batch_id=submitted.batch.batch_id)
    except Exception as exc:
        return tool_error(_OPERATION, exc)


__all__ = ["EditImagesRequest", "ImageEdit", "edit_images"]
