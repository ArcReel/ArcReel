"""剧本生成 Prompt 构建器（drama / narration 两种 content_mode）。

设计原则：
- 不重复 schema 已声明的枚举（shot_type / camera_motion 等）；让 response_schema 直接约束。
- 多选枚举字段不在 prompt 里写"如何选"判据，避免把人的镜头审美灌给 LLM；
  让模型按画面内容自行决定。
- 不写无法被 LLM 自检的字数硬限制（"≤200 字"）；用示例隐性表达节奏。
- 字段说明给 1-2 个正例（必要时配一个反例），不堆"必须 / 禁止"清单。
- 节奏建议由 lib.prompt_rules.episode_pacing 注入，跨 subagent 与 builder 共享。
"""

from lib.prompt_rules import is_v2_enabled
from lib.prompt_rules.episode_pacing import render_pacing_section


def _format_names(items: dict) -> str:
    if not items:
        return "（暂无）"
    return "\n".join(f"- {name}" for name in items.keys())


def _format_duration_constraint(supported_durations: list[int], default_duration: int | None) -> str:
    """生成时长约束描述。连续整数集 ≥5 用区间表达，否则枚举。"""
    if not supported_durations:
        raise ValueError("supported_durations 不能为空：调用方必须提供 model 的合法时长列表")

    sorted_d = sorted(set(supported_durations))
    is_continuous = len(sorted_d) >= 5 and all(sorted_d[i] == sorted_d[i - 1] + 1 for i in range(1, len(sorted_d)))
    if is_continuous:
        body = f"{sorted_d[0]} 到 {sorted_d[-1]} 秒间整数任选"
    else:
        durations_str = ", ".join(str(d) for d in sorted_d)
        body = f"从 [{durations_str}] 秒中选择"

    if default_duration is not None:
        if default_duration not in sorted_d:
            raise ValueError(
                f"default_duration={default_duration} 不在 supported_durations={sorted_d} 内，"
                "调用方必须保证默认值合法（否则 prompt 会自相矛盾）"
            )
        return f"时长：{body}，默认 {default_duration} 秒"
    return f"时长：{body}，按内容节奏自行决定"


def _format_aspect_ratio_desc(aspect_ratio: str) -> str:
    if aspect_ratio == "9:16":
        return "竖屏构图"
    if aspect_ratio == "16:9":
        return "横屏构图"
    return f"{aspect_ratio} 构图"


def _format_outline_lines(outline: dict) -> str:
    """渲染分集大纲条目：故事节点 / 集尾钩子 / 下集预告语，缺失的行省略。"""
    lines: list[str] = []
    beats = outline.get("story_beats") or []
    if beats:
        lines.append("故事节点：")
        lines.extend(f"- {beat}" for beat in beats)
    if outline.get("hook"):
        lines.append(f"集尾钩子：{outline['hook']}")
    if outline.get("next_episode_teaser"):
        lines.append(f"下集预告语：{outline['next_episode_teaser']}")
    return "\n".join(lines)


# 钩子落地要求：集尾钩子与下集预告是分集规划的核心设计，必须体现在成片末场，
# 而不是只停留在规划文档里。仅在账本提供了钩子/预告时渲染。
_HOOK_LANDING_GUIDE = (
    "集尾钩子与下集预告不能只停留在大纲：末场（最后一个或几个分镜）的画面与对白"
    "必须实际呈现集尾钩子的戏剧内容，让悬念定格在画面上；有下集预告语时，"
    "用结尾画面或对白自然引出它，不要生硬插入「下集预告」字样的旁白。"
)


def _format_episode_outline_block(episode_outline: dict | None, next_episode_outline: dict | None) -> str:
    """渲染本集大纲 + 下集大纲两个上下文块；无规划数据时返回空串（prompt 不渲染该段）。"""
    parts: list[str] = []
    if episode_outline:
        title = episode_outline.get("title")
        title_line = f"本集标题：{title}\n" if title else ""
        parts.append(f"""<episode_outline>
本集大纲（分集规划设计，剧本改编应覆盖全部故事节点）：
{title_line}{_format_outline_lines(episode_outline)}
</episode_outline>""")
        if episode_outline.get("hook") or episode_outline.get("next_episode_teaser"):
            parts.append(_HOOK_LANDING_GUIDE)
    if next_episode_outline:
        title = next_episode_outline.get("title")
        title_line = f"下集标题：{title}\n" if title else ""
        parts.append(f"""<next_episode_outline>
下集大纲（仅用于设计本集结尾的衔接，不要把下集情节提前写进本集）：
{title_line}{_format_outline_lines(next_episode_outline)}
</next_episode_outline>""")
    if not parts:
        return ""
    return "\n\n".join(parts) + "\n\n"


