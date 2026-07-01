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
from lib.speech_rate import speech_rate_units_per_second
from lib.text_metrics import reading_unit_noun


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
本集大纲（分集规划设计，剧本内容应覆盖全部故事节点）：
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
# 两段式分层文案（见 ADR 0041）：step1（normalize）= 内容、step2（drama）= 视觉。
#
# 内容抽取前移到 step1：场景边界、出场资产、逐字口播 utterances、原文锚 source_text、
# 视觉改编描述 scene_description 一次定稿，并按 source_kind 切「改编 / 提取」口径。
# step2 只补视觉层（image_prompt / video_prompt），按 scene_id 透传内容、不再识别口播、
# 不分 source_kind——故 step2 文案无 novel/screenplay 分支。
# ---------------------------------------------------------------------------

# step1（build_normalize_prompt）开篇任务句
_NORMALIZE_TASK_NOVEL = (
    "你的任务是将小说原文**改编**为结构化的分镜场景内容（含视觉改编描述、逐字口播 utterances "
    "与原文锚 source_text），用于后续 AI 视频生成。"
)
_NORMALIZE_TASK_SCREENPLAY = (
    "你的任务是从作者已写好的剧本中**提取**结构化的分镜场景内容："
    "逐字保留台词与画外音（落在 utterances）、摘录原文锚 source_text、把视觉层转写为场景描述，"
    "用于后续 AI 视频生成。这是成品剧本、不是待加工的素材——只做提取、不做再创作。"
)

# step1 scene_description（视觉改编自由文本）填写规则——只承载视觉内容，口播不内嵌
_NORMALIZE_SCENE_RULE_NOVEL = (
    "改编后的视觉化描述：角色动作、神态、环境、光影氛围，适合画面呈现。"
    "**台词 / 画外音不要写进这里**——口播统一落在 utterances，本字段只承载视觉内容。"
)
_NORMALIZE_SCENE_RULE_SCREENPLAY = (
    "把作者写下的运镜、景别、舞台提示、视觉场景转写为画面视觉描述。"
    "**台词 / 画外音不要写进这里**——逐字落在 utterances，本字段只承载视觉内容；"
    "排版符号（markdown、△、各类标签、表格、emoji）一律剥离，只留干净文本。"
)

# step1 utterances（场景级有序发声序列）填写规则。每条 {kind, speaker, text}：
# kind=dialogue 必带非空 speaker、kind=voiceover 必无 speaker（speaker: null）。
_NORMALIZE_UTTERANCES_NOVEL = (
    '按口播出现顺序产出发声序列：角色台词写为 `{kind: "dialogue", speaker, text}`'
    "（speaker 必须出现在 characters_in_scene、text 填台词内容）。"
    '叙述、心理独白等不靠画面演出的内容，可按剧情语境判断写为画外音 `{kind: "voiceover", speaker: null, text}`——'
    "是否产出由你依语境创作判断，不预设规则或类别白名单、也不作兜底，自然需要则产出。"
    "场景无口播则留空数组 []。"
)
_NORMALIZE_UTTERANCES_SCREENPLAY = (
    "把作者写下的台词与画外音**逐字照搬**为有序发声序列，按它们在场景中的先后排列："
    '角色台词写为 `{kind: "dialogue", speaker, text}`（speaker 填原文说话人：命名角色应来自 '
    "characters_in_scene，路人群演如「老人甲」「村民若干」照填原文称呼即可、可不在 characters_in_scene；"
    'text 填台词原文）；画外音 / 旁白写为 `{kind: "voiceover", speaker: null, text}`（无说话人）。'
    "不改写、不润色、不删减、不补写。场景无口播则留空数组 []。"
)

# step1 source_text（逐字原文锚）填写规则——两源共用
_NORMALIZE_SOURCE_TEXT_GUIDE = (
    "逐字摘录本场景对应的原文片段（追溯锚：用于对照原文、定位失真、为单场景重生成提供依据；"
    "不被朗读、不出音）。尽量与原文一致、宁缺毋造（best-effort，无把握可留空）。"
)

