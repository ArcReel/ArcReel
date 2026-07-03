"""SDK MCP tools for episode planning (plan / replan).

主 agent 单次调用、只收账本摘要；窗口读取、文本模型调用、机械校验重试与
同锁提交全部在 :class:`lib.episode_planner.EpisodePlanner` 内完成。
"""

from __future__ import annotations

from typing import Any

from claude_agent_sdk import tool

from lib.episode_planner import (
    EpisodePlanner,
    EpisodePlanningError,
    PlanResult,
    ReplanConfirmationRequired,
)
from server.agent_runtime.sdk_tools._context import ToolContext, tool_error

# instructions 以「必须全部落实」的最高优先级注入规划 prompt，超长文本会失控 token 用量
# 并稀释规划器对原文的处理，超限按参数错误提前拒绝。上限对偏好文本足够宽松，仅挡病态输入。
_MAX_INSTRUCTIONS_LEN = 4000


def _format_summary(result: PlanResult, *, header: str) -> str:
    """账本摘要：每集标题 + 钩子 + 体量（阅读单位）。"""
    lines = [header]
    for ep in result.episodes:
        status_note = "（stale，需重做下游产物）" if ep.ledger_status == "stale" else ""
        lines.append(f"- 第 {ep.episode} 集《{ep.title}》{status_note}｜体量约 {ep.reading_units}｜钩子：{ep.hook}")
    if result.settings_updated:
        updated = "、".join(f"{key}={value}" for key, value in result.settings_updated.items())
        lines.append(f"已回写项目设置（后续批次自动继承）：{updated}")
    if result.source_exhausted:
        lines.append("源文已全部规划完毕。")
    elif result.cursor:
        lines.append(f"下一批规划起点：{result.cursor.get('source_file')} 偏移 {result.cursor.get('offset')}")
    lines.append("请把以上摘要展示给用户做批级审阅；需要调整时调用 replan_episodes。")
    return "\n".join(lines)


def plan_episodes_tool(ctx: ToolContext):
    @tool(
        "plan_episodes",
        "分集规划：从账本 planning_cursor 起读一个源文窗口，调用项目配置的文本模型一次规划出"
        "窗口内所有剧情弧完整的集（标题/钩子/原文范围；drama 另含分集大纲），在同一把项目锁内"
        "写账本、派生 source/episode_N.txt 并清理残留派生文件。返回账本摘要（每集标题+钩子+体量）。"
        "窗口字数与每批集数上限为内部默认，project.json 顶层 planning_window_chars / "
        "planning_max_episodes 可覆盖，每集目标体量沿用 episode_target_units。"
        "用户表达分集偏好（如按章节对齐切分、指定某处收尾）时经 instructions 传入原文；规划器会"
        "以「必须全部落实」的强度对齐该偏好、优先于默认剧情弧完整性。规划按窗口分多批（长篇会多次"
        "调用本工具），instructions 不持久化，规划全部完成前每一批调用都要重复带上同一偏好。",
        {
            "type": "object",
            "properties": {
                "instructions": {
                    "type": "string",
                    "description": (
                        "用户分集偏好原文（可选，如「按章节对齐切分」）；每批调用都要重复带上，"
                        f"缺省/空白视同未传，最长 {_MAX_INSTRUCTIONS_LEN} 字符"
                    ),
                }
            },
        },
    )
    async def _handler(args: dict[str, Any]) -> dict[str, Any]:
        raw_instructions = args.get("instructions")
        if raw_instructions is not None and not isinstance(raw_instructions, str):
            text = f"❌ 参数错误：instructions 必须是字符串，收到 {type(raw_instructions).__name__}"
            return {"content": [{"type": "text", "text": text}], "is_error": True}
        if isinstance(raw_instructions, str) and len(raw_instructions) > _MAX_INSTRUCTIONS_LEN:
            text = f"❌ 参数错误：instructions 过长（{len(raw_instructions)} 字符，上限 {_MAX_INSTRUCTIONS_LEN}），请精简后重试"
            return {"content": [{"type": "text", "text": text}], "is_error": True}
        instructions = raw_instructions.strip() if isinstance(raw_instructions, str) else ""
        try:
            planner = await EpisodePlanner.create(ctx.project_path)
            result = await planner.plan(instructions=instructions or None)
            if not result.episodes and result.source_exhausted:
                return {"content": [{"type": "text", "text": "源文已全部规划完毕，没有可规划的新内容。"}]}
            return {
                "content": [
                    {"type": "text", "text": _format_summary(result, header=f"✅ 已规划 {len(result.episodes)} 集：")}
                ]
            }
        except (EpisodePlanningError, FileNotFoundError) as exc:
            return {"content": [{"type": "text", "text": f"❌ 分集规划失败：{exc}"}], "is_error": True}
        except Exception as exc:  # noqa: BLE001
            return tool_error("plan_episodes", exc)

    return _handler