# ---------------------------------------------------------------------------
# 字段写作指导（drama / narration 共用）
# ---------------------------------------------------------------------------

# image_prompt.scene 写作指导：原则 + 正反例。LLM 对示例的泛化优于对清单的执行。
# 好例用方括号小标注隐性传达"主体 / 环境 / 光线 / 氛围"四层覆盖。
_SCENE_WRITING_GUIDE = """用一段连贯的描述说明当前画面中真实可见的元素：角色姿态、面部可观察的状态、环境细节、可见的氛围信号（光线、雾、雨等）。聚焦"此刻这一帧"，不要混入过去/未来事件、抽象情绪词或镜头之外的元素。画面元素（材质、装束、道具质感、环境年代特征）须贴合上方 `<style>` 块定义的风格基调，避免与风格相冲的元素混入（例如赛博朋克风下不出现榻榻米，国风水墨下不出现霓虹屏）。
   好例：「[主体] 林清坐在窗边木桌前，左手撑着下巴，目光落在桌上一封拆开的信纸上。[环境] 桌面摊着信封与一只褪色的怀表。[光线] 半边脸笼在右侧落地窗逆光的蓝灰色阴影里。[氛围] 雨丝拍在木格窗棂，玻璃凝着细小水珠。」
   反例（跑偏）：「林清陷入了多年前那个绝望的雨夜，画面基调：忧郁。光影设定：冷调。」
   反例（过短）：「林清坐在窗边发呆。」——缺少环境元素、光线方向、氛围细节，至少应覆盖主体 / 环境 / 光线 / 氛围中三层。
   反例里这类词族也要避免：陷入 / 回忆 / 思绪 / 意识到 / 画外音 / BGM / 精致 / 震撼。"""

# video_prompt.action 写作指导：动态优先 + 正反例。
# 好例用方括号小标注隐性传达"主体动作 / 物件互动 / 环境动态"三层。
_ACTION_WRITING_GUIDE = """用一段描述说明该时长内主体的连贯动作（肢体动作、手势、表情过渡），可包含必要的环境互动（衣摆、尘埃、推门带起的气流等）。让画面"活"起来，但不要堆叠不可能在单镜头内完成的动作或蒙太奇切换。动词应描述物理可观察动作（伸手 / 转身 / 摩挲 / 投向 / 收紧），避免内心动词。动作幅度应与该 segment 的 duration 匹配：5 秒级镜头通常完成一个连贯动作 + 一个细节互动；8 秒级可承载一次动作过渡（如「抬头—对视—开口」），不要把三组以上独立动作塞进同一 action。
   好例：「[主体动作] 林清缓缓抬起头，眼角微微收紧。[物件互动] 手指无意识地摩挲信纸边缘。[环境动态] 窗外雨势渐大，桌面投下的雨痕影子在缓慢移动。」
   反例：「林清像蝴蝶般飞舞，思绪在过去与现在之间快速切换。」
   反例里这类词族也要避免：思绪飞舞 / 回忆翻涌 / 突然意识到 / 决心 / 仿佛 / 像蝴蝶般。"""

_LIGHTING_WRITING_GUIDE = (
    "描述具体的光源、方向、色温（如「左侧窗户透入的暖黄色晨光（约 3500K）」「头顶单点冷白色的吊灯」）。"
    "可附加摄影质感术语（如「浅景深」「逆光剪影」「丁达尔光柱」「轮廓光勾边」「35mm 胶片颗粒感」），"
    "让画面具备可观察的镜头语言而非抽象修辞；避免「光影神秘」「氛围唯美」这类抽象词。"
)
_AMBIANCE_WRITING_GUIDE = "描述可观察的环境效果（如「薄雾弥漫」「尘埃在光柱里翻飞」），避免抽象情绪词。"
_AMBIANCE_AUDIO_WRITING_GUIDE = (
    "只描写画内音（diegetic sound）：环境声、脚步、物体声响。不要写 BGM、配乐、画外音、旁白。"
)


