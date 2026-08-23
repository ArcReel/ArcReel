"""SDK MCP tool for instruction-based image editing (image edit, see ``docs/adr/0050``).

Editing forks the **image**, not the prompt: the current image is the sole reference,
the user's instruction is the sole prompt, ``image_prompt`` is never rewritten. This is
the tool-facing entry point for that flow — the fail-fast i2i check and resource
resolution reuse the same helpers the HTTP endpoint (``server/routers/generate.py``)
uses, so the two entry points can't diverge (see ``server/services/image_edit_tasks.py``).
"""

from __future__ import annotations

from typing import Any

from claude_agent_sdk import tool

from lib.artifact_activation import (
    ArtifactCurrencyResolver,
    active_artifact_currency_resolver,
    resolve_artifact_episode,
)
from lib.artifact_manifest import ArtifactManifestError
from lib.config.resolver import ConfigResolver
from lib.db import async_session_factory
from lib.generation_queue_client import TaskSpec, batch_enqueue_and_wait
from lib.generation_result import (
    GenerationAction,
    GenerationCandidate,
    GenerationProblem,
    GenerationProblemCode,
    GenerationResultBuilder,
    GenerationSelectionMode,
    GenerationTargetState,
    record_batch_outcomes,
)
from server.agent_runtime.sdk_tools._context import (
    ToolContext,
    generation_result_response,
    tool_error,
    validate_script_filename,
)
from server.services.image_edit_tasks import EDITABLE_RESOURCE_TYPES, resolve_usable_image_edit_source

# 编辑始终是显式选择：一次编辑必须携带自己的指令，没有可由 Manifest 推导的
# "缺失的编辑"，因此本工具不提供 missing-only 选择。
_OPERATION = "edit_images"

# Display label for tool output only; storyboard isn't an ASSET_SPECS member so this
# can't reuse that dict directly (mirrors enqueue_assets._EMOJI's separate table).
_LABEL_ZH: dict[str, str] = {
    "character": "角色",
    "scene": "场景",
    "prop": "道具",
    "product": "商品",
    "storyboard": "分镜图",
}


async def _i2i_provider_available(
    project: dict[str, Any],
    *,
    config_resolver: ConfigResolver | None = None,
) -> bool:
    """项目 i2i 槽解析不出可用供应商时返回 False——与 HTTP 端点入队前 fail-fast 同一判断点
    （见 ``server/routers/generate.py::_require_i2i_image_provider_configured``），批量编辑
    只需要一次「是否可用」的项目级判断，不像端点那样需要拿到 provider_id 传给入队。
    """
    try:
        resolver = config_resolver or ConfigResolver(async_session_factory)
        await resolver.resolve_image_backend(project, None, capability="i2i")
    except ValueError:
        return False
    return True


def _build_specs(
    *,
    project: dict[str, Any],
    project_path: Any,
    resource_type: str,
    edits: list[Any],
    script: dict[str, Any] | None,
    script_filename: str | None,
    artifact_episode: int | None,
    resolver: ArtifactCurrencyResolver,
    warnings: list[str],
    builder: GenerationResultBuilder,
    states: dict[str, GenerationTargetState],
) -> list[TaskSpec]:
    """Turn the requested edits into task specs, blocking the ones that cannot run.

    Malformed entries with no usable ID stay in ``warnings``: they have no unit
    ID to report against, so they cannot enter the per-ID contract. ``states``
    is filled in with each resolved edit source's pre-edit path so that, if the
    edit task itself later fails, the per-ID result still reports the untouched
    source image instead of ``None`` — the edit never landed, but the image it
    would have overwritten is still there.
    """

    label = _LABEL_ZH[resource_type]
    specs: list[TaskSpec] = []
    seen_ids: set[str] = set()
    for edit in edits:
        if not isinstance(edit, dict):
            warnings.append(f"⚠️  edits 中存在非法条目（须为对象），跳过: {edit!r}")
            continue
        resource_id = str(edit.get("id") or "").strip()
        instruction = str(edit.get("instruction") or "").strip()
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
            source = resolve_usable_image_edit_source(
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
        if source is None:
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
            candidate=GenerationCandidate(unit_id=resource_id, artifact_path=source.artifact_path)
        )
        specs.append(
            TaskSpec.from_request(
                task_type="image_edit",
                media_type="image",
                resource_id=resource_id,
                prompt=instruction,
                script_file=script_filename if resource_type == "storyboard" else None,
                extra_payload={"resource_type": resource_type},
            )
        )
    return specs


