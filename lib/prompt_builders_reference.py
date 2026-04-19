"""参考生视频模式 Prompt 构建器。

Spec §7.3 的 LLM prompt 模板。
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
    """
    character_names = list(characters.keys())
    scene_names = list(scenes.keys())
    prop_names = list(props.keys())

    durations_desc = "/".join(str(d) for d in supported_durations) + "s"

    return f"""你的任务是为短视频生成「参考生视频」模式的 JSON 剧本。请仔细遵循以下指示：

**重要：所有输出内容必须使用{target_language}。仅 JSON 键名和枚举值使用英文。**

1. 你将获得故事概述、视觉风格、已注册的角色/场景/道具列表，以及 Step 1 已拆分好的 video_units 表。

2. 为每个 video_unit 生成 `ReferenceVideoScript.video_units[]` 数组项，并遵循如下约束：

<overview>
{project_overview.get("synopsis", "")}

题材类型：{project_overview.get("genre", "")}
核心主题：{project_overview.get("theme", "")}
世界观设定：{project_overview.get("world_setting", "")}
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

3. 每个 unit 的生成规则：

a. **unit_id**：保留 Step 1 中的 `E{{集数}}U{{序号}}`。

b. **shots**：1-4 个 Shot。每个 Shot 含：
   - `duration`：整数秒，取值必须在当前模型支持列表中：{durations_desc}
   - `text`：中文镜头描述，聚焦当下瞬间可见画面，**仅**用 `@名称` 引用角色/场景/道具——**不要**写外貌、服装、场景细节（这些由参考图提供）。
   - 每 unit 所有 Shot `duration` 之和即该 unit `duration_seconds`。

c. **references**：`{{type, name}}` 列表，顺序决定 `[图N]` 编号。
   - `type` 取值 character / scene / prop。
   - `name` 必须来自以下候选，否则会校验失败：
     - character: {", ".join(character_names) or "（无）"}
     - scene: {", ".join(scene_names) or "（无）"}
     - prop: {", ".join(prop_names) or "（无）"}
   - 每个 shot `text` 中出现的 `@名称` 都必须在 references 里注册一次。
   - **references 数量不得超过 {max_refs}**（模型上限），超出时把次要角色合并到背景描述。

d. **duration_seconds**：所有 shot `duration` 之和；不要手动覆盖。

e. **transition_to_next**：默认 "cut"，如明显切换时间/空间可用 "fade" / "dissolve"。

f. **note**：可选，人类备注；通常留空。

4. 整集 `ReferenceVideoScript` 顶层字段：
   - `episode`、`title`、`summary`、`novel.title` / `novel.chapter` 必填。
   - `content_mode` 固定 "reference_video"。
   - `duration_seconds` 可先写 0，由 caller 重算。

5. 关键约束复核：
   - 每 unit 最多 **4 个 shot**；所有 shot 时长之和应贴近 Step 1 预估。
   - `@名称` 只能引用在 characters / scenes / props 三张表中已注册的名字。
   - 禁止在 shot `text` 中描写角色外貌、服装、场景细节（参考图负责视觉一致性）。
   - 禁止发明新的资产名称。

请根据 <step1_units> 逐 unit 产出。
"""
