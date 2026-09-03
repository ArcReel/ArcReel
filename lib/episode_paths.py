"""script_plan 中间态文件名与 episode 剧本路径的单一真相源。

内容确认、状态计算、web 草稿读写层、剧本生成器与 SDK 文本工具统一消费本模块的路径映射。
内容确认找不到 script_plan 文件时按 ``no_script_plan`` 放行 prompt_authoring（文件不存在不等于故障），因此所有读写侧
必须共享同一组文件名与目录。新增走结构化两段式的 content_mode 只需在
``SCRIPT_PLAN_FILENAMES`` 登记结构化文件名，内容确认、状态计算、web 读取与写盘便会保持一致。

以下语义差异必须保留：

- 内容确认只认结构化 ``.json``（``SCRIPT_PLAN_FILENAMES`` / ``script_plan_filename``）；
- 状态计算与 web 读取层额外兼认旧版 ``.md`` 别名（``SCRIPT_PLAN_LEGACY_FILENAMES`` /
  ``script_plan_read_candidates``），令存量在制品仍被识别 / 可浏览——「是否分过段」与「格式迁移」
  是两回事。
"""

from __future__ import annotations

from pathlib import Path

#: 结构化 script_plan 中间态文件名（按 content_mode）。内容确认仅认这两类。
#: 新增走结构化两段式的 content_mode 在此登记一处即可让 gate / 状态计算 / web 读取 / 写盘一致。
SCRIPT_PLAN_FILENAMES: dict[str, str] = {
    "drama": "script_plan_normalized_script.json",
    "narration": "script_plan_segments.json",
}

#: 旧版非结构化 script_plan 别名（按 content_mode）。仅供状态计算 / web 读取层兼认存量在制品，
#: 内容确认与写盘侧不认。新增 content_mode 无历史遗留，无需登记于此。
SCRIPT_PLAN_LEGACY_FILENAMES: dict[str, tuple[str, ...]] = {
    "drama": ("script_plan_normalized_script.md",),
    "narration": ("script_plan_segments.md",),
}

#: reference_video 的结构化 script_plan 中间态文件名。reference_video 是 generation_mode 维度、
#: 跨 content_mode（narration / drama 均可），不进按 content_mode 键控的 ``SCRIPT_PLAN_FILENAMES``；
#: 内容确认按 script_plan 变体单独纳入本文件名（见 ``lib.script_review.script_plan_kind``）。
REFERENCE_VIDEO_SCRIPT_PLAN_FILENAME = "script_plan_reference_units.json"

#: reference_video 旧版自由文本 script_plan 别名。仅供读取 / 浏览层兼认存量在制品；
#: 写盘与生成侧不认——仅存在旧 ``.md`` 时生成侧给出重跑拆分的明确提示。
REFERENCE_VIDEO_SCRIPT_PLAN_LEGACY_FILENAME = "script_plan_reference_units.md"

#: 草稿文件名。与正式文件同目录、不同名：正式文件因此永远只装校验通过的内容，而待
#: 处置的产物不被丢弃——Agent 就地改草稿再调晋升工具重判。内容确认与生成侧都要认
#: 这些名字（草稿在场时阻塞确认与 prompt_authoring），故与正式文件名收敛在同一处，避免任一侧
#: 漏认会静默绕过待处置草稿的阻塞状态。
REFERENCE_VIDEO_SCRIPT_PLAN_QUARANTINE_FILENAME = "script_plan_reference_units.invalid.json"
REFERENCE_VIDEO_PROMPT_AUTHORING_QUARANTINE_FILENAME = "prompt_authoring_reference_script.invalid.json"
DRAMA_SCRIPT_PLAN_QUARANTINE_FILENAME = "script_plan_normalized_script.invalid.json"
NARRATION_SCRIPT_PLAN_QUARANTINE_FILENAME = "script_plan_segments.invalid.json"

#: 对 Agent 写禁的正式 script_plan 文件名（见 ``AgentAccessPolicy._is_protected_formal_script_plan``）。
#: 收的是文件名而非按项目变体解析的路径：写禁在会话装配前就要成立，而项目的 content_mode /
#: generation_mode 是运行时可变的，按项目状态分叉判定会让改过模式的项目落进无人拦的缝里。
#: 判据是「该变体的修改已有草稿通道可走」——写禁与替代通道成对出现，只拒不给出路会
#: 把 Agent 卡死。三条路线的正式 script_plan 均已有草稿通道，故三个文件名全部在表内。
AGENT_PROTECTED_SCRIPT_PLAN_FILENAMES: frozenset[str] = frozenset(
    {SCRIPT_PLAN_FILENAMES["drama"], SCRIPT_PLAN_FILENAMES["narration"], REFERENCE_VIDEO_SCRIPT_PLAN_FILENAME}
)


def script_plan_filename(content_mode: str) -> str | None:
    """该 content_mode 的结构化 script_plan 文件名；不走结构化 script_plan（如 ad）时返回 None。"""
    return SCRIPT_PLAN_FILENAMES.get(content_mode)


def script_plan_read_candidates(content_mode: str) -> tuple[str, ...]:
    """结构化 script_plan 文件名 + 旧版 ``.md`` 别名（读取 / 浏览侧候选，主文件缺失时回落探测）。

    不走结构化 script_plan 的模式返回空元组。内容确认不用此函数（只认结构化 ``.json``）。
    """
    primary = SCRIPT_PLAN_FILENAMES.get(content_mode)
    if primary is None:
        return ()
    return (primary, *SCRIPT_PLAN_LEGACY_FILENAMES.get(content_mode, ()))


def episode_drafts_dir(project_path: Path, episode: int) -> Path:
    """该集 script_plan 草稿目录 ``{project}/drafts/episode_N``。"""
    return project_path / "drafts" / f"episode_{episode}"


def episode_source_relpath(episode: int) -> str:
    """该集派生源文相对项目根的路径 ``source/episode_N.txt``。

    分集规划工具按账本 ``source_range`` 派生该文件；script_plan 生成侧缺省读它、
    工作流状态与产物指纹按同一路径取源文，三处共享本函数以保持同一口径。
    """
    return f"source/episode_{episode}.txt"


def episode_script_filename(episode: int) -> str:
    """该集剧本文件名 ``episode_N.json``（不含 ``scripts/`` 目录前缀）。"""
    return f"episode_{episode}.json"


def episode_script_relpath(episode: int) -> str:
    """该集剧本相对项目根的默认路径 ``scripts/episode_N.json``。"""
    return f"scripts/{episode_script_filename(episode)}"
