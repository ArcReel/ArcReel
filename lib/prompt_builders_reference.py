"""参考生视频 Prompt 构建器。

设计原则与 prompt_builders_script.py 一致：
- 不重复 schema 已声明的枚举（type 等）；让 response_schema 直接约束。
- 多选枚举字段不在 prompt 里写"如何选"判据；让模型按画面内容自行决定。
- 字段说明给指导和示例，不堆"必须 / 禁止"清单。
- 跨 backend 时长 / references 上限通过参数显式注入，不在文本里硬编码秒数。

两级 prompt 注入的引用语法规范取自同一份常量
（``lib.reference_video.writing_syntax.writing_syntax_spec``）：LLM 产出与人在编辑器写的
是同一种格式，语法只能有一份措辞，本模块不复写。
"""

from __future__ import annotations

from lib.prompt_rules.episode_target_duration import render_episode_target_duration_rule
from lib.reference_video.writing_syntax import writing_syntax_spec
from lib.speech_rate import speech_rate_units_per_second
from lib.text_metrics import reading_unit_noun


def _format_asset_names(assets: dict | None) -> str:
    if not assets:
        return "（暂无）"
    return "\n".join(
        f"- {name}: {meta.get('description', '') if isinstance(meta, dict) else ''}" for name, meta in assets.items()
    )


def _candidate_block(characters: dict, scenes: dict, props: dict) -> str:
    return (
        f"  - character: {', '.join(characters.keys()) or '（暂无）'}\n"
        f"  - scene: {', '.join(scenes.keys()) or '（暂无）'}\n"
        f"  - prop: {', '.join(props.keys()) or '（暂无）'}"
    )


def _format_outline_block(episode_outline: dict | None, next_episode_outline: dict | None) -> str:
    """把分集大纲渲染为 XML 块；两者皆空时返回空串（不留空标签）。

    与 drama script_plan 同源的做法：大纲是本集内容边界的既定契约，拆分 unit 时先知道本集要讲到
    哪里、下集从哪接，才不会把跨集情节吞进来或提前抖包袱。
    """
    blocks: list[str] = []
    for tag, outline in (("episode_outline", episode_outline), ("next_episode_outline", next_episode_outline)):
        if not isinstance(outline, dict) or not outline:
            continue
        lines: list[str] = []
        title = outline.get("title")
        if isinstance(title, str) and title.strip():
            lines.append(f"标题：{title.strip()}")
        hook = outline.get("hook")
        if isinstance(hook, str) and hook.strip():
            lines.append(f"钩子：{hook.strip()}")
        beats = outline.get("story_beats")
        if isinstance(beats, list):
            lines.extend(f"- {beat}" for beat in beats if isinstance(beat, str) and beat.strip())
        teaser = outline.get("next_episode_teaser")
        if isinstance(teaser, str) and teaser.strip():
            lines.append(f"下集预告：{teaser.strip()}")
        if lines:
            blocks.append(f"<{tag}>\n" + "\n".join(lines) + f"\n</{tag}>")
    return "\n\n".join(blocks) + "\n\n" if blocks else ""


