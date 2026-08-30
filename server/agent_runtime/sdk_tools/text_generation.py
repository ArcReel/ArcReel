"""SDK MCP adapters for text generation and video capability queries."""

from __future__ import annotations

import json
import logging
from typing import Any

from claude_agent_sdk import tool

from lib.draft_quarantine import OPEN_DRAFT_TOOL_NAME, PROMOTE_TOOL_NAME
from lib.script_plan_entries import SCOPE_STALE
from server.draft_workflow import DiscardDraftRequest, DraftLocator, PatchDraftRequest, PromoteDraftRequest
from server.media_tools.context import (
    MAX_INSTRUCTIONS_LEN,
    ToolContext,
    tool_outcome_response,
    tool_services,
)
from server.text_generation import TextGenerationRequest as ToolTextGenerationRequest
from server.tool_runtime import (
    ToolOutcome,
    ToolProblem,
    ToolRequest,
    discard_draft,
    get_video_capabilities,
    open_draft,
    patch_draft,
    promote_draft,
)
from server.tool_runtime import (
    confirm_script_review as run_confirm_script_review,
)
from server.tool_runtime import (
    generate_episode_script as run_generate_episode_script,
)
from server.tool_runtime import (
    generate_script_plan as run_generate_script_plan,
)

# 四个分集数据生成工具共用的 instructions 参数 schema：用户意见原样注入 prompt 末尾的
# 「用户意见」分节，遵循强度由正文表达（需要强约束时在正文写明）。
_INSTRUCTIONS_SCHEMA: dict[str, Any] = {
    "type": "string",
    "description": (
        "用户对本次生成的意见原文（可选）；原样注入 prompt 末尾的「用户意见」分节，"
        f"遵循强度由正文表达，缺省/空白视同未传，最长 {MAX_INSTRUCTIONS_LEN} 字符"
    ),
}

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# get_video_capabilities
# ---------------------------------------------------------------------------

# 本模块的能力查询函数（``_fetch_caps_with_fallback`` / ``_fetch_reference_caps_with_fallback``
# 及 ``server.media_tools.context`` 的 ``resolve_video_caps`` /
# ``fetch_video_caps``）未注入解析器时一律省略 ``config_resolver`` 关键字，不传 ``None``：
# 这些符号会被整体替换为不接受该关键字的替身，调用形状须与不带该关键字的签名兼容。


def get_video_capabilities_tool(ctx: ToolContext):
    @tool(
        "get_video_capabilities",
        "查视频模型能力（model 粒度）+ 用户项目偏好。返回 JSON；"
        "参考生视频项目另含 reference_unit_durations（按 unit 有无 @ 引用分开的两套生效档位）。"
        "能力按项目生成模式定轴，全项目同一口径，无需指定剧集。",
        {"type": "object", "properties": {}},
    )
    async def _handler(_args: dict[str, Any]) -> dict[str, Any]:
        outcome = await get_video_capabilities(ToolRequest(None), ctx.scope, ctx.caller, tool_services(ctx))
        return tool_outcome_response("video_capabilities", outcome)

    return _handler


# ---------------------------------------------------------------------------
# draft workflow adapters
# ---------------------------------------------------------------------------


def _draft_response(outcome: ToolOutcome[dict[str, Any]]) -> dict[str, Any]:
    if outcome.problem is not None:
        payload = {"problem": {"code": outcome.problem.code, "detail": outcome.problem.detail}}
        return {"content": [{"type": "text", "text": json.dumps(payload, ensure_ascii=False)}], "is_error": True}
    return {"content": [{"type": "text", "text": json.dumps({"draft": outcome.value}, ensure_ascii=False)}]}


_DRAFT_LOCATOR_SCHEMA = DraftLocator.model_json_schema()
_DRAFT_LOCATOR_SCHEMA["properties"].pop("source")
_DRAFT_LOCATOR_SCHEMA["properties"]["episode"]["description"] = "剧集编号"

_OPEN_DRAFT_SCHEMA = DraftLocator.model_json_schema()
_OPEN_DRAFT_SCHEMA["properties"]["episode"]["description"] = "剧集编号"
_OPEN_DRAFT_SCHEMA["properties"]["source"]["description"] = (
    "可选小说源文件路径；仅在首次从正式 script_plan 创建草稿时用作重判来源"
)