# step1 segment_break 规则
_NORMALIZE_BREAK_RULE_NOVEL = '- segment_break：场景切换点标记"是"，同一连续场景标"否"'
_NORMALIZE_BREAK_RULE_SCREENPLAY = (
    "- segment_break：沿用剧本自带的场次/场景切换——场次变更（地点 / 时间 / 场景切换）标「是」，"
    "同一场次内标「否」；不要重新切碎作者的场次"
)

# step2（build_drama_prompt）开篇角色定位 + 收尾目标——视觉层专责，无 source_kind 分支
_DRAMA_VISUAL_ROLE = (
    "你是一位资深的短剧分镜摄影 / 动作设计师。下方分镜内容（场景边界、出场资产、逐字口播、"
    "原文锚、视觉改编描述）均已定稿，你的唯一职责是为每个分镜补全视觉生产层："
    "image_prompt（画面）与 video_prompt（动作 / 运镜 / 环境音）。"
    "**不要改写或重述口播、不要新增 / 删除 / 重排分镜、不要改动场景内容**——只按 scene_id 逐条产出视觉字段。"
)
_DRAMA_VISUAL_GOAL = "输出可直接驱动 AI 图像 / 视频生成的、视觉一致、节奏紧凑的视觉层。忠于已定稿的分镜内容与戏剧张力。"


# ---------------------------------------------------------------------------
# Builder
# ---------------------------------------------------------------------------


def _neutralize_tags(value: str) -> str:
    """中和动态文本里的尖括号：novel_text / 资产名出现 </segments> 等标签序列时，避免打散
    标签化 prompt 的块结构。属 prompt 鲁棒性——step2 输出仍由 response_schema 强制，无安全边界。
    """
    return value.replace("<", "＜").replace(">", "＞")


def _format_narration_step1_segments(step1_segments: list[dict]) -> str:
    """把 step1 结构化片段渲染为 step2 的只读上下文：segment_id + 内容字段 + 逐字原文。

    这些字段在 step1 已定、step2 透传不重出；此处仅作为「为该片段写好视觉层」的依据呈现。
    """
    if not step1_segments:
        return "（无片段）"
    lines: list[str] = []
    for seg in step1_segments:
        sid = _neutralize_tags(str(seg.get("segment_id", "?")))
        dur = seg.get("duration_seconds", "?")
        brk = "，场景切换" if seg.get("segment_break") else ""
        chars = _neutralize_tags("、".join(seg.get("characters_in_segment") or []) or "无")
        scene_names = _neutralize_tags("、".join(seg.get("scenes") or []) or "无")
        prop_names = _neutralize_tags("、".join(seg.get("props") or []) or "无")
        # 多行 novel_text 续行缩进进原文块，避免 flush-left 溢出 <segments>；尖括号一并中和防注入
        novel_block = _neutralize_tags(seg.get("novel_text") or "").replace("\n", "\n  ")
        lines.append(
            f"- {sid}（时长 {dur}s{brk}）｜出场角色：{chars}｜场景：{scene_names}｜道具：{prop_names}\n  原文：{novel_block}"
        )
    return "\n".join(lines)


