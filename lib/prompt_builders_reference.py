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


def build_reference_units_split_prompt(
    *,
    novel_text: str,
    project_overview: dict,
    characters: dict,
    scenes: dict,
    props: dict,
    supported_durations: list[int],
    max_duration: int,
    max_reference_images: int | None,
    default_duration: int | None,
    episode: int,
    target_language: str = "中文",
) -> str:
    """Step-1 video_unit 拆分 prompt：源文 → 结构化 unit 表（shots 叙事文本 + 时长）。

    由 ``split_reference_video_units`` MCP tool 消费。输出受 response_schema
    （``build_reference_units_step1_model``，单 shot 时长枚举硬约束）约束为结构化 JSON；
    unit 总时长上限与 references 上限依赖运行时能力值，在 prompt 给出指引、由工具后校验。
    references 不进 LLM 输出，由工具从 shot 文本的 ``@[名称]`` 引用机械派生。

    Args:
        supported_durations: 单 shot 允许的时长取值集合（秒）。
        max_duration: unit 总时长上限（秒），即单次视频生成上限。
        max_reference_images: 单 unit 参考图上限；None 时不写入硬性数量约束。
        default_duration: 用户项目偏好的默认单 shot 秒数；须为 supported_durations 成员或 None。
    """
    normalized_durations = sorted({int(d) for d in supported_durations})
    if not normalized_durations:
        raise ValueError("supported_durations 不能为空：必须提供模型支持的秒数集合")
    if default_duration is not None and int(default_duration) not in normalized_durations:
        raise ValueError(f"default_duration={default_duration} 不在 supported_durations={normalized_durations} 内")

    durations_str = ", ".join(str(d) for d in normalized_durations)
    default_rule = (
        f"单 shot 默认取 {default_duration} 秒，叙事需要更长时可取更长档（偏好可被内容需要覆盖，硬约束不可）"
        if default_duration is not None
        else "按叙事需要从档位中取值，不强制默认值"
    )
    max_refs_rule = (
        f"\n- **references 上限**：一个 unit 内 `@` 引用的资产名（去重后）不超过 {max_reference_images} 个；"
        "超出时把次要角色融入背景描述（不用 `@` 引用），不要压缩主体资产。"
        if max_reference_images is not None
        else ""
    )
    character_names = list(characters.keys())
    scene_names = list(scenes.keys())
    prop_names = list(props.keys())

    return f"""# 角色与任务

你是一位参考生视频单元架构师，本任务是把源文拆分为适配多模态参考视频模型的 video_unit 表（step1 内容拆分）。
每个 video_unit 对应**一次视频生成调用**，含 1-4 个 shot；shot 表示镜头切换，但共享同一次生成。
视觉编排（景别 / 构图 / 运镜扩写）由后续 step2 以你的拆分为基底生成，本阶段只定叙事内容与时间结构。

**输出语言**：所有字符串值必须使用 {target_language}；JSON 键名保持英文。
例外（逐字保留、不翻译）：`@[名称]` 中的资产名须逐字等于下方候选表中的登记名。
**结构约束**：字段 / 枚举 / 必填项由 response_schema 强制；本提示只解释**如何写好每个字段的内容**。

# 上下文

<overview>
{project_overview.get("synopsis", "")}

题材：{project_overview.get("genre", "")}
主题：{project_overview.get("theme", "")}
世界观：{project_overview.get("world_setting", "")}
</overview>

<characters>
{_format_asset_names(characters)}
</characters>

<scenes>
{_format_asset_names(scenes)}
</scenes>

<props>
{_format_asset_names(props)}
</props>

## 小说原文

<novel>
{novel_text}
</novel>

# 拆分规则

当前正在生成第 {episode} 集。

- **unit 边界**：每个 unit 对应一个连贯的视频生成片段——同一时间、同一地点、主体动作连续；
  时间 / 空间 / 情节重大切换点开新 unit。
- **unit_id**：`E{episode}U{{两位序号}}` 格式（如 E{episode}U01），按顺序递增，不得用其他集号前缀。
- **时长决策序**（自上而下，高优先级是硬边界，低优先级在其内做优化）：
  1. 硬约束：单 shot 时长必须取支持档位（{durations_str}）中的值；unit 内所有 shot 时长之和不超过 {max_duration} 秒。
     叙事需要的总时长放不下时，把该 unit 按叙事顺序重拆为多个 unit，**不得违约时长**。
  2. 默认偏好：{default_rule}。
  3. 打包效率：在 1、2 之内组合 shot，使 unit 总时长贴近 {max_duration} 秒；不要默认选最短 / 保守值。{max_refs_rule}

# shot 文本写作指引

- 每 shot 的 `text` 聚焦当下瞬间的**可见动作**：谁做了什么、物件互动、环境动态；动词描述物理可观察动作
  （伸手 / 转身 / 推门 / 投向），避免「陷入 / 回忆 / 意识到 / 决定」等内心动词。
- 角色 / 场景 / 道具统一用 `@[名称]` 引用，名称必须逐字取自下列候选，不要发明候选之外的名称：
  - character: {", ".join(character_names) or "（暂无）"}
  - scene: {", ".join(scene_names) or "（暂无）"}
  - prop: {", ".join(prop_names) or "（暂无）"}
- **不要**描写外貌、服装、场景陈设、色调光影——静态外观由参考图承担；泛指群演（老人甲 / 村民若干）
  写入叙事文本即可，不用 `@` 引用、不占 references 名额。
- 本阶段不写景别 / 构图 / 运镜（step2 补），把叙事内容与动作过程写清楚即可。

请覆盖全部源文情节，按叙事顺序逐 unit 产出。
"""


def render_reference_units_for_step2(units: list[dict]) -> str:
    """把结构化 step1 units 渲染为 step2 prompt 的输入文本。

    机械渲染、无 LLM 参与：unit_id + 各 shot 时长与叙事文本 + references 表。
    step2 以此为唯一基底做视觉扩写（见 ADR 0041）。
    """
    blocks: list[str] = []
    for unit in units:
        shots = unit.get("shots") or []
        total = sum(int(s.get("duration") or 0) for s in shots if isinstance(s, dict))
        refs = unit.get("references") or []
        refs_line = ", ".join(f"{r.get('type')}:{r.get('name')}" for r in refs if isinstance(r, dict)) or "（无）"
        lines = [f"#### {unit.get('unit_id')}（预估总时长 {total}s）", f"references: {refs_line}"]
        for idx, shot in enumerate(shots, start=1):
            if not isinstance(shot, dict):
                continue
            duration = shot.get("duration")
            lines.append(f"Shot {idx} ({duration if duration is not None else 0}s): {shot.get('text', '')}")
        blocks.append("\n".join(lines))
    return "\n\n".join(blocks)


def build_reference_video_prompt(
    *,
    project_overview: dict,
    style: str,
    style_description: str,
    characters: dict,
    scenes: dict,
    props: dict,
    step1_units: list[dict],
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
        step1_units: 结构化 step1 units（``step1_reference_units.json`` 经校验后的 dict 列表），
            由 ``render_reference_units_for_step2`` 机械渲染进 prompt——step2 以其为唯一基底，
            不解析自由文本。
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
{render_reference_units_for_step2(step1_units)}
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
