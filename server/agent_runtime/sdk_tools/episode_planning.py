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

from lib.episode_planner import (
    EpisodePlanner,
    EpisodePlanningError,
    LedgerStats,
    PlanResult,
)
from lib.episode_reset import (
    EpisodeResetError,
    ResetConfirmationRequired,
    reset_episode_planning,
)
from server.agent_runtime.sdk_tools._context import ToolContext, tool_error

# instructions 以「必须全部落实」的最高优先级注入规划 prompt，超长文本会失控 token 用量
# 并稀释规划器对原文的处理，超限按参数错误提前拒绝。上限对偏好文本足够宽松，仅挡病态输入。
_MAX_INSTRUCTIONS_LEN = 4000


def _require_positive_int(args: dict[str, Any], key: str) -> int:
    """取必填正整数入参。``bool`` 是 ``int`` 子类，须单独排除以免 ``true`` 被当成 1。"""
    value = args[key]
    if not isinstance(value, int) or isinstance(value, bool) or value < 1:
        raise ValueError(f"{key} 必须是正整数，收到 {value!r}")
    return value


def _optional_bool(args: dict[str, Any], key: str) -> bool:
    """取可选布尔入参，缺省为 False。确认类开关不做真值化，非布尔一律拒绝。"""
    value = args.get(key, False)
    if not isinstance(value, bool):
        raise ValueError(f"{key} 必须是布尔值（JSON true/false），收到 {value!r}")
    return value


def _render_ledger_stats(stats: LedgerStats) -> list[str]:
    """全局核对材料：累计集数、最小体量集、中位数、目标体量，附一句面向主 agent 的核对指令。"""
    lines = [f"累计总集数：{stats.total_episodes}"]
    if stats.smallest:
        smallest = "、".join(f"第 {num} 集（约 {units}）" for num, units in stats.smallest)
        lines.append(f"体量最小的几集：{smallest}")
    if stats.median_units is not None:
        lines.append(f"全账本体量中位数：约 {stats.median_units}")
    if stats.target_units is not None:
        lines.append(f"每集目标体量设置：约 {stats.target_units}")
    lines.append("若用户给过总集数、按章节对齐等结构性偏好，请对照以上分布核实，有偏差须向用户明确说明。")
    return lines


def _format_summary(result: PlanResult, *, header: str) -> str:
    """账本摘要：每集标题 + 钩子 + 体量（阅读单位）。

    常规批次只加「累计已规划 N 集」一行；末批/耗尽时 ``result.ledger_stats`` 非
    None，改附全局核对材料（累计集数已含在其中，不重复加那一行）。
    """
    lines = [header]
    for ep in result.episodes:
        status_note = "（stale，需重做下游产物）" if ep.ledger_status == "stale" else ""
        lines.append(f"- 第 {ep.episode} 集《{ep.title}》{status_note}｜体量约 {ep.reading_units}｜钩子：{ep.hook}")
    if result.source_exhausted:
        lines.append("源文已全部规划完毕。")
    elif result.cursor:
        lines.append(f"下一批规划起点：{result.cursor.get('source_file')} 偏移 {result.cursor.get('offset')}")
    if result.ledger_stats is not None:
        lines += _render_ledger_stats(result.ledger_stats)
    else:
        lines.append(f"累计已规划 {result.total_planned} 集。")
    lines.append(
        "请把以上摘要展示给用户做批级审阅；需要调整时先调用 reset_episode_planning 退回到"
        "最早受影响的集，再带 instructions 重新调用本工具。"
    )
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
        "以「必须全部落实」的强度对齐该偏好、优先于默认剧情弧完整性，并附带已规划集数、未规划余量、"
        "本窗口体量供换算切分节奏。规划按窗口分多批（长篇会多次调用本工具），instructions 不持久化，"
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
                lines = ["源文已全部规划完毕，没有可规划的新内容。"]
                if result.ledger_stats is not None:
                    lines += _render_ledger_stats(result.ledger_stats)
                return {"content": [{"type": "text", "text": "\n".join(lines)}]}
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
            from_episode = _require_positive_int(args, "from_episode")
            confirm_consumed = _optional_bool(args, "confirm_consumed")
        except (KeyError, ValueError) as exc:
            return {"content": [{"type": "text", "text": f"❌ 参数错误：{exc}"}], "is_error": True}

        try:
            result = reset_episode_planning(
                ctx.project_path,
                from_episode=from_episode,
                confirm_consumed=confirm_consumed,
            )
        except (EpisodeResetError, FileNotFoundError) as exc:
            return {"content": [{"type": "text", "text": f"❌ 分集规划重置失败：{exc}"}], "is_error": True}
        except Exception as exc:  # noqa: BLE001
            return tool_error("reset_episode_planning", exc)

        if isinstance(result, ResetConfirmationRequired):
            episodes = "、".join(str(num) for num in result.consumed_episodes)
            if from_episode == 1:
                aftermath = "账本清空后这些集需要重新规划"
            else:
                aftermath = f"这些集的账本条目被清除后需要重新规划，第 1..{from_episode - 1} 集保留不动"
            lines = [
                f"⚠️ 本次重置会波及已消费集（已有 step1/剧本/媒体产物）：第 {episodes} 集。尚未执行任何改动。",
                "请把影响范围告知用户；用户确认后带 confirm_consumed=true 重新调用"
                f"（剧本与媒体产物不会被删除，但{aftermath}）。",
            ]
            if result.archived_files:
                lines.append(f"其中无原文范围记录的集文件会改名留底：{'、'.join(result.archived_files)}")
            return {"content": [{"type": "text", "text": "\n".join(lines)}]}

        if from_episode == 1:
            lines = [f"✅ 已全量重置分集规划：清空 {len(result.removed_episodes)} 集，planning_cursor 已置空。"]
        else:
            lines = [
                f"✅ 已部分重置分集规划：清空第 {from_episode} 集起共 {len(result.removed_episodes)} 集，"
                f"planning_cursor 已退到第 {from_episode - 1} 集原文范围末尾。"
            ]
        if result.deleted_files:
            lines.append(f"已删除可重造的派生集文件 {len(result.deleted_files)} 个。")
        if result.archived_files:
            archived = "、".join(f"{src} → {dst}" for src, dst in result.archived_files)
            lines.append(f"无原文范围记录的集文件已改名留底（内容保留）：{archived}")
        if result.consumed_episodes:
            consumed = "、".join(str(num) for num in result.consumed_episodes)
            lines.append(f"第 {consumed} 集的剧本 / 媒体产物仍在磁盘，未删除。")
        if from_episode == 1:
            lines.append("账本已空，请调用 plan_episodes 从头重新规划（集号从第 1 集起）。")
        else:
            lines.append(f"请调用 plan_episodes 继续规划（新集号从第 {from_episode} 集起）。")
        return {"content": [{"type": "text", "text": "\n".join(lines)}]}

    return _handler


__all__ = ["plan_episodes_tool", "reset_episode_planning_tool"]
