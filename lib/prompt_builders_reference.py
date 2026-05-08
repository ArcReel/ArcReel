"""参考生视频模式 Prompt 构建器（Spec §7.3）。

设计原则与 prompt_builders_script.py 一致：
- 不重复 schema 已声明的枚举（type 等）；让 response_schema 直接约束。
- 多选枚举字段不在 prompt 里写"如何选"判据；让模型按画面内容自行决定。
- 字段说明给指导和 example，不堆"必须 / 禁止"清单。
- 跨 backend 时长 / references 上限通过参数显式注入，不在文本里硬编码秒数。
"""

from __future__ import annotations


def _format_asset_names(assets: dict | None) -> str:
    if not assets:
        return "（无）"
    return "\n".join(
        f"- {name}: {meta.get('description', '') if isinstance(meta, dict) else ''}" for name, meta in assets.items()
    )


def build_reference_video_prompt(
    *,
    project_overview: dict,
    style: str,
    style_description: str,
    characters: dict,
    scenes: dict,
    props: dict,
    units_md: str,
    supported_durations: list[int],
    max_refs: int,
    max_duration: int | None = None,
    aspect_ratio: str = "9:16",
    target_language: str = "中文",
) -> str:
    """构建参考生视频模式的 LLM Prompt。

    Args:
        project_overview: 项目概述（synopsis, genre, theme, world_setting）。
        style / style_description: 视觉风格标签与描述。
        characters / scenes / props: 三类已注册资产字典（用于候选列表）。
        units_md: `step1_reference_units.md` 内容（subagent 输出）。
        supported_durations: 当前视频模型支持的单镜头时长列表（秒）。
        max_refs: 当前视频模型支持的最大参考图数。
        max_duration: 当前视频模型的单次生成时长上限（秒）。传入时 prompt 会显式
            引导 LLM 让 unit 总时长贴近该值，避免默认挑最短值；为 None 时不插入该段。
    """
    character_names = list(characters.keys())
    scene_names = list(scenes.keys())
    prop_names = list(props.keys())

    durations_desc = "/".join(str(d) for d in supported_durations) + "s"
    max_duration_line = (
        f"\n   - unit 内所有 Shot `duration` 之和宜贴近 {max_duration} 秒（当前模型上限），"
        f"除非内容明显不需要这么长；不要默认挑最短值，也不得超过 {max_duration}。"
        if max_duration is not None
        else ""
    )

    return f"""# 角色与任务

你是一位资深的短视频分镜编剧，本任务是为「参考生视频」模式产出 JSON 剧本。
你的任务：基于下方 step1_units 表，按 schema 产出 ReferenceVideoScript。

**输出语言**：所有字符串值必须使用 {target_language}；JSON 键名 / 枚举值保持英文。
**结构约束**：字段 / 枚举 / 必填项由 response_schema 强制；本提示只解释**如何写好每个字段**。

# 上下文

<overview>
{project_overview.get("synopsis", "")}

题材：{project_overview.get("genre", "")}
主题：{project_overview.get("theme", "")}
世界观：{project_overview.get("world_setting", "")}
</overview>

<style>
风格：{style}
描述：{style_description}
画面比例：{aspect_ratio}
</style>

<characters>
{_format_asset_names(characters)}
</characters>

<scenes>
{_format_asset_names(scenes)}
</scenes>

<props>
{_format_asset_names(props)}
</props>

<step1_units>
{units_md}
</step1_units>

# 字段写作指引

对每个 video_unit，按下列要求填写字段：

a. **unit_id**：保留 step1 中的 `E{{集}}U{{序号}}`，不要改格式。

b. **shots**：1-4 个 Shot。
   - `duration`：整数秒，取值必须在当前模型支持列表中：{durations_desc}。{max_duration_line}
   - `text`：中文镜头描述，聚焦此刻可见画面。仅用 `@名称` 引用角色 / 场景 / 道具——**不要**写外貌、服装、场景细节（这些由参考图提供视觉一致性）。
     好例：「@角色A 立于 @场景A 前，左手紧握 @道具A，目光投向远处」。
     反例：「身穿某色服装的角色A 站在某色场景A 前，手里紧握着某色道具A」（外貌 / 服装 / 颜色应由参考图承担）。
   - 单 unit 内所有 Shot `duration` 之和即该 unit `duration_seconds`。

c. **references**：`{{type, name}}` 列表，顺序决定 `[图N]` 编号。
   - `name` 必须来自候选：
     - character: {", ".join(character_names) or "（无）"}
     - scene: {", ".join(scene_names) or "（无）"}
     - prop: {", ".join(prop_names) or "（无）"}
   - 每个 shot `text` 中出现的 `@名称` 都要在 references 注册一次。
   - **references 数量不超过 {max_refs}**（模型上限）；超出时把次要角色合并到背景描述。

d. **duration_seconds**：所有 shot `duration` 之和；不要手动覆盖。

# 顶层字段

- `episode` / `title` / `summary` / `novel.title` / `novel.chapter` 必填。
- `content_mode` 固定 "reference_video"。
- `duration_seconds` 可先写 0，由 caller 重算。

# 复核

- 每 unit 最多 4 个 shot；shot 时长之和贴近 step1 预估。
- `@名称` 只能引用 characters / scenes / props 三表中已注册的名字。
- 不要在 shot `text` 中描写外貌、服装、场景细节。
- 不要发明新资产。

请按 step1_units 顺序逐 unit 产出。
"""
