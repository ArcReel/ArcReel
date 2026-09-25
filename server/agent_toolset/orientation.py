"""定向工具的声明：制作计划、视频能力与提示词预览，供决定下一步之前读取。"""

from __future__ import annotations

from lib.workflow.workflow_plan import WorkflowPlanRequest
from server.agent_toolset.declaration import READ_CHECK, Exempt, ToolDeclaration
from server.tool_runtime import (
    NoArguments,
    PromptPreviewRequest,
    get_prompt_preview,
    get_video_capabilities,
    get_workflow_plan,
)

GET_WORKFLOW_PLAN = ToolDeclaration(
    name="get_workflow_plan",
    description=(
        "读取项目的权威制作计划：有序步骤、阻断原因、结构问题、活动任务观测、视频准入与唯一的下一动作 "
        "next_action。每一步动手前先读它，按 next_action 行动。narration_delivery 与 "
        "confirmed_request_durations 只作用于本次计划，不写入项目。只读，无副作用。"
        "项目数据升级失败时计划只含这一条 project_migration_failed 问题，下一动作指向修复。"
    ),
    request_model=WorkflowPlanRequest,
    migration=READ_CHECK,
    domain_key="workflow_plan",
    handler=get_workflow_plan,
)

GET_VIDEO_CAPABILITIES = ToolDeclaration(
    name="get_video_capabilities",
    description=(
        "查询项目当前视频模型的能力（model 粒度：供应商、模型、支持的时长等）与项目偏好，并给出按项目生成模式"
        "定轴的时长约束 duration_constraints；全项目同一口径，无需指定剧集。参考生视频项目另含 "
        "reference_unit_durations（带图与无图的档位、各桶 endpoint_fixed 标志及成因；无图不可解析时附 problem）。"
        "只读，不向供应商发请求。视频模型未配置或能力无法解析时返回相应 problem。"
    ),
    request_model=NoArguments,
    migration=Exempt("只读：查询模型能力与项目偏好，不读写产物清单、不签发写入凭据；迁移失败时仍须可用，供规划修复。"),
    domain_key="video_capabilities",
    handler=get_video_capabilities,
)

GET_PROMPT_PREVIEW = ToolDeclaration(
    name="get_prompt_preview",
    description=(
        "读取一个分镜条目最终会送进图像 / 视频模型的提示词文本，与执行路径同一渲染出口，逐字等于生成时实际"
        "发出的提示词；某一侧提示词尚待生成或为空时，该侧带稳定的 unavailable 原因码而不是文本。"
        "只读：不向供应商发请求、不产生费用。项目数据升级失败时返回 project_migration_failed problem。"
    ),
    request_model=PromptPreviewRequest,
    migration=READ_CHECK,
    domain_key="prompt_preview",
    handler=get_prompt_preview,
)

ORIENTATION_TOOLS = (GET_WORKFLOW_PLAN, GET_VIDEO_CAPABILITIES, GET_PROMPT_PREVIEW)

__all__ = [
    "GET_PROMPT_PREVIEW",
    "GET_VIDEO_CAPABILITIES",
    "GET_WORKFLOW_PLAN",
    "ORIENTATION_TOOLS",
]