def open_draft_tool(ctx: ToolContext):
    @tool(
        OPEN_DRAFT_TOOL_NAME,
        "读取指定草稿；草稿不存在时从对应正式文档创建编辑副本。返回完整正文、违约与 canonical revision。",
        _OPEN_DRAFT_SCHEMA,
    )
    async def _handler(args: dict[str, Any]) -> dict[str, Any]:
        try:
            locator = DraftLocator.model_validate(args)
            outcome = await open_draft(ToolRequest(locator), ctx.scope, ctx.caller, tool_services(ctx))
        except Exception as exc:
            outcome = ToolOutcome(problem=ToolProblem("invalid_request", str(exc)))
        return _draft_response(outcome)

    return _handler


def patch_draft_tool(ctx: ToolContext):
    schema = {
        **_DRAFT_LOCATOR_SCHEMA,
        "properties": {
            **_DRAFT_LOCATOR_SCHEMA["properties"],
            "content": {"type": "object", "description": "替换后的完整草稿正文；允许中间态不通过业务校验"},
            "base_revision": {"type": "string", "description": "open_draft / 上次 patch_draft 返回的 revision"},
            "accept_formal_revision": {
                "type": ["string", "null"],
                "description": "合并正式文档并发修改后，显式接受 open_draft 返回的 formal_revision",
            },
            "source": {
                "type": ["string", "null"],
                "description": "可选源文范围；仅在修正 script_plan 草稿的重判范围时提供",
            },
        },
        "required": ["episode", "doc_type", "content", "base_revision"],
    }

    @tool(
        "patch_draft",
        "按 canonical revision 原子替换草稿正文；revision 冲突时拒绝且不写入。",
        schema,
    )
    async def _handler(args: dict[str, Any]) -> dict[str, Any]:
        try:
            request = PatchDraftRequest.model_validate(
                {
                    **args,
                    "accepts_formal_revision": "accept_formal_revision" in args,
                    "updates_source": "source" in args,
                }
            )
            outcome = await patch_draft(ToolRequest(request), ctx.scope, ctx.caller, tool_services(ctx))
        except Exception as exc:
            outcome = ToolOutcome(problem=ToolProblem("invalid_request", str(exc)))
        return _draft_response(outcome)

    return _handler


def discard_draft_tool(ctx: ToolContext):
    schema = {
        **_DRAFT_LOCATOR_SCHEMA,
        "properties": {
            **_DRAFT_LOCATOR_SCHEMA["properties"],
            "base_revision": {"type": "string", "description": "open_draft / 上次 patch_draft 返回的 revision"},
        },
        "required": ["episode", "doc_type", "base_revision"],
    }

    @tool("discard_draft", "按 canonical revision 丢弃指定草稿；正式文档保持不变。", schema)
    async def _handler(args: dict[str, Any]) -> dict[str, Any]:
        try:
            request = DiscardDraftRequest.model_validate(args)
            outcome = await discard_draft(ToolRequest(request), ctx.scope, ctx.caller, tool_services(ctx))
        except Exception as exc:
            outcome = ToolOutcome(problem=ToolProblem("invalid_request", str(exc)))
        return _draft_response(outcome)

    return _handler


# ---------------------------------------------------------------------------
# promote_draft
# ---------------------------------------------------------------------------


def promote_draft_tool(ctx: ToolContext):
    schema = {
        **_DRAFT_LOCATOR_SCHEMA,
        "properties": {
            **_DRAFT_LOCATOR_SCHEMA["properties"],
            "base_revision": {"type": "string", "description": "open_draft 返回的当前草稿 revision"},
        },
        "required": [*_DRAFT_LOCATOR_SCHEMA["required"], "base_revision"],
    }

    @tool(
        PROMOTE_TOOL_NAME,
        "重新全量校验指定草稿；通过则晋升为正式文件并清除草稿，不通过则刷新违约报告。可反复调用。",
        schema,
    )
    async def _handler(args: dict[str, Any]) -> dict[str, Any]:
        try:
            request = PromoteDraftRequest.model_validate(args)
            outcome = await promote_draft(ToolRequest(request), ctx.scope, ctx.caller, tool_services(ctx))
        except Exception as exc:
            outcome = ToolOutcome(problem=ToolProblem("invalid_request", str(exc)))
        return _draft_response(outcome)

    return _handler