# ---------------------------------------------------------------------------
# source_kind=screenplay 分支文案（提取优先：台词/画外音逐字保真，只补视觉层）
#
# 默认 source_kind="novel" 走「改编/创作」原文案；screenplay 翻面为「提取/逐字透传」。
# 逐字只锚「可听见的内容」（台词文字 + 画外音文字）；排版/标签、运镜舞台提示、视觉描述、
# 泛指群演由 LLM 裁量转写或剥离。下方常量按 source_kind 在两段 builder 间共享。
# ---------------------------------------------------------------------------

# step2（build_drama_prompt）开篇角色定位
_DRAMA_ROLE_NOVEL = (
    "你是一位资深的短剧分镜编剧，精通把改编后的剧本场景表转写为可直接驱动 AI 图像 / 视频生成的结构化分镜。"
)
_DRAMA_ROLE_SCREENPLAY = (
    "你是一位资深的短剧分镜编剧。下方场景表来自作者已写好的成品剧本，你的职责是**转写而非再创作**："
    "把作者写下的台词与画外音逐字搬进结构化字段，只补剧本没写的视觉生产层（image_prompt / video_prompt）。"
)

# step2 video_prompt.dialogue 字段指引
_DRAMA_DIALOGUE_GUIDE_NOVEL = "包含分镜中角色对话；speaker 必须出现在 characters_in_scene。"
_DRAMA_DIALOGUE_GUIDE_SCREENPLAY = (
    "把场景描述里作者写下的台词**逐字照搬**（不改写、不润色、不删减、不补写）：line 填台词原文、"
    "speaker 填原文说话人。命名角色的 speaker 应来自 characters_in_scene；路人群演（如「老人甲」「村民若干」）"
    "照填其原文称呼即可，speaker 可以不在 characters_in_scene 中。分镜无台词则留空数组。"
)

# step2 voiceover 字段指引（DramaScene 一等字段，与 dialogue 平级）
_DRAMA_VOICEOVER_GUIDE_NOVEL = "本项目源自小说、无画外音轨，voiceover 一律留空数组 []。"
_DRAMA_VOICEOVER_GUIDE_SCREENPLAY = (
    "把场景描述里标注的画外音 / 旁白原文**逐字填入**（不改写、不删减）：每段一个数组元素，按出现顺序排列；"
    "分镜无画外音则留空数组 []。画外音无 speaker，不要塞进 dialogue。"
)

# step2 收尾创作目标
_DRAMA_GOAL_NOVEL = "输出可直接驱动 AI 生成的、视觉一致、节奏紧凑的分镜剧本。忠于原创设定、保留戏剧张力。"
_DRAMA_GOAL_SCREENPLAY = (
    "输出可直接驱动 AI 生成的分镜剧本：台词与画外音忠实于作者原文一字不改，"
    "视觉层（image_prompt / video_prompt）由你补全，保留剧本的戏剧张力。"
)

# step1（build_normalize_prompt）开篇任务句
_NORMALIZE_TASK_NOVEL = "你的任务是将小说原文改编为结构化的分镜场景表（Markdown 格式），用于后续 AI 视频生成。"
_NORMALIZE_TASK_SCREENPLAY = (
    "你的任务是从作者已写好的剧本中**提取**结构化的分镜场景表（Markdown 格式），"
    "逐字保留台词与画外音，用于后续 AI 视频生成。这是成品剧本、不是待加工的素材——只做提取、不做再创作。"
)

# step1 场景描述列的填写规则
_NORMALIZE_SCENE_RULE_NOVEL = "- 场景描述：改编后的剧本化描述，包含角色动作、对话、环境，适合视觉化呈现"
_NORMALIZE_SCENE_RULE_SCREENPLAY = """- 场景描述：逐字保留「可听见的内容」，原样搬进本列——
  - 角色台词照搬为 `角色名："台词原文"`，一字不改（不改写、不润色、不删减、不翻译）；路人群演（如「老人甲」）也保留其台词
  - 画外音 / 旁白照搬为 `【画外音】：原文`，一字不改
  - 运镜、景别、舞台提示（如「航拍，全景」「压低声音」）转写为画面视觉描述，不要写进台词
  - 排版符号（markdown、△、各类标签、表格、emoji）一律剥离，只留干净文本"""

