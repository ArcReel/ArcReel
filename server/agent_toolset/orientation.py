"""定向工具的声明：制作计划、视频能力与提示词预览，供决定下一步之前读取。"""

from __future__ import annotations

from lib.workflow.workflow_plan import WorkflowPlanRequest
from server.agent_toolset.declaration import READ_CHECK, Exempt, ToolDeclaration
from server.tool_runtime import (
    NoArguments,
    PromptPreviewRequest,
    get_image_capabilities,
    get_prompt_preview,
    get_video_capabilities,
    get_workflow_plan,
)

GET_WORKFLOW_PLAN = ToolDeclaration(
    name="get_workflow_plan",
    description=(
        "读取项目的权威制作计划：内容现状、各 AI 操作的准入 status.operations、有序步骤、阻断原因、数据问题、"
        "活动任务观测、视频准入与建议的下一动作 next_action。每一步动手前先读它：用户点名的操作在 "
        "operations 里为 admitted 就执行，refused 时转述 reason；用户说继续时按 next_action 行动，"
        "next_alternatives 非空时由用户在并列选项中选。confirmed_request_durations "
        "只作用于本次计划，不写入项目。只读，无副作用。"
        "项目数据升级失败时计划只含这一条 project_migration_failed 问题，下一动作指向修复。"
    ),
    request_model=WorkflowPlanRequest,
    migration=READ_CHECK,
    domain_key="workflow_plan",
    handler=get_workflow_plan,
)

GET_IMAGE_CAPABILITIES = ToolDeclaration(
    name="get_image_capabilities",
    description="查询项目文生图（t2i）和图生图（i2i）桶的模型与 max_reference_images 生效上限。null 表示未收录，0 表示不接受参考图。配置不可用时该桶返回 problem。",
    request_model=NoArguments,
    migration=Exempt("只读：模型能力查询，不读写产物。"),
    domain_key="image_capabilities",
    handler=get_image_capabilities,
)

GET_VIDEO_CAPABILITIES = ToolDeclaration(
    name="get_video_capabilities",
    description=(
        "查询项目当前视频模型的能力（model 粒度：供应商、模型、支持的时长等）与项目偏好，并给出按项目生成模式"
        "定轴的时长约束 duration_constraints；全项目同一口径，无需指定剧集。参考生视频项目另含 "
        "reference_unit_durations（带图与无图的档位、各桶 endpoint_fixed 标志及成因；无图不可解析时附 problem）。"
        "end_frame_unit_durations 同时给出 without_end_frame 与 with_end_frame 档位、excluded 成因与 problem；规划期用无尾帧档位，已有分镜挂尾帧时用带尾帧档位，越界须改秒数或移除尾帧，不自动改秒数。"
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
        "读取一个分镜条目或参考视频单元最终会送进图像 / 视频模型的提示词文本，与执行路径同一渲染出口，逐字等于生成时实际"
        "发出的提示词；某一侧提示词尚待生成或为空时，该侧带稳定的 unavailable 原因码而不是文本。"
        "只读：不向供应商发请求、不产生费用。项目数据升级失败时返回 project_migration_failed problem。"
    ),
    request_model=PromptPreviewRequest,
    migration=READ_CHECK,
    domain_key="prompt_preview",
    handler=get_prompt_preview,
)

ORIENTATION_TOOLS = (GET_WORKFLOW_PLAN, GET_VIDEO_CAPABILITIES, GET_IMAGE_CAPABILITIES, GET_PROMPT_PREVIEW)

__all__ = [
    "GET_IMAGE_CAPABILITIES",
    "GET_PROMPT_PREVIEW",
    "GET_VIDEO_CAPABILITIES",
    "GET_WORKFLOW_PLAN",
    "ORIENTATION_TOOLS",
]