def build_narration_prompt(
    project_overview: dict,
    style: str,
    style_description: str,
    characters: dict,
    scenes: dict,
    props: dict,
    step1_segments: list[dict],
    episode: int,
    aspect_ratio: str = "9:16",
    target_language: str = "中文",
) -> str:
    """构建说书模式 step2（视觉层）prompt。

    step1 已定的 novel_text / 时长 / segment_break / 出场角色 / 场景 / 道具按 segment_id
    透传，step2 只产 image_prompt 与 video_prompt。``<segments>`` 块为只读上下文，
    LLM 不重出这些字段——novel_text 由此不再经 step2 的 LLM 扩写漂移。
    """
    pacing_block = (render_pacing_section("narration") + "\n\n") if is_v2_enabled() else ""
    segments_block = _format_narration_step1_segments(step1_segments)

    return f"""# 角色与任务

你是一位资深的短视频分镜编剧，专精把已定稿的小说片段转化为可直接驱动 AI 图像 / 视频生成的视觉分镜。
你的任务：基于下方已定稿的"片段表"，为**每个片段**产出视觉层（image_prompt 与 video_prompt），按 segment_id 一一对齐。

**只产视觉层**：novel_text、时长、segment_break、出场角色 / 场景 / 道具均已在 step1 定稿、按 segment_id 透传，**不要重复输出、不要改写**；你只产出 image_prompt 与 video_prompt。
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
{segments_block}
</segments>

segments 表每个片段已定稿（segment_id、逐字原文、时长、出场角色 / 场景 / 道具、是否场景切换），为只读上下文。

<episode_constraints>
当前正在生成第 {episode} 集。为每个片段输出一条视觉层，其 segment_id 必须与 segments 表逐字一致——逐一对应，不增、不减、不改写。
</episode_constraints>

# 字段写作指引

为每个片段产出下列视觉字段（其余字段由 step1 透传，勿输出）。

## 图片提示词（image_prompt）——切换到「摄影师」视角

- **image_prompt.scene**：{_SCENE_WRITING_GUIDE}
- **image_prompt.composition.shot_type**：从枚举中按画面内容选择，不强加倾向。
- **image_prompt.composition.lighting**：{_LIGHTING_WRITING_GUIDE}
- **image_prompt.composition.ambiance**：{_AMBIANCE_WRITING_GUIDE}

## 视频提示词（video_prompt）——切换到「动作设计师」视角

- **video_prompt.action**：{_ACTION_WRITING_GUIDE}
- **video_prompt.camera_motion**：每个片段只选一种，按画面内容自行选择。
- **video_prompt.ambiance_audio**：{_AMBIANCE_AUDIO_WRITING_GUIDE}
- **video_prompt.dialogue**：仅当该片段原文带引号对话时填写；speaker 必须出现在该片段的出场角色中。

# 创作目标

输出可直接驱动 AI 生成的、视觉一致、节奏紧凑的分镜视觉层。忠于原文叙事、保留情绪张力。
"""


def render_drama_content_for_step2(content_scenes: list) -> str:
    """把 step1 已定稿的场景内容渲染为 step2 视觉生成的输入块（每分镜一段）。

    口播 / 原文锚仅供 LLM 理解戏剧节奏，明确标注「不要复制进视觉字段」——它们由后端按 scene_id
    透传（见 ``merge_drama_visual_into_scenes``），step2 只产出 image_prompt / video_prompt。

    渲染结果嵌入 step2 prompt 的 ``<shots>`` 块：资产名 / utterances 字段先判 ``isinstance(_, list)``——
    降级 / 手改 step1 可能写成非列表值（字符串会被逐字符迭代、数字会抛 TypeError），非列表按空处理；
    列表内再按 ``isinstance(_, str)`` 过滤非字符串脏数据。所有动态文本过 ``_neutralize_tags`` 中和尖括号——
    逐字 source_text / utterances / scene_description 含 ``<...>`` 时不致打散标签块结构（与 narration 的
    ``_format_narration_step1_segments`` 同口径）。本函数 fail-soft：结构性 fail-loud 在上游 _load_drama_step1_content。
    """
    if not content_scenes:
        return "（无分镜内容）"
    blocks: list[str] = []
    for scene in content_scenes:
        if not isinstance(scene, dict):
            continue
        sid = _neutralize_tags(str(scene.get("scene_id") or "?"))
        duration = scene.get("duration_seconds")
        header = f"### {sid}" + (f"（时长 {duration} 秒）" if duration else "")
        lines = [header]
        raw_chars = scene.get("characters_in_scene")
        raw_scenes_ref = scene.get("scenes")
        raw_props_ref = scene.get("props")
        chars = [c for c in raw_chars if isinstance(c, str)] if isinstance(raw_chars, list) else []
        scenes_ref = [s for s in raw_scenes_ref if isinstance(s, str)] if isinstance(raw_scenes_ref, list) else []
        props_ref = [p for p in raw_props_ref if isinstance(p, str)] if isinstance(raw_props_ref, list) else []
        lines.append(
            f"出场资产：角色 [{_neutralize_tags(', '.join(chars) or '无')}]、"
            f"场景 [{_neutralize_tags(', '.join(scenes_ref) or '无')}]、道具 [{_neutralize_tags(', '.join(props_ref) or '无')}]"
        )
        scene_desc = _neutralize_tags(str(scene.get("scene_description") or "（无）")).replace("\n", "\n  ")
        lines.append(f"视觉改编：{scene_desc}")
        raw_utterances = scene.get("utterances")
        utterances = raw_utterances if isinstance(raw_utterances, list) else []
        if utterances:
            lines.append("口播（仅供理解戏剧节奏，不要复制进视觉字段）：")
            for u in utterances:
                if not isinstance(u, dict):
                    continue
                text = _neutralize_tags(str(u.get("text") or "")).replace("\n", "\n    ")
                if u.get("kind") == "dialogue":
                    speaker = _neutralize_tags(str(u.get("speaker") or ""))
                    lines.append(f"  - [台词] {speaker}：{text}")
                else:
                    lines.append(f"  - [画外音] {text}")
        source_text = scene.get("source_text")
        if source_text:
            source_block = _neutralize_tags(str(source_text)).replace("\n", "\n  ")
            lines.append(f"原文锚（仅供理解，不要复制进视觉字段）：{source_block}")
        blocks.append("\n".join(lines))
    return "\n\n".join(blocks)