# step1 segment_break 规则
_NORMALIZE_BREAK_RULE_NOVEL = '- segment_break：场景切换点标记"是"，同一连续场景标"否"'
_NORMALIZE_BREAK_RULE_SCREENPLAY = (
    "- segment_break：沿用剧本自带的场次/场景切换——场次变更（地点 / 时间 / 场景切换）标「是」，"
    "同一场次内标「否」；不要重新切碎作者的场次"
)


# ---------------------------------------------------------------------------
# Builder
# ---------------------------------------------------------------------------


def build_narration_prompt(
    project_overview: dict,
    style: str,
    style_description: str,
    characters: dict,
    scenes: dict,
    props: dict,
    segments_md: str,
    supported_durations: list[int],
    episode: int,
    default_duration: int | None = None,
    aspect_ratio: str = "9:16",
    target_language: str = "中文",
) -> str:
    """构建说书模式的剧本生成 prompt。"""
    character_names = list(characters.keys())
    scene_names = list(scenes.keys())
    prop_names = list(props.keys())
    pacing_block = (render_pacing_section("narration") + "\n\n") if is_v2_enabled() else ""

    return f"""# 角色与任务

你是一位资深的短视频分镜编剧，专精把小说片段改写为可直接驱动 AI 图像 / 视频生成的结构化分镜剧本。
你的任务：基于下方"小说片段拆分表"，逐条产出符合 schema 的 JSON 剧本。

**输出语言**：所有字符串值必须使用 {target_language}；JSON 键名 / 枚举值保持英文。
**结构约束**：字段 / 枚举 / 必填项由 response_schema 强制；本提示只解释**如何写好每个字段的内容**。

{pacing_block}# 上下文

<overview>
{project_overview.get("synopsis", "")}

题材：{project_overview.get("genre", "")}
主题：{project_overview.get("theme", "")}
世界观：{project_overview.get("world_setting", "")}
</overview>

<style>
风格：{style}
描述：{style_description}
画面比例：{aspect_ratio}（{_format_aspect_ratio_desc(aspect_ratio)}）
</style>

<characters>
{_format_names(characters)}
</characters>

<scenes>
{_format_names(scenes)}
</scenes>

<props>
{_format_names(props)}
</props>

<segments>
{segments_md}
</segments>

segments 表每行是一个待生成的片段，包含：片段 ID（E{episode}S{{序号}}，当前为第 {episode} 集）、小说原文、{_format_duration_constraint(supported_durations, default_duration)}、是否含对话、是否为 segment_break。

<episode_constraints>
当前正在生成第 {episode} 集。本集所有 segment_id 必须严格使用 `E{episode}S{{两位序号}}` 格式（如 E{episode}S01、E{episode}S02），不得使用其他集号前缀。
若 segments 表里出现非 `E{episode}` 前缀（如 E1S..），视为脏数据，请按当前集号 `E{episode}` 重写。
</episode_constraints>

# 字段写作指引

对每个片段，按下列章节填写字段。

## 基础字段

- **novel_text**：原样复制小说原文，不修改、不删改标点。
- **characters_in_segment** / **scenes** / **props**：仅列出此片段画面或对话中实际出现的资产。
  - 候选 characters：[{", ".join(character_names) or "（无）"}]
  - 候选 scenes：[{", ".join(scene_names) or "（无）"}]
  - 候选 props：[{", ".join(prop_names) or "（无）"}]
  - 不要发明候选之外的名称。
- **segment_break** / **duration_seconds**：与 segments 表保持一致。

## 图片提示词（image_prompt）——切换到「摄影师」视角

- **image_prompt.scene**：{_SCENE_WRITING_GUIDE}
- **image_prompt.composition.shot_type**：从枚举中按画面内容选择，不强加倾向。
- **image_prompt.composition.lighting**：{_LIGHTING_WRITING_GUIDE}
- **image_prompt.composition.ambiance**：{_AMBIANCE_WRITING_GUIDE}

## 视频提示词（video_prompt）——切换到「动作设计师」视角

- **video_prompt.action**：{_ACTION_WRITING_GUIDE}
- **video_prompt.camera_motion**：每个片段只选一种，按画面内容自行选择。
- **video_prompt.ambiance_audio**：{_AMBIANCE_AUDIO_WRITING_GUIDE}
- **video_prompt.dialogue**：仅当小说原文带引号对话时填写；speaker 必须出现在 characters_in_segment。

# 创作目标

输出可直接驱动 AI 生成的、视觉一致、节奏紧凑的分镜剧本。忠于原文叙事、保留情绪张力。
"""


