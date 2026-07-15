"""参考生视频模式 Prompt 构建器。

设计原则与 prompt_builders_script.py 一致：
- 不重复 schema 已声明的枚举（type 等）；让 response_schema 直接约束。
- 多选枚举字段不在 prompt 里写"如何选"判据；让模型按画面内容自行决定。
- 字段说明给指导和示例，不堆"必须 / 禁止"清单。
- 跨 backend 时长 / references 上限通过参数显式注入，不在文本里硬编码秒数。
"""

from __future__ import annotations


def _format_asset_names(assets: dict | None) -> str:
    if not assets:
        return "（暂无）"
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
    max_refs: int | None,
    episode: int,
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
        max_refs: 当前视频模型支持的最大参考图数；为 None 时不写入硬性数量约束。
        max_duration: 当前视频模型的单次生成时长上限（秒）。传入时 prompt 会显式
            给出时长目标（贴近 step1 预估、不默认选最短值）；上限本身由动态 schema 的
            duration 枚举硬约束，prompt 不复述。为 None 时不插入该段。
    """
    character_names = list(characters.keys())
    scene_names = list(scenes.keys())
    prop_names = list(props.keys())

    durations_desc = "/".join(str(d) for d in supported_durations) + "s"
    max_refs_line = (
        f"\n    - **references 数量不超过 {max_refs}**（模型上限）；超出时把次要角色合并到背景描述。"
        if max_refs is not None
        else ""
    )
    duration_guide_line = (
        f"\n    - `duration`：unit 内所有 Shot `duration` 之和应贴近 step1 预估时长；预估缺失或超过 "
        f"{max_duration} 秒（当前模型上限）时，以 {max_duration} 秒为目标。不要默认选最短值。"
        if max_duration is not None
        else ""
    )

    return f"""# 角色与任务

你是一位资深的短视频分镜编剧，本任务是为「参考生视频」模式产出 JSON 剧本。
你的任务：基于下方 step1_units 表，按 schema 产出 ReferenceVideoScript。

**输出语言**：所有字符串值必须使用 {target_language}；JSON 键名 / 枚举值保持英文。
**结构约束**：字段 / 枚举 / 必填项由 response_schema 强制；本提示只解释**如何写好每个字段的内容**。

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

<episode_constraints>
当前正在生成第 {episode} 集。本集所有 unit_id 沿用 step1_units 表中的编号，必须严格使用 `E{episode}U{{两位序号}}` 格式（如 E{episode}U01、E{episode}U02），不得使用其他集号前缀。
若 step1_units 表里出现非 `E{episode}` 前缀（如残留自其他集号的编号），视为脏数据，请按当前集号 `E{episode}` 重写。
</episode_constraints>

# 字段写作指引

对每个 video_unit，按下列要求填写字段：

a. **shots**：{duration_guide_line}
    - `text`：这段文字将直接驱动该镜头的视频生成（语言遵循上方"输出语言"约束）。按「景别 → 构图 → 运镜 → 画面内容」四要素依次组织，写足画面信息、宁详勿略：
        - 景别：大全景 / 全景 / 中景 / 近景 / 特写，及拍摄角度（俯拍 / 仰拍 / 平视）。
        - 构图：主体在画面中的位置、前景与背景的关系（如中心构图、对角线构图、以公路 / 廊柱作引导线）。
        - 运镜：机位与镜头运动（固定机位 / 跟随 / 推近 / 拉远 / 摇移），含镜头内焦点主体的变更。
        - 画面内容：占篇幅大头——镜头时长内发生的全部可见运动：每个出场主体各自的动作链（肢体 / 手势 / 神态过渡）、物件互动、背景与环境动态（人群、天气、衣摆、光影移动），可带运动质感（如动态模糊），末尾用一句点明氛围基调。动作量与 `duration` 匹配：短镜头完成一个连贯动作 + 一个细节互动，更长的镜头随时长递增动作段数。
        - 角色 / 场景 / 道具仅用 `@[名称]` 引用——外貌、服装、场景陈设等静态外观由参考图承担，**不要**在文本里描写；动作、姿态、互动与环境动态则写得越具体越好。动词应描述物理可观察动作（伸手 / 转身 / 摩挲 / 投向 / 收紧），避免「陷入 / 回忆 / 意识到 / 决定」等内心动词。
        - 正例：「景别：中景，轻微仰拍。构图：@[角色A] 居画面中心，@[场景A] 的窗棂与案几为前景。运镜：固定机位，缓慢推近。画面内容：@[角色A] 在 @[场景A] 中缓步走向窗前，抬手推开木窗，衣摆随穿堂风轻扬；随后低头凝视手中的 @[道具A]，指尖缓缓收紧，呼吸放缓，目光从 @[道具A] 缓慢抬起投向窗外；烛焰随风明灭，光影在面部缓慢移动，渲染压抑而克制的氛围。」
        - 反例（过短）：「@[角色A] 站在 @[场景A] 里。」——没有景别 / 构图 / 运镜，也没有动作过程与环境动态，生成的视频会近乎静止。
        - 反例（写外貌）：「身穿某色服装的角色A 站在某色场景A 前，手里紧握着某色道具A」——外貌 / 服装 / 颜色应由参考图承担，且未用 `@[名称]` 引用。

b. **references**：每个 shot `text` 中出现的 `@[名称]` 都要在 references 注册一次。
    - `name` 必须来自候选：
        - character: {", ".join(character_names) or "（暂无）"}
        - scene: {", ".join(scene_names) or "（暂无）"}
        - prop: {", ".join(prop_names) or "（暂无）"}{max_refs_line}

c. **duration_seconds**：请编排各 shot 时长，使其相加正好落在支持集合（{durations_desc}）内。

请按 step1_units 顺序逐 unit 产出。
"""