def build_drama_prompt(
    project_overview: dict,
    style: str,
    style_description: str,
    scenes_content: str,
    episode: int,
    aspect_ratio: str = "16:9",
    target_language: str = "中文",
) -> str:
    """构建剧集动画模式 step2（视觉层）prompt。

    内容抽取前移到 step1（见 ADR 0041）：场景边界、出场资产、逐字口播 utterances、原文锚
    source_text、视觉改编描述均已在 step1 定稿，``scenes_content`` 是其渲染输入
    （``render_drama_content_for_step2``）。step2 仅产出视觉层（image_prompt / video_prompt），
    LLM 输出按 scene_id 与 step1 内容对齐、由后端合并；不再按 source_kind 分支、不再识别口播、
    不再标注资产或时长——这些都是 step1 的职责。
    """
    pacing_block = (render_pacing_section("drama") + "\n\n") if is_v2_enabled() else ""

    return f"""# 角色与任务

{_DRAMA_VISUAL_ROLE}
你的任务：基于下方已定稿的"分镜内容"，为每个 scene_id 逐条产出视觉层 JSON（image_prompt / video_prompt）。

**输出语言**：所有字符串值必须使用 {target_language}；JSON 键名 / 枚举值保持英文。
**结构约束**：字段 / 枚举 / 必填项由 response_schema 强制；本提示只解释**如何写好每个字段的内容**。
**对齐约束**：每个分镜产出一条视觉层，`scene_id` 必须与下方内容逐字一致、不增不减不改；不要输出口播 / 时长 / 资产等非视觉字段。

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

<shots>
{scenes_content}
</shots>

<episode_constraints>
当前正在生成第 {episode} 集。每条视觉层的 scene_id 必须逐字等于上方分镜内容里的 scene_id；若该 ID 含拆分/编辑后缀（如 `_1`），也必须原样保留，不得改写、合并或新增。
</episode_constraints>

# 字段写作指引

对每个分镜，按下列章节填写视觉字段。

## 图片提示词（image_prompt）——切换到「摄影师」视角

- **image_prompt.scene**：{_SCENE_WRITING_GUIDE}
- **image_prompt.composition.shot_type**：从枚举中按画面内容选择，不强加倾向。
- **image_prompt.composition.lighting**：{_LIGHTING_WRITING_GUIDE}
- **image_prompt.composition.ambiance**：{_AMBIANCE_WRITING_GUIDE}

## 视频提示词（video_prompt）——切换到「动作设计师」视角

- **video_prompt.action**：{_ACTION_WRITING_GUIDE}
- **video_prompt.camera_motion**：每个分镜只选一种，按画面内容自行选择。
- **video_prompt.ambiance_audio**：{_AMBIANCE_AUDIO_WRITING_GUIDE}

# 创作目标

{_DRAMA_VISUAL_GOAL}
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
    target_language: str = "中文",
    source_language: str | None = None,
    episode_outline: dict | None = None,
    next_episode_outline: dict | None = None,
) -> str:
    """Step-1 规范化 prompt：源文 → 结构化分镜内容（utterances + source_text + 视觉改编描述）。

    由 ``normalize_drama_script`` MCP tool 消费。内容抽取前移（见 ADR 0041）：step1 一次定稿场景
    边界、出场资产、逐字口播、原文锚与视觉改编描述，step2 仅透传 + 补视觉。输出受 response_schema
    （``DramaNormalizedScript``）约束为结构化 JSON。

    ``source_kind="screenplay"`` 翻为「提取/逐字保留」：台词与画外音逐字落 utterances、视觉转写为
    scene_description；默认 ``"novel"`` 维持「改编」语义、画外音由语境判断放开。``episode_outline`` /
    ``next_episode_outline`` 来自分集账本，驱动内容覆盖故事节点、末场落地集尾钩子。

    ``source_language`` 供时长指引的「台词口播时长」单向下界软指引取语速（阅读单位 / 秒，来自
    ``lib.speech_rate`` 单一真相源，与保存期上界 warning、字幕派生同口径）；缺省 / 未登记回退默认语速。
    """
    char_list = _format_names(characters)
    scene_list = _format_names(scenes)
    prop_list = _format_names(props)
    character_names = list(characters.keys())
    scene_names = list(scenes.keys())
    prop_names = list(props.keys())

    is_screenplay = source_kind == "screenplay"
    task_line = _NORMALIZE_TASK_SCREENPLAY if is_screenplay else _NORMALIZE_TASK_NOVEL
    source_heading = "剧本原文" if is_screenplay else "小说原文"
    source_tag = "screenplay" if is_screenplay else "novel"
    scene_rule = _NORMALIZE_SCENE_RULE_SCREENPLAY if is_screenplay else _NORMALIZE_SCENE_RULE_NOVEL
    utterances_rule = _NORMALIZE_UTTERANCES_SCREENPLAY if is_screenplay else _NORMALIZE_UTTERANCES_NOVEL
    break_rule = _NORMALIZE_BREAK_RULE_SCREENPLAY if is_screenplay else _NORMALIZE_BREAK_RULE_NOVEL
    outline_block = _format_episode_outline_block(episode_outline, next_episode_outline)

    # 资产引用字段（characters_in_scene / scenes / props，须逐字等于 project.json 登记名）与
    # 说话人引用 `utterances[].speaker`（须等于 characters_in_scene 中登记的角色名）须排除在目标语言要求外——
    # 两者被翻译都会与已登记资产失配（speaker 失配会破坏字幕归属 / 后续 TTS 配音映射）。source_text 是逐字
    # 原文锚、两源都摘录原文不译。screenplay 额外把台词 `utterances[].text` 也逐字保留（提取优先）；
    # novel 的台词 text 仍按目标语言改编。
    if is_screenplay:
        language_rule = (
            "除资产引用字段（`characters_in_scene[]` / `scenes[]` / `props[]`，须逐字等于 project.json 登记名）"
            "与逐字字段（`utterances[].text` / `utterances[].speaker` / `source_text`）外，"
            f"其余自然语言字符串值必须使用 {target_language}；上述豁免字段逐字保留原文、不翻译、不改写"
            "（speaker 沿用 characters_in_scene 中登记的角色名原文，群演沿用原文称呼）。JSON 键名 / 枚举值保持英文。"
        )
    else:
        language_rule = (
            "除资产引用字段（`characters_in_scene[]` / `scenes[]` / `props[]`，须逐字等于 project.json 登记名）、"
            "说话人引用 `utterances[].speaker`（须等于 characters_in_scene 中登记的角色名、不翻译）"
            f"与逐字原文锚 `source_text` 外，其余自然语言字符串值必须使用 {target_language}；JSON 键名 / 枚举值保持英文。"
        )

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
        base_duration_rule = (
            f"只能取 {durations_str} 中的值（该视频模型支持的秒数集合）；默认 {default_duration} 秒，"
            f"打斗 / 大场面 / 情绪铺陈等画面可取更长值至上限 {max_dur} 秒，不要默认挑最短值"
        )
    else:
        base_duration_rule = (
            f"只能取 {durations_str} 中的值（该视频模型支持的秒数集合）；"
            f"按画面内容复杂度匹配合适时长（最长 {max_dur} 秒），不强制默认值"
        )
    # 台词口播时长单向下界软指引：模型为某场选 duration 时，不应选到装不下该场 utterances 口播的短档。
    # 语速（阅读单位 / 秒）从 lib.speech_rate 单一真相源按 source_language 注入、不写死，与保存期上界
    # warning、字幕派生同口径。纯软约束：只在 prompt 里下发靠模型遵守，不加生成后机械改写、不加硬阻塞。
    # source_language 来自 project.json，可能是非字符串脏数据；非字符串回退 None，避免下游
    # speech_rate / reading_unit_noun 的 .strip() 触发 AttributeError（与保存期上界 warning 同口径守卫）。
    source_language = source_language if isinstance(source_language, str) else None
    speech_rate = speech_rate_units_per_second(source_language)
    unit_label = reading_unit_noun(source_language)
    duration_lower_bound_rule = (
        "再按台词口播长度设下界：先估算该场 utterances（台词 + 画外音）念完约需的秒数"
        f"（口播语速约 {speech_rate:g} {unit_label}/秒），在上述可选值里取**不低于**这个秒数的最接近档位；"
        "这是单向下界——画面 / 情绪留白可继续把时长往上撑，但台词永不把时长压到念不完的短档，"
        "utterances 为空（纯画面、无口播）的场景没有此下界、按画面自行取值；"
        f"若口播估算已超过最长 {max_dur} 秒，取最长档即可（不裁台词、不硬塞），保存时会另有提示"
    )
    duration_rule = f"{base_duration_rule}。{duration_lower_bound_rule}"
    pacing_block = (render_pacing_section("drama") + "\n\n") if is_v2_enabled() else ""

    return f"""{task_line}