def build_drama_prompt(
    project_overview: dict,
    style: str,
    style_description: str,
    characters: dict,
    scenes: dict,
    props: dict,
    scenes_md: str,
    supported_durations: list[int],
    episode: int,
    default_duration: int | None = None,
    aspect_ratio: str = "16:9",
    target_language: str = "中文",
    episode_outline: dict | None = None,
    next_episode_outline: dict | None = None,
    source_kind: str = "novel",
) -> str:
    """构建剧集动画模式的剧本生成 prompt。

    ``episode_outline`` / ``next_episode_outline`` 来自分集账本（title / hook /
    story_beats / next_episode_teaser），None 表示账本无规划数据，prompt 不渲染大纲段。

    ``source_kind="screenplay"`` 时翻为「提取/逐字透传」：台词逐字照搬进
    ``video_prompt.dialogue``、画外音逐字落入 ``voiceover``，泛指 speaker 放宽；
    默认 ``"novel"`` 维持原「改编/创作」语义、voiceover 留空。
    """
    character_names = list(characters.keys())
    scene_names = list(scenes.keys())
    prop_names = list(props.keys())
    pacing_block = (render_pacing_section("drama") + "\n\n") if is_v2_enabled() else ""
    outline_block = _format_episode_outline_block(episode_outline, next_episode_outline)

    is_screenplay = source_kind == "screenplay"
    role_task = _DRAMA_ROLE_SCREENPLAY if is_screenplay else _DRAMA_ROLE_NOVEL
    dialogue_guide = _DRAMA_DIALOGUE_GUIDE_SCREENPLAY if is_screenplay else _DRAMA_DIALOGUE_GUIDE_NOVEL
    voiceover_guide = _DRAMA_VOICEOVER_GUIDE_SCREENPLAY if is_screenplay else _DRAMA_VOICEOVER_GUIDE_NOVEL
    creative_goal = _DRAMA_GOAL_SCREENPLAY if is_screenplay else _DRAMA_GOAL_NOVEL
    # screenplay 下台词 / 说话人 / 画外音逐字保真，必须排除在目标语言要求之外：line、voiceover
    # 逐字保留原文，speaker 是角色资产引用键（须等于登记角色名），任一被翻译都会与 project.json 资产失配
    language_rule = (
        f"除 `video_prompt.dialogue[].line`、`video_prompt.dialogue[].speaker` 与 `voiceover[]` 外，"
        f"所有字符串值必须使用 {target_language}；这些字段逐字保留剧本原文、不翻译、不改写"
        "（speaker 沿用 characters_in_scene 中登记的角色名原文，群演沿用原文称呼）。JSON 键名 / 枚举值保持英文。"
        if is_screenplay
        else f"所有字符串值必须使用 {target_language}；JSON 键名 / 枚举值保持英文。"
    )

    return f"""# 角色与任务

{role_task}
你的任务：基于下方"分镜拆分表"，逐条产出符合 schema 的 JSON 剧本。

**输出语言**：{language_rule}
**结构约束**：字段 / 枚举 / 必填项由 response_schema 强制；本提示只解释**如何写好每个字段的内容**。

{pacing_block}# 上下文

<overview>
{project_overview.get("synopsis", "")}

题材：{project_overview.get("genre", "")}
主题：{project_overview.get("theme", "")}
世界观：{project_overview.get("world_setting", "")}
</overview>

<style>
风格：{style}
描述：{style_description}
画面比例：{aspect_ratio}（{_format_aspect_ratio_desc(aspect_ratio)}）
</style>

<characters>
{_format_names(characters)}
</characters>

<project_scenes>
{_format_names(scenes)}
</project_scenes>

<props>
{_format_names(props)}
</props>

<shots>
{scenes_md}
</shots>

shots 表每行是一个分镜，包含：分镜 ID（E{episode}S{{序号}}，当前为第 {episode} 集）、分镜描述、{_format_duration_constraint(supported_durations, default_duration)}、是否为 segment_break。

{outline_block}<episode_constraints>
当前正在生成第 {episode} 集。本集所有 scene_id 必须严格使用 `E{episode}S{{两位序号}}` 格式（如 E{episode}S01、E{episode}S02），不得使用其他集号前缀。
若 shots 表里出现非 `E{episode}` 前缀（如 E1S..），视为脏数据，请按当前集号 `E{episode}` 重写。
</episode_constraints>

# 字段写作指引

对每个分镜，按下列章节填写字段。

## 基础字段

- **characters_in_scene** / **scenes** / **props**：仅列出此分镜画面或对话中实际出现的资产。
  - 候选 characters：[{", ".join(character_names) or "（无）"}]
  - 候选 scenes：[{", ".join(scene_names) or "（无）"}]
  - 候选 props：[{", ".join(prop_names) or "（无）"}]
  - 不要发明候选之外的名称。
- **segment_break** / **duration_seconds**：与 shots 表保持一致。

## 图片提示词（image_prompt）——切换到「摄影师」视角

- **image_prompt.scene**：{_SCENE_WRITING_GUIDE}
- **image_prompt.composition.shot_type**：从枚举中按画面内容选择，不强加倾向。
- **image_prompt.composition.lighting**：{_LIGHTING_WRITING_GUIDE}
- **image_prompt.composition.ambiance**：{_AMBIANCE_WRITING_GUIDE}

## 视频提示词（video_prompt）——切换到「动作设计师」视角

- **video_prompt.action**：{_ACTION_WRITING_GUIDE}
- **video_prompt.camera_motion**：每个分镜只选一种，按画面内容自行选择。
- **video_prompt.ambiance_audio**：{_AMBIANCE_AUDIO_WRITING_GUIDE}
- **video_prompt.dialogue**：{dialogue_guide}

## 画外音（voiceover）

- **voiceover**：{voiceover_guide}

# 创作目标

{creative_goal}
"""


