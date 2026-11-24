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

SCRIPT_PLAN_FILENAMES: dict[str, str] = {
    "drama": "script_plan_normalized_script.json",
    "narration": "script_plan_segments.json",
}

SCRIPT_PLAN_LEGACY_FILENAMES: dict[str, tuple[str, ...]] = {
    "drama": ("script_plan_normalized_script.md",),
    "narration": ("script_plan_segments.md",),
}

REFERENCE_VIDEO_SCRIPT_PLAN_FILENAME = "script_plan_reference_units.json"

REFERENCE_VIDEO_SCRIPT_PLAN_LEGACY_FILENAME = "script_plan_reference_units.md"

REFERENCE_VIDEO_SCRIPT_PLAN_QUARANTINE_FILENAME = "script_plan_reference_units.invalid.json"
REFERENCE_VIDEO_PROMPT_AUTHORING_QUARANTINE_FILENAME = "prompt_authoring_reference_script.invalid.json"
DRAMA_SCRIPT_PLAN_QUARANTINE_FILENAME = "script_plan_normalized_script.invalid.json"
NARRATION_SCRIPT_PLAN_QUARANTINE_FILENAME = "script_plan_segments.invalid.json"

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
    工作流状态与产物指纹按同一路径取源文，各侧共享本函数以保持同一口径。
    """
    return f"source/episode_{episode}.txt"


def episode_source_path(project_path: Path, episode: int) -> Path:
    """该集派生源文的绝对路径 ``{project}/source/episode_N.txt``。

    生产侧（分集规划的派生文件写盘与对账）按本函数取路径，与消费侧共享
    ``episode_source_relpath`` 的同一份路径形状。
    """
    return project_path / episode_source_relpath(episode)


def episode_script_filename(episode: int) -> str:
    """该集剧本文件名 ``episode_N.json``（不含 ``scripts/`` 目录前缀）。"""
    return f"episode_{episode}.json"


def episode_script_relpath(episode: int) -> str:
    """该集剧本相对项目根的默认路径 ``scripts/episode_N.json``。"""
    return f"scripts/{episode_script_filename(episode)}"