**输出语言**：{language_rule}
**结构约束**：字段 / 枚举 / 必填项由 response_schema 强制；本提示只解释**如何写好每个字段的内容**。

{pacing_block}## 项目信息

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

{outline_block}# 字段写作指引

把源文拆为有序分镜，逐条产出符合 schema 的结构化场景内容。当前正在生成第 {episode} 集；所有 scene_id 必须用 `E{episode}S{{两位序号}}` 格式（如 E{episode}S01），不得用其他集号前缀。

## 基础字段

- **scene_id**：`E{episode}S{{两位序号}}`，按分镜顺序递增。
- **duration_seconds**：{duration_rule}。
{break_rule}
- **characters_in_scene** / **scenes** / **props**：仅列出此分镜实际出现的资产。
  - 候选 characters：[{", ".join(character_names) or "（无）"}]
  - 候选 scenes：[{", ".join(scene_names) or "（无）"}]
  - 候选 props：[{", ".join(prop_names) or "（无）"}]
  - 不要发明候选之外的名称；泛指群演（如「老人甲」「村民若干」）不登记为角色资产、不进 characters_in_scene。
- **scene_description**：{scene_rule}

## 逐字内容（内容真相源，step2 透传不改）

- **source_text**：{_NORMALIZE_SOURCE_TEXT_GUIDE}
- **utterances**：{utterances_rule}

每个分镜应为一个独立的视觉画面、可在指定时长内完成；避免一个分镜塞入多个动作或画面切换。
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
