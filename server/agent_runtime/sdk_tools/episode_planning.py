"""SDK MCP tools for episode planning (plan / reset).

主 agent 单次调用、只收账本摘要；窗口读取、文本模型调用、机械校验重试与
同锁提交全部在 :class:`lib.episode_planner.EpisodePlanner` 内完成。重置走
:mod:`lib.episode_reset`，不经文本模型。用户需要调整已规划内容时走「重置 +
重新规划」：先调用 reset_episode_planning 退回到最早受影响的集，再带
instructions 分批重新调用 plan_episodes。
"""

from __future__ import annotations

from typing import Any

from claude_agent_sdk import tool

from lib.episode_planner import EpisodePlanner
from lib.episode_reset import reset_episode_planning
from server.media_tools.context import (
    ToolContext,
    tool_outcome_response,
    tool_services,
)
from server.tool_runtime import (
    MAX_INSTRUCTIONS_LEN,
    PlanEpisodesRequest,
    ResetEpisodePlanningRequest,
    ToolOutcome,
    ToolProblem,
    ToolRequest,
    plan_episodes,
)
from server.tool_runtime import (
    reset_episode_planning as reset_episode_planning_handler,
)


def plan_episodes_tool(ctx: ToolContext):
    @tool(
        "plan_episodes",
        "分集规划：从账本 planning_cursor 起读一个源文窗口，调用项目配置的文本模型一次规划出"
        "窗口内所有剧情弧完整的集（标题/钩子/原文范围；drama 另含分集大纲），在同一把项目锁内"
        "写账本、派生 source/episode_N.txt 并清理残留派生文件。返回账本摘要（每集标题+钩子+体量）。"
        "窗口字数与每批集数上限为内部默认，project.json 顶层 planning_window_chars / "
        "planning_max_episodes 可覆盖，每集目标体量沿用 episode_target_units。"
        "用户表达分集意见（如按章节对齐切分、指定某处收尾）时经 instructions 传入原文；意见原样注入"
        "规划 prompt 的「用户意见」分节（遵循强度由正文表达，需要强约束时在正文写明），并附带已规划"
        "集数、未规划余量、本窗口体量供换算切分节奏。规划按窗口分多批（长篇会多次调用本工具），instructions 不持久化，"
        "规划全部完成前每一批调用都要重复带上同一偏好。末批即全部源文规划完毕、或再次调用已无新内容"
        "时，返回会额外附全局体量核对材料（累计集数、体量最小几集、体量中位数、目标体量）："
        "若用户给过总集数、按章节对齐等结构性偏好，须对照核对、有偏差明确告知用户；常规批次只报"
        "累计已规划集数。提交时按参与源文件记录内容指纹；若检测到已记录的源文件内容被改动或消失"
        "（源文被替换/编辑/删除，即使账本坐标仍在界内），会拒绝规划并指名变动文件，此时需调用 "
        "reset_episode_planning 做全量重置后才能重新规划。",
        {
            "type": "object",
            "properties": {
                "instructions": {
                    "type": "string",
                    "description": (
                        "用户分集意见原文（可选，如「按章节对齐切分」）；原样注入规划 prompt 的"
                        "「用户意见」分节，遵循强度由正文表达。每批调用都要重复带上，"
                        f"缺省/空白视同未传，最长 {MAX_INSTRUCTIONS_LEN} 字符"
                    ),
                }
            },
        },
    )
    async def _handler(args: dict[str, Any]) -> dict[str, Any]:
        try:
            request = PlanEpisodesRequest.model_validate(args)
        except ValueError as exc:
            outcome = ToolOutcome(problem=ToolProblem("invalid_request", f"❌ 参数错误：{exc}"))
        else:
            outcome = await plan_episodes(
                ToolRequest(request), ctx.scope, ctx.caller, tool_services(ctx), planner_cls=EpisodePlanner
            )
        return tool_outcome_response("episode_plan", outcome)

    return _handler


def reset_episode_planning_tool(ctx: ToolContext):
    @tool(
        "reset_episode_planning",
        "重置分集规划：把账本退回未规划状态的逃生口，不调用文本模型，不受供应商配置影响。"
        "from_episode=1 是全量重置（清空整个账本与 planning_cursor），源文被替换或删除重建、"
        "账本写坏导致规划报「起点越界」「范围无效」等错误并永久失败时用它恢复，零前置校验、"
        "任何损坏状态都能执行成功。from_episode>1 是部分重置：保留第 1..from_episode-1 集，"
        "只清除 from_episode 起的条目，游标退到第 from_episode-1 集原文范围末尾，下次 "
        "plan_episodes 从第 from_episode 集续接编号；这条路径有前置校验（全部已记录源文指纹须"
        "与当前源文一致，且保留段坐标须完整、连续、落在当前源文界内），任一不满足会拒绝执行并"
        "指明具体原因，此时改用 from_episode=1 做全量重置。"
        "两种模式都对波及已消费集（已有 step1/剧本/媒体产物）时不执行并返回受影响清单，须告知用户、"
        "确认后带 confirm_consumed=true 重新调用。任何下游产物（剧本、媒体）都不会被删除；"
        "重置范围内可由账本重造的 source/episode_N.txt 会被删除，无原文范围记录的集文件改名留底"
        "（不会丢内容），保留段的派生文件不受影响。",
        {
            "type": "object",
            "properties": {
                "from_episode": {
                    "type": "integer",
                    "description": "重置起点集号；1 为全量重置，大于 1 为部分重置（保留其前的集）",
                },
                "confirm_consumed": {
                    "type": "boolean",
                    "description": "已向用户确认波及的已消费集后置 true",
                },
            },
            "required": ["from_episode"],
        },
    )
    async def _handler(args: dict[str, Any]) -> dict[str, Any]:
        try:
            request = ResetEpisodePlanningRequest.model_validate(args)
        except ValueError as exc:
            outcome = ToolOutcome(problem=ToolProblem("invalid_request", f"❌ 参数错误：{exc}"))
        else:
            outcome = await reset_episode_planning_handler(
                ToolRequest(request),
                ctx.scope,
                ctx.caller,
                tool_services(ctx),
                resetter=reset_episode_planning,
            )
        return tool_outcome_response("episode_reset", outcome)

    return _handler


__all__ = ["plan_episodes_tool", "reset_episode_planning_tool"]