def build_normalize_prompt(
    novel_text: str,
    project_overview: dict,
    style: str,
    characters: dict,
    scenes: dict,
    props: dict,
    default_duration: int | None,
    supported_durations: list[int],
    episode: int,
    source_kind: str = "novel",
) -> str:
    """Step-1 normalization prompt: source text → markdown scene table.

    Consumed by ``normalize_drama_script`` MCP tool. Sibling of
    ``build_drama_prompt`` (step 2 of the drama pipeline).

    ``source_kind="screenplay"`` 时翻为「提取/逐字保留」：场景描述列照搬作者写下的
    台词与画外音原文（供 step2 逐字透传），不做改编；默认 ``"novel"`` 维持「改编」语义。
    """
    char_list = _format_names(characters)
    scene_list = _format_names(scenes)
    prop_list = _format_names(props)

    is_screenplay = source_kind == "screenplay"
    task_line = _NORMALIZE_TASK_SCREENPLAY if is_screenplay else _NORMALIZE_TASK_NOVEL
    source_heading = "剧本原文" if is_screenplay else "小说原文"
    source_tag = "screenplay" if is_screenplay else "novel"
    output_intro = "将剧本提取为场景列表" if is_screenplay else "将小说改编为场景列表"
    scene_rule = _NORMALIZE_SCENE_RULE_SCREENPLAY if is_screenplay else _NORMALIZE_SCENE_RULE_NOVEL
    break_rule = _NORMALIZE_BREAK_RULE_SCREENPLAY if is_screenplay else _NORMALIZE_BREAK_RULE_NOVEL

    # 规范化 + 校验：空集合或 default 不在集合内都会产出自相矛盾的提示词，
    # 让生成阶段失败比让 LLM 见到"只能取 — 中的值"更便于诊断（PR #528 review）。
    normalized_durations = sorted({int(d) for d in supported_durations})
    if not normalized_durations:
        raise ValueError("supported_durations 不能为空：必须提供模型支持的秒数集合")
    if default_duration is not None and int(default_duration) not in normalized_durations:
        raise ValueError(f"default_duration={default_duration} 不在 supported_durations={normalized_durations} 内")

    durations_str = ", ".join(str(d) for d in normalized_durations)
    max_dur = normalized_durations[-1]

    if default_duration is not None:
        duration_rules = (
            f"- 时长：只能取 {durations_str} 中的值（该视频模型支持的秒数集合）\n"
            f"- 每场景默认 {default_duration} 秒；打斗、大场面、情绪铺陈等画面可取更长值至上限 {max_dur} 秒，"
            "不要默认挑最短值"
        )
    else:
        duration_rules = (
            f"- 时长：只能取 {durations_str} 中的值（该视频模型支持的秒数集合）\n"
            f"- 按画面内容复杂度匹配合适时长（最长 {max_dur} 秒），不强制默认值"
        )

    return f"""{task_line}

## 项目信息

<overview>
{project_overview.get("synopsis", "")}

题材类型：{project_overview.get("genre", "")}
核心主题：{project_overview.get("theme", "")}
世界观设定：{project_overview.get("world_setting", "")}
</overview>

<style>
{style}
</style>

<characters>
{char_list}
</characters>

<scenes>
{scene_list}
</scenes>

<props>
{prop_list}
</props>

## {source_heading}

<{source_tag}>
{novel_text}
</{source_tag}>

## 输出要求

{output_intro}，使用 Markdown 表格格式：

| 场景 ID | 场景描述 | 时长 | segment_break |
|---------|---------|------|---------------|
| E{episode}S01 | 详细的场景描述... | <duration> | 是 |
| E{episode}S02 | 详细的场景描述... | <duration> | 否 |

规则：
- 当前正在生成第 {episode} 集；所有场景 ID 必须使用 `E{episode}S{{两位序号}}` 格式，不得使用其他集号前缀
{scene_rule}
{duration_rules}
{break_rule}
- 每个场景应为一个独立的视觉画面，可以在指定时长内完成
- 避免一个场景包含多个不同的动作或画面切换

仅输出 Markdown 表格，不要包含其他解释文字。
"""