def build_reference_units_split_prompt(
    *,
    novel_text: str,
    project_overview: dict,
    characters: dict,
    scenes: dict,
    props: dict,
    supported_durations: list[int],
    reference_supported_durations: list[int] | None = None,
    text_supported_durations: list[int] | None = None,
    max_duration: int,
    max_reference_images: int | None,
    default_duration: int | None,
    episode: int,
    target_language: str = "中文",
    source_language: str | None = None,
    speech_rate_override: float | None = None,
    episode_target_duration: int | None = None,
    episode_outline: dict | None = None,
    next_episode_outline: dict | None = None,
) -> str:
    """Step-1 video_unit 拆分 prompt：源文 → 扁平 unit 表（时长 + 原文锚 + 引用语法正文）。

    由 ``generate_script_plan`` 的参考生视频变体消费。script_plan 定的是**结构与内容契约**——
    unit 边界、时长（即计费单位）、台词落位、核心资产指认；提示词编写（景别 / 构图 / 运镜）
    留给 prompt_authoring。产出受 response_schema（``build_reference_units_script_plan_model``，unit 时长
    枚举硬约束）约束；unit_id / utterances 全部机器派生，不进 LLM 输出。

    Args:
        supported_durations: unit 允许的时长取值集合（秒），即两种引用状态下档位的并集，
            与 response_schema 的枚举同集合。
        reference_supported_durations: 带 ``@`` 引用的 unit 适用的档位。
        text_supported_durations: 不带 ``@`` 引用的 unit 适用的档位。两套档位相同（或任一为
            None）时不写入该联动约束——多数型号不声明「参考图↔时长」，多写一条无效约束只挤占
            注意力；两套不同时**两套都写**，不假定谁包含谁。
        max_duration: 单次视频生成的时长上限（秒），即档位最大值。
        max_reference_images: 单 unit 参考图上限；None 时不写入硬性数量约束。
        default_duration: 用户项目偏好的默认秒数；须为 supported_durations 成员或 None。
        source_language: 项目源文语言码（zh / en / vi 或 None），供台词口播时长下界取语速。
        speech_rate_override: 项目级语速覆盖（阅读单位 / 秒，由调用方经
            ``project_speech_rate_override`` 解析）；None 即无覆盖、回退语言默认。
        episode_target_duration: 项目级「单集目标时长」偏好（秒，由调用方经
            ``project_episode_target_duration`` 解析）。设了目标时打包效率按该目标组织，
            未设（None）时按单次生成上限组织，与不设该项的项目行为一致。
        episode_outline / next_episode_outline: 分集账本大纲（``episode_outline_context``
            的返回值），用于约束本集内容边界；为 None 时不插入该段。
    """
    normalized_durations = sorted({int(d) for d in supported_durations})
    if not normalized_durations:
        raise ValueError("supported_durations 不能为空：必须提供模型支持的秒数集合")
    if default_duration is not None and int(default_duration) not in normalized_durations:
        raise ValueError(f"default_duration={default_duration} 不在 supported_durations={normalized_durations} 内")
    normalized_reference_durations = sorted({int(d) for d in reference_supported_durations or []})
    normalized_text_durations = sorted({int(d) for d in text_supported_durations or []})
    for name, tier in (
        ("reference_supported_durations", normalized_reference_durations),
        ("text_supported_durations", normalized_text_durations),
    ):
        if not set(tier) <= set(normalized_durations):
            raise ValueError(f"{name}={tier} 不是 supported_durations={normalized_durations} 的子集")

    durations_str = ", ".join(str(d) for d in normalized_durations)
    # 「参考图↔时长」联动约束只在型号真的声明它、且两套档位不同时才写进 prompt：多数型号两者
    # 等价，多写一条无效约束只会挤占模型注意力。两套不同时两套都写全，不写成「并集 + 带图收窄」
    # ——`constrain_durations` 在交集为空时回退到未收窄候选，带图那套反而可能更宽，此时无引用
    # unit 才是被收窄的一方，只讲带图会让它照并集取到自己申请不到的档位。
    # 约束的落地判定在工具侧按机械派生的 references 逐 unit 做，prompt 这段是教学，不是唯一防线。
    tiers_differ = (
        bool(normalized_reference_durations and normalized_text_durations)
        and normalized_reference_durations != normalized_text_durations
    )
    reference_rule = (
        "\n     本型号下该档位还随「有无参考图」分两套，按该 unit **画面描述里有没有 `@` 资产引用**"
        "取用（台词记号 `@[角色]{台词}` 的说话人不计入——它不生成参考图，只驱动音色声明）："
        f"带 `@` 引用取（{', '.join(str(d) for d in normalized_reference_durations)}），"
        f"不带取（{', '.join(str(d) for d in normalized_text_durations)}）。"
        "两者取其一：要么改取该 unit 引用状态对应档位内的值，要么调整引用——"
        "把次要资产融入描述文字、不用 `@` 引用，从而适用不带引用的那套档位。"
        if tiers_differ
        else ""
    )
    # 默认偏好只是第 3 优先级、在第 1 条硬约束内做优化，但两套档位不同时它可能只对其中一种
    # 引用状态合法（Veo 3.1 在 720p 下带图仅 8s，而项目默认可能是 4s）。此时点明它的适用范围，
    # 免得模型把「默认 4 秒」套到带引用的 unit 上、拆出执行期申请不到的时长。
    default_scope = ""
    if default_duration is not None and tiers_differ:
        in_reference = int(default_duration) in normalized_reference_durations
        in_text = int(default_duration) in normalized_text_durations
        if in_reference != in_text:
            applies = "带 `@` 引用的" if in_reference else "不带 `@` 引用的"
            default_scope = f"（该默认值只落在{applies} unit 的档位内，另一种状态的 unit 按上面的硬约束取值）"
    default_rule = (
        f"unit 默认取 {default_duration} 秒{default_scope}，"
        "叙事需要更长时可取更长档（偏好可被内容需要覆盖，硬约束不可）"
        if default_duration is not None
        else "按叙事需要从档位中取值，不强制默认值"
    )
    # 上限按机械派生的 references 计数，故这里与 `reference_rule` 同口径点明说话人不计入：
    # 少这句会让模型把只出现在台词记号里的角色也算进配额，凭空压掉真正要进画面的资产。
    max_refs_rule = (
        f"\n- **references 上限**：一个 unit 的**画面描述里** `@` 引用的资产名（去重后）不超过 "
        f"{max_reference_images} 个（台词记号 `@[角色]{{台词}}` 的说话人不计入——它不生成参考图）；"
        "超出时把次要角色融入背景描述（不用 `@` 引用），不要压缩主体资产。"
        if max_reference_images is not None
        else ""
    )
    # 打包效率：未设单集目标时长时按单次生成上限组织（拆得越满，同样内容的生成调用越少）；
    # 设了目标时改为在目标内组织——无条件贴近上限会把体量本就不大的一集拆成一串满档 unit，
    # 正是「单集目标时长」要收敛的那个行为。
    episode_target_rule = render_episode_target_duration_rule(episode_target_duration)
    if episode_target_rule:
        packing_rule = (
            f"在 1-3 之内组织正文内容，按本集目标时长打包——{episode_target_rule}；"
            f"单个 unit 在目标之内可长可短，不必贴近单次上限 {max_duration} 秒，也不要默认选最短 / 保守值。"
        )
    else:
        packing_rule = f"在 1-3 之内组织正文内容，使 unit 时长贴近 {max_duration} 秒；不要默认选最短 / 保守值。"
    # 语速从 lib.speech_rate 单一真相源取（项目级覆盖优先、否则按 source_language 的语言默认）、
    # 不写死；与工具侧的台词超载后校验同一套换算，prompt 给的下界和校验器判的上界因此是同一把尺。
    source_language = source_language if isinstance(source_language, str) else None
    speech_rate = speech_rate_units_per_second(source_language, speech_rate_override)
    unit_label = reading_unit_noun(source_language)

    return f"""# 角色与任务

你是一位视频单元架构师，本任务是把源文拆分为适配多模态参考生视频模型的 video_unit 表（script_plan 脚本规划）。
每个 video_unit 对应**一次视频生成调用**，正文是一段连续的画面描述，一次生成完整覆盖它。
本阶段定的是**结构与内容契约**：unit 边界、时长（时长即计费单位）、台词落位、核心资产指认——用户会逐 unit 审阅确认这份契约。
视觉编排（景别 / 构图 / 运镜扩写）由后续 prompt_authoring 以你的拆分为基底生成，本阶段不写。

**输出语言**：所有字符串值必须使用 {target_language}；JSON 键名保持英文。
例外（逐字保留、不翻译）：`@[名称]` 中的资产名须逐字等于下方候选表中的登记名；`source_text` 须逐字复制小说原文。
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

{_format_outline_block(episode_outline, next_episode_outline)}# 拆分规则

当前正在生成第 {episode} 集。请覆盖全部源文情节，按叙事顺序逐 unit 产出。

- **unit 边界**：每个 unit 对应一个连贯的视频生成片段——同一时间、同一地点、主体动作连续；
  时间 / 空间 / 情节重大切换点开新 unit。
- **source_text**：该 unit 所依据的小说原文片段，**逐字复制**（可截断首尾，但中间不得删字、改写、翻译或概括）。
  它是追溯锚，用于把生成结果对回原文；不逐字复制会被机械校验拒绝。
- **时长决策序**（自上而下，高优先级是硬边界，低优先级在其内做优化）：
  1. 硬约束：`duration_seconds` 是 unit 时长（一次生成调用一个时长），必须取支持档位（{durations_str}）中的值。
     叙事需要的时长放不下时，把该 unit 按叙事顺序重拆为多个 unit，**不得违约时长**。{reference_rule}
  2. 台词下界：先估算该 unit 全部台词与画外音念完约需的秒数（口播语速约 {speech_rate:g} {unit_label}/秒），
     取**不低于**这个秒数的档位。这是单向下界——台词永不压进念不完的短档；无台词的 unit 没有此下界。
     台词量超过最长档（{max_duration} 秒）时把该 unit 拆开，不要把台词硬塞进一个 unit。
  3. 默认偏好：{default_rule}。
  4. 打包效率：{packing_rule}{max_refs_rule}

# 正文书写语法

{writing_syntax_spec()}

# 本阶段的正文写作指引

- 画面描述聚焦当下瞬间的**可见动作**：谁做了什么、物件互动、环境动态；动词描述物理可观察动作
  （伸手 / 转身 / 推门 / 投向），避免「陷入 / 回忆 / 意识到 / 决定」等内心动词。
- 资产名必须逐字取自下列候选，不要发明候选之外的名称：
{_candidate_block(characters, scenes, props)}
- 原文里的人物对白写成台词记号（`@[角色]{{台词}}`），旁白 / 心声写成画外音记号（`{{台词}}`），逐字保留原文措辞；
  台词是内容契约的一部分，prompt_authoring 不会再改动它。
- 每个 unit 的正文都要 `@` 引用它发生地的场景资产；地点切换到新 unit 时换成新地点的场景，
  地点不变的连续 unit 逐条重复引用同一个场景（候选表里没有匹配的场景时才用文字写地点）。
- 本阶段不写景别 / 构图 / 运镜（prompt_authoring 补），把叙事内容与动作过程写清楚即可。
"""