def edit_images_tool(ctx: ToolContext):
    @tool(
        "edit_images",
        "对已生成的资产图/分镜图做指令式局部编辑：保持原图大体不变，仅按指令修改不满意的部分"
        "（如换发色、去掉背景杂物、调整光线氛围），支持同类型批量下发。"
        "与「重新生成」的区别：编辑=保底图微调、不改变原 image_prompt，重生成会作废本次编辑效果"
        "（仍按原 prompt 重画）；重新生成=按原 prompt 整图重画，会推翻已满意的部分。"
        "用户只想改局部时用编辑；用户想推翻构图/内容重来、或原 image_prompt 本身要改时用重新生成。"
        "resource_type 支持 character/scene/prop/product/storyboard 五类，storyboard 必须带 script_file。"
        "编辑必然走图生图（i2i）；当前项目图片供应商不支持 i2i 时直接返回错误，不创建任何任务。"
        "编辑始终是显式选择（每条编辑自带指令），结果按 requested / succeeded / failed / blocked 逐 ID 返回。",
        {
            "type": "object",
            "properties": {
                "resource_type": {
                    "type": "string",
                    "enum": list(EDITABLE_RESOURCE_TYPES),
                    "description": "编辑目标类型",
                },
                "edits": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "id": {
                                "type": "string",
                                "description": "资产名称，或 storyboard 的 segment_id/scene_id",
                            },
                            "instruction": {"type": "string", "description": "编辑指令（自然语言，描述要修改的部分）"},
                        },
                        "required": ["id", "instruction"],
                    },
                    "minItems": 1,
                    "description": "批量编辑列表，每项一个 id + instruction",
                },
                "script_file": {
                    "type": "string",
                    "description": "剧本文件名（如 episode_1.json）；resource_type=storyboard 时必填，"
                    "必须是纯文件名，禁止任何路径分隔符",
                },
            },
            "required": ["resource_type", "edits"],
        },
    )
    async def _handler(args: dict[str, Any]) -> dict[str, Any]:
        try:
            resource_type = args.get("resource_type")
            if resource_type not in EDITABLE_RESOURCE_TYPES:
                return {
                    "content": [
                        {
                            "type": "text",
                            "text": f"resource_type 必须是以下之一: {', '.join(EDITABLE_RESOURCE_TYPES)}",
                        }
                    ],
                    "is_error": True,
                }

            edits = args.get("edits")
            if not isinstance(edits, list) or not edits:
                return {"content": [{"type": "text", "text": "edits 不能为空"}], "is_error": True}

            is_storyboard = resource_type == "storyboard"
            script_filename: str | None = None
            script: dict[str, Any] | None = None
            if is_storyboard:
                raw_script = args.get("script_file")
                if not raw_script:
                    return {
                        "content": [{"type": "text", "text": "resource_type=storyboard 时 script_file 必填"}],
                        "is_error": True,
                    }
                script_filename = validate_script_filename(raw_script)
                script = ctx.pm.load_script(ctx.project_name, script_filename)

            project = ctx.pm.load_project(ctx.project_name)
            project_path = ctx.project_path
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

            if ctx.config_resolver is None:
                provider_available = await _i2i_provider_available(project)
            else:
                provider_available = await _i2i_provider_available(project, config_resolver=ctx.config_resolver)
            if not provider_available:
                # 拦截在入队前：不是某个 ID 的产物问题，是整批共享的前置条件不满足，
                # 但调用方仍按逐 ID 契约读结果，因此每个请求到的 ID 各记一条 blocked，
                # 而不是只回一段无法编程消费的文本。
                seen: set[str] = set()
                for edit in edits:
                    if not isinstance(edit, dict):
                        continue
                    resource_id = str(edit.get("id") or "").strip()
                    if not resource_id or resource_id in seen:
                        continue
                    seen.add(resource_id)
                    builder.block(
                        resource_id,
                        # 复用 lib.task_failure 已登记的 i2i 缺能力码（同一失败在
                        # HTTP 端点的执行期路径下就是这个码），不新造未登记码。
                        problem=GenerationProblem(
                            code="image_capability_missing_i2i",
                            detail="当前项目图片供应商不支持图生图（i2i），无法执行编辑；请提示用户前往设置更换支持 i2i 的供应商",
                            action=GenerationAction.CONFIGURE_PROVIDER,
                        ),
                    )
                return generation_result_response(builder.build(), warnings)
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
            )
            if not specs and not builder.recorded_ids:
                return {
                    "content": [{"type": "text", "text": "\n".join([*warnings, "没有可执行的编辑任务"])}],
                    "is_error": True,
                }

            if specs:
                successes, failures = await batch_enqueue_and_wait(
                    project_name=ctx.project_name,
                    specs=specs,
                )
                # 编辑产物不写回 Manifest（编辑意图不可推导，见模块顶部说明），
                # 因此这里不带 resolver：产物时效轴如实留空而不是假装已知。states 只
                # 用来在失败时把未被触碰的编辑源图路径带回结果，不参与时效判断。
                record_batch_outcomes(
                    builder,
                    successes=successes,
                    failures=failures,
                    states=states,
                    fallback_path=lambda rid: rid,
                )

            return generation_result_response(builder.build(), warnings)
        except Exception as exc:  # noqa: BLE001
            return tool_error(_OPERATION, exc)

    return _handler


__all__ = ["edit_images_tool"]