# ---------------------------------------------------------------------------
# 项目概述（overview）prompt
#
# novel（默认，含非法/缺省值）：从源文正文归纳题材 / 主题 / 故事梗概 / 世界观。
# screenplay：提取优先——作者常在剧本里附「创作方案」前言（以任意形态写明核心设定，
# 无固定标记），优先照用其设定填字段，缺失才退回从正文归纳。
# ---------------------------------------------------------------------------

_OVERVIEW_TASK_NOVEL = "请分析以下小说内容，提取关键信息："
_OVERVIEW_TASK_SCREENPLAY = (
    "请分析以下成品剧本，提炼项目概述（题材 / 主题 / 故事梗概 / 世界观）。\n"
    "剧本里可能附有作者写下的创作方案——以任意形态（开篇前言、大纲、设定卡等，标题与排版各异）"
    "写明题材、主题、一句话故事、世界观等核心设定。若能识别出这类创作方案，"
    "请优先照用作者已写下的设定填充对应字段（忠于原意，可精炼归并、不另起炉灶重新推断）；"
    "剧本未附创作方案时，再从剧本正文自行归纳。"
)


def build_overview_prompt(source_content: str, source_kind: str = "novel") -> str:
    """构建项目概述（overview）生成 prompt。

    ``source_kind="screenplay"`` 时翻为「提取优先」：作者若在剧本内写下创作方案前言
    （题材 / 主题 / 一句话故事 / 世界观，形态不限、无固定标记），优先照用其设定填充
    overview 字段，缺失才退回从正文归纳。``"novel"``（默认，含非法值）维持从正文归纳的原行为。
    """
    task = _OVERVIEW_TASK_SCREENPLAY if source_kind == "screenplay" else _OVERVIEW_TASK_NOVEL
    return f"{task}\n\n{source_content}"