def render_reference_units_for_prompt_authoring(units: list[dict]) -> str:
    """把 script_plan units 渲染为 prompt_authoring prompt 的输入文本。

    机械渲染、无 LLM 参与：按 script_plan 的落盘顺序逐 unit 输出序号 + 时长 + 正文。
    prompt_authoring 以此为唯一基底做视觉扩写（见 ADR 0041）；``unit_id`` 不进渲染——它由序号机械
    派生，prompt_authoring 不写 id 就没有 id 漂移可校验。
    """
    blocks: list[str] = []
    for index, unit in enumerate(units, start=1):
        duration = int(unit.get("duration_seconds") or 0)
        body = str(unit.get("text") or "")
        blocks.append(f"#### unit {index}（时长 {duration}s）\n{body}")
    return "\n\n".join(blocks)


def build_reference_video_prompt(
    *,
    project_overview: dict,
    style: str,
    style_description: str,
    characters: dict,
    scenes: dict,
    props: dict,
    script_plan_units: list[dict],
    max_refs: int | None,
    episode: int,
    aspect_ratio: str = "9:16",
    target_language: str = "中文",
) -> str:
    """构建参考生视频 prompt_authoring（提示词编写）的 LLM Prompt。

    prompt_authoring 只做一件事：把 script_plan 每个 unit 的正文按同一份书写语法扩写出视觉层，**保结构**——
    unit 数与顺序不变、台词逐字不变；时长不进输出（script_plan 定稿、机械沿用）。

    Args:
        project_overview: 项目概述（synopsis, genre, theme, world_setting）。
        style / style_description: 视觉风格标签与描述。
        characters / scenes / props: 三类已注册资产字典（用于候选列表）。
        script_plan_units: 结构化 script_plan units（``script_plan_reference_units.json`` 经校验后的 dict 列表），
            由 ``render_reference_units_for_prompt_authoring`` 机械渲染进 prompt。
        max_refs: 当前视频模型支持的最大参考图数；为 None 时不写入硬性数量约束。
    """
    max_refs_line = (
        f"\n- 单个 unit 的**画面描述里** `@` 引用的资产名（去重后）不超过 {max_refs} 个（模型上限）；"
        "台词记号 `@[角色]{台词}` 的说话人不计入——它不生成参考图，只驱动音色声明。"
        "超出时把次要角色合并到背景描述，不用 `@` 引用。"
        if max_refs is not None
        else ""
    )

    return f"""# 角色与任务

你是一位资深的短视频分镜编剧，本任务是为采用「参考生视频」的第 {episode} 集做**提示词编写**。
下方 script_plan_units 表给出的是已经用户确认的内容契约；你的任务是逐 unit 把正文扩写出景别 / 构图 / 运镜与画面细节。

**输出语言**：所有字符串值必须使用 {target_language}；JSON 键名保持英文。
**结构约束**：字段 / 必填项由 response_schema 强制；本提示只解释**如何写好正文**。

# 保结构要求（违反即整份产出被拒）

- `units` 数组与 script_plan_units **等长、同序**：不合并、不拆分、不增删 unit。
- 每个 unit 的**台词与画外音逐字保留**：不改词、不增删、不重排、不换说话人。
  台词配不上你想要的画面时，请按台词写画面——**不要**改台词。
- 正文里新出现的 `@[名称]` 必须是候选表中的登记名（script_plan 没引用过的资产也可以引用，但必须已登记）。{max_refs_line}

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

<script_plan_units>
{render_reference_units_for_prompt_authoring(script_plan_units)}
</script_plan_units>

# 正文书写语法

{writing_syntax_spec()}

# 提示词编写写作指引

正文将直接驱动该 unit 的视频生成，按「景别 → 构图 → 运镜 → 画面内容」四要素依次组织，写足画面信息、宁详勿略：

- 景别：大全景 / 全景 / 中景 / 近景 / 特写，及拍摄角度（俯拍 / 仰拍 / 平视）。
- 构图：主体在画面中的位置、前景与背景的关系（如中心构图、对角线构图、以公路 / 廊柱作引导线）。
- 运镜：机位与镜头运动（固定机位 / 跟随 / 推近 / 拉远 / 摇移），含焦点主体的变更。
- 画面内容：占篇幅大头——unit 时长内发生的全部可见运动：每个出场主体各自的动作链（肢体 / 手势 / 神态过渡）、
  物件互动、背景与环境动态（人群、天气、衣摆、光影移动），可带运动质感（如动态模糊），末尾用一句点明氛围基调。
  动作量与 unit 时长匹配：时长越长，动作段数随之递增。
- 角色 / 场景 / 道具仅用 `@[名称]` 引用，候选：
{_candidate_block(characters, scenes, props)}
  外貌、服装、场景陈设等静态外观由参考图承担，**不要**在文本里描写；动作、姿态、互动与环境动态则写得越具体越好。
  动词应描述物理可观察动作（伸手 / 转身 / 摩挲 / 投向 / 收紧），避免「陷入 / 回忆 / 意识到 / 决定」等内心动词。
- 正例：「景别：中景，轻微仰拍。构图：@[角色A] 居画面中心，@[场景A] 的窗棂与案几为前景。运镜：固定机位，缓慢推近。
  画面内容：@[角色A] 在 @[场景A] 中缓步走向窗前，抬手推开木窗，衣摆随穿堂风轻扬。@[角色A]{{这风比去年冷。}}随后低头凝视手中的 @[道具A]，
  指尖缓缓收紧，呼吸放缓，目光从 @[道具A] 缓慢抬起投向窗外；烛焰随风明灭，光影在面部缓慢移动，渲染压抑而克制的氛围。」
- 反例（过短）：「@[角色A] 站在 @[场景A] 里。」——没有景别 / 构图 / 运镜，也没有动作过程与环境动态，生成的视频会近乎静止。
- 反例（写外貌）：「身穿某色服装的角色A 站在某色场景A 前」——外貌 / 服装 / 颜色应由参考图承担，且未用 `@[名称]` 引用。
- 场景引用逐 unit 保留：script_plan 正文里该 unit 引用的场景资产必须在展开后的正文中照常 `@` 引用，
  不因为上一个 unit 已经引用过同一场景就省略；script_plan 未引用场景而候选表里有匹配该地点的场景时，补上引用。

`title` 给本集拟一个简短标题。请按 script_plan_units 顺序逐 unit 产出。
"""