def generate_episode_script_tool(ctx: ToolContext):
    @tool(
        "generate_episode_script",
        "调用项目配置的文本模型生成 JSON 剧本。已有剧本时默认只重写脚本规划内容已变化的条目，"
        "其余条目的提示词、备注、尾帧与已生成产物原样保留。dry_run=true 时仅返回 prompt。",
        {
            "type": "object",
            "properties": {
                "episode": {"type": "integer", "minimum": 1, "description": "剧集编号"},
                "instructions": _INSTRUCTIONS_SCHEMA,
                "scope": {
                    "type": "string",
                    "enum": ["stale", "all"],
                    "description": "重写范围：stale（默认）只重写内容失配与新增的条目；all 整集重写",
                },
                "entry_ids": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "只重写这些条目（分镜 / 单元 id）；给出时即为本次范围，与 scope=all 互斥",
                },
                "dry_run": {"type": "boolean", "description": "仅显示 prompt，不调用模型"},
            },
            "required": ["episode"],
        },
    )
    async def _handler(args: dict[str, Any]) -> dict[str, Any]:
        try:
            request = ToolTextGenerationRequest(
                episode=int(args["episode"]),
                instructions=args.get("instructions"),
                scope=args.get("scope") or SCOPE_STALE,
                entry_ids=tuple(args.get("entry_ids") or ()),
                dry_run=bool(args.get("dry_run")),
            )
        except (TypeError, ValueError) as exc:
            # 参数自相矛盾（如 scope=all 同时点名 entry_ids）是调用方的错，报 invalid_request
            # 而不是让它冒成 internal_error——后者会引导 Agent 原样重试。
            return tool_outcome_response(
                "text_generation", ToolOutcome(problem=ToolProblem("invalid_request", str(exc)))
            )
        outcome = await run_generate_episode_script(ToolRequest(request), ctx.scope, ctx.caller, tool_services(ctx))
        return tool_outcome_response("text_generation", outcome)

    return _handler


def generate_script_plan_tool(
    ctx: ToolContext,
):
    @tool(
        "generate_script_plan",
        "按项目创作类型生成结构化 script_plan：剧情分镜、旁白分镜或参考生视频单元。"
        "广告/短片项目无 script_plan。dry_run=true 时仅返回 prompt。",
        {
            "type": "object",
            "properties": {
                "episode": {"type": "integer", "minimum": 1, "description": "剧集编号"},
                "source": {"type": "string", "description": "可选的项目内源文件相对路径"},
                "instructions": _INSTRUCTIONS_SCHEMA,
                "dry_run": {"type": "boolean", "description": "仅显示 prompt，不调用模型"},
            },
            "required": ["episode"],
        },
    )
    async def _handler(args: dict[str, Any]) -> dict[str, Any]:
        request = ToolTextGenerationRequest(
            episode=int(args["episode"]),
            source=args.get("source"),
            instructions=args.get("instructions"),
            dry_run=bool(args.get("dry_run")),
        )
        outcome = await run_generate_script_plan(
            ToolRequest(request),
            ctx.scope,
            ctx.caller,
            tool_services(ctx),
        )
        return tool_outcome_response("text_generation", outcome)

    return _handler


def confirm_script_review_tool(ctx: ToolContext):
    @tool(
        "confirm_script_review",
        "确认本集 script_plan 结构化中间态，放行 prompt_authoring 视觉生成。仅在用户已明确认可进入视觉生成时调用。",
        {
            "type": "object",
            "properties": {"episode": {"type": "integer", "description": "剧集编号"}},
            "required": ["episode"],
        },
    )
    async def _handler(args: dict[str, Any]) -> dict[str, Any]:
        outcome = await run_confirm_script_review(
            ToolRequest(int(args["episode"])),
            ctx.scope,
            ctx.caller,
            tool_services(ctx),
        )
        return tool_outcome_response("text_generation", outcome)

    return _handler


__all__ = [
    "confirm_script_review_tool",
    "discard_draft_tool",
    "generate_episode_script_tool",
    "generate_script_plan_tool",
    "get_video_capabilities_tool",
    "open_draft_tool",
    "patch_draft_tool",
    "promote_draft_tool",
]
