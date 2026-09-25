"""分集规划工具的声明：规划下一源文窗口的分集，以及把账本退回未规划状态。

``plan_episodes`` 是长任务：ArcReel Agent 等到规划完成，拿到 ``episode_plan``；外部 Agent 立即拿到
``generation_batch`` 句柄并轮询。重置不调用文本模型，两宿主都同步返回。
"""

from __future__ import annotations

from typing import Any

from lib.generation.generation_batch import GenerationBatchReadModel
from server.agent_toolset.declaration import BLOCKED, ToolDeclaration
from server.agent_toolset.envelope import json_value
from server.tool_runtime import (
    PlanEpisodesRequest,
    PlanEpisodesResult,
    ResetEpisodePlanningRequest,
    plan_episodes,
    reset_episode_planning,
)

_MIGRATION_REFUSAL = "项目数据升级失败时拒绝执行，返回 project_migration_failed problem。"


def _plan_structured(value: PlanEpisodesResult | GenerationBatchReadModel) -> dict[str, Any]:
    """批次句柄投到 ``generation_batch``，规划完成的账本摘要投到 ``episode_plan``。"""
    if isinstance(value, GenerationBatchReadModel):
        return {"generation_batch": json_value(value)}
    return {"episode_plan": json_value(value)}


PLAN_EPISODES = ToolDeclaration(
    name="plan_episodes",
    description=(
        "分集规划：从账本 planning_cursor 起读一个源文窗口，调用项目配置的文本模型一次规划出窗口内所有剧情弧完整的集"
        "（标题 / 钩子 / 原文范围；drama 另含分集大纲），在同一把项目锁内写账本、派生 source/episode_N.txt 并清理残留"
        "派生文件。返回账本摘要（每集标题、钩子、体量，以及本集原文的首句与尾句，超长句截断），首尾句供用户核对分集边界。"
        "窗口字数与每批集数上限为内部默认，project.json 顶层 planning_window_chars / planning_max_episodes 可覆盖；"
        "每集目标体量取 episode_target_units，未设时按 episode_target_duration 经口播语速折算（折算值在返回的核对材料里"
        "标明来源）。用户提供分集附加指令（如按章节对齐切分、指定某处收尾）时经 instructions 传入原文，规划 prompt 另附"
        "已规划集数、未规划余量、本窗口体量供换算切分节奏。规划按窗口分多批（长篇会多次调用本工具），instructions "
        "不持久化，规划全部完成前每一批调用都要重复带上同一附加指令。末批即全部源文规划完毕、或再次调用已无新内容时，"
        "返回会额外附全局体量核对材料（累计集数、体量最小几集、体量中位数、目标体量）：若用户给过总集数、按章节对齐等"
        "结构性偏好，须对照核对、有偏差明确告知用户；常规批次只报累计已规划集数。提交时按参与源文件记录内容指纹；若检测到"
        "已记录的源文件内容被改动或消失（源文被替换 / 编辑 / 删除，即使账本坐标仍在界内），会拒绝规划并指名变动文件，"
        "此时需调用 reset_episode_planning 做全量重置后才能重新规划。" + _MIGRATION_REFUSAL
    ),
    request_model=PlanEpisodesRequest,
    migration=BLOCKED,
    domain_key="episode_plan",
    handler=plan_episodes,
    long_task=True,
    projection=_plan_structured,
)

RESET_EPISODE_PLANNING = ToolDeclaration(
    name="reset_episode_planning",
    description=(
        "重置分集规划：把账本退回未规划状态的逃生口，不调用文本模型，不受供应商配置影响。"
        "from_episode=1 是全量重置（清空整个账本与 planning_cursor），源文被替换或删除重建、账本写坏导致规划报"
        "「起点越界」「范围无效」等错误并永久失败时用它恢复，零前置校验、任何损坏状态都能执行成功。"
        "from_episode>1 是部分重置：保留第 1..from_episode-1 集，只清除 from_episode 起的条目，游标退到第 "
        "from_episode-1 集原文范围末尾，下次 plan_episodes 从第 from_episode 集续接编号；这条路径有前置校验（全部已记录"
        "源文指纹须与当前源文一致，且保留段坐标须完整、连续、落在当前源文界内），任一不满足会拒绝执行并指明具体原因，"
        "此时改用 from_episode=1 做全量重置。两种模式都对波及已消费集（已有 script_plan / 剧本 / 媒体产物）时不执行，"
        "返回 confirmation_required=true 与受影响清单，须告知用户、确认后带 confirm_consumed=true 重新调用。"
        "任何下游产物（剧本、媒体）都不会被删除；重置范围内可由账本重造的 source/episode_N.txt 会被删除，无原文范围记录的"
        "集文件改名留底（不会丢内容），保留段的派生文件不受影响。" + _MIGRATION_REFUSAL
    ),
    request_model=ResetEpisodePlanningRequest,
    migration=BLOCKED,
    domain_key="episode_reset",
    handler=reset_episode_planning,
)

EPISODE_PLANNING_TOOLS = (PLAN_EPISODES, RESET_EPISODE_PLANNING)

__all__ = ["EPISODE_PLANNING_TOOLS", "PLAN_EPISODES", "RESET_EPISODE_PLANNING"]