def replan_episodes_tool(ctx: ToolContext):
    @tool(
        "replan_episodes",
        "分集重排：按用户自由文本意见（instructions 可同时包含任意多处意见）重排账本中 "
        "from_episode 起的已规划集，from_episode 取意见中最早受影响的集；之前的集作为已定上下文。"
        "波及已消费集（已有 step1/剧本/媒体）时不执行并返回受影响清单，须告知用户、确认后带 "
        "confirm_consumed=true 重新调用，这些集会标 stale（产物不删除）。全局性意见（每集体量等）"
        "自动回写项目设置。返回重排后的账本摘要。",
        {
            "type": "object",
            "properties": {
                "from_episode": {"type": "integer", "description": "重排起点集号（意见中最早受影响的集）"},
                "instructions": {
                    "type": "string",
                    "description": f"用户重排意见原文（可含多处意见），最长 {_MAX_INSTRUCTIONS_LEN} 字符",
                },
                "confirm_consumed": {
                    "type": "boolean",
                    "description": "已向用户确认波及的已消费集后置 true",
                },
            },
            "required": ["from_episode", "instructions"],
        },
    )
    async def _handler(args: dict[str, Any]) -> dict[str, Any]:
        # 参数校验与 planner 调用分属两个 try：校验阶段的 KeyError/ValueError 才算参数错误，
        # planner 内部（如供应商未配置）抛出的 ValueError 走 tool_error 通用路径，不被误标。
        try:
            raw_from_episode = args["from_episode"]
            if not isinstance(raw_from_episode, int) or isinstance(raw_from_episode, bool) or raw_from_episode < 1:
                raise ValueError(f"from_episode 必须是正整数，收到 {raw_from_episode!r}")
            from_episode = raw_from_episode
            raw_instructions = args["instructions"]
            if not isinstance(raw_instructions, str):
                raise ValueError(f"instructions 必须是字符串，收到 {type(raw_instructions).__name__}")
            instructions = raw_instructions.strip()
            if not instructions:
                raise ValueError("instructions 不能为空")
            if len(raw_instructions) > _MAX_INSTRUCTIONS_LEN:
                raise ValueError(
                    f"instructions 过长（{len(raw_instructions)} 字符，上限 {_MAX_INSTRUCTIONS_LEN}），请精简后重试"
                )
            raw_confirm = args.get("confirm_consumed", False)
            if not isinstance(raw_confirm, bool):
                raise ValueError(f"confirm_consumed 必须是布尔值（JSON true/false），收到 {raw_confirm!r}")
            confirm_consumed = raw_confirm
        except (KeyError, ValueError) as exc:
            return {"content": [{"type": "text", "text": f"❌ 参数错误：{exc}"}], "is_error": True}

        try:
            planner = await EpisodePlanner.create(ctx.project_path)
            result = await planner.replan(from_episode, instructions, confirm_consumed=confirm_consumed)
            if isinstance(result, ReplanConfirmationRequired):
                episodes = "、".join(str(num) for num in result.consumed_episodes)
                return {
                    "content": [
                        {
                            "type": "text",
                            "text": (
                                f"⚠️ 本次重排会波及已消费集（已有 step1/剧本/媒体产物）：第 {episodes} 集。"
                                "尚未执行任何改动。请把影响范围告知用户；用户确认后带 confirm_consumed=true "
                                "重新调用（这些集将标 stale，产物不删除、重做时沿版本机制替换）。"
                            ),
                        }
                    ]
                }
            header = f"✅ 已重排第 {from_episode} 集起的 {len(result.episodes)} 集："
            if result.stale_episodes:
                stale = "、".join(str(num) for num in result.stale_episodes)
                header += f"（第 {stale} 集标 stale，需重做下游产物）"
            return {"content": [{"type": "text", "text": _format_summary(result, header=header)}]}
        except (EpisodePlanningError, FileNotFoundError) as exc:
            return {"content": [{"type": "text", "text": f"❌ 分集重排失败：{exc}"}], "is_error": True}
        except Exception as exc:  # noqa: BLE001
            return tool_error("replan_episodes", exc)

    return _handler


__all__ = ["plan_episodes_tool", "replan_episodes_tool"]
