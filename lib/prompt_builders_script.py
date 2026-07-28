"""剧本生成 Prompt 构建器（drama / narration 两种 content_mode）。

设计原则：
- 不重复 schema 已声明的枚举（shot_type / camera_motion 等）；让 response_schema 直接约束。
- 多选枚举字段不在 prompt 里写"如何选"判据，避免把人的镜头审美灌给 LLM；
  让模型按画面内容自行决定。
- 不写无法被 LLM 自检的字数硬限制（"≤200 字"）；用示例隐性表达节奏。
- 字段说明用少量正例与带解说的反例传达要求，不堆"必须 / 禁止"清单。
- 节奏建议由 lib.prompt_rules.episode_pacing 注入，跨 subagent 与 builder 共享。
"""

from lib.prompt_rules.episode_pacing import render_pacing_section
from lib.speech_rate import speech_rate_units_per_second
from lib.text_metrics import reading_unit_noun


def _format_names(items: dict) -> str:
    if not items:
        return "（暂无）"
    return "\n".join(f"- {name}" for name in items.keys())


def _format_assets_with_desc(items: dict) -> str:
    """渲染资产块：名称 + 外观描述，供 step2 视觉层写细节时取材。

    缺描述 / value 非 dict（存量脏数据）时退化为纯名字。名称与描述都是 project.json
    动态文本，过 ``_neutralize_tags`` 中和尖括号；多行描述续行缩进，避免 flush-left
    溢出标签块。
    """
    if not items:
        return "（暂无）"
    lines: list[str] = []
    for name, data in items.items():
        safe_name = _neutralize_tags(str(name))
        desc = data.get("description") if isinstance(data, dict) else None
        if isinstance(desc, str) and desc.strip():
            desc_block = _neutralize_tags(desc.strip()).replace("\n", "\n  ")
            lines.append(f"- {safe_name}：{desc_block}")
        else:
            lines.append(f"- {safe_name}")
    return "\n".join(lines)


# step2 资产块的取材注记（narration / drama 共用）：外观细节以登记描述为准，不自行发明。
_ASSET_APPEARANCE_NOTE = "资产外观以上述描述为准：视觉字段写到出场资产的服装 / 材质 / 陈设细节时从中取材，不自行发明。"


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
    "末场（最后一个或几个分镜）的画面与对白须实际呈现集尾钩子的戏剧内容，让悬念定格在画面上；"
    "有下集预告语时，用结尾画面或对白自然引出，不要生硬插入「下集预告」字样的旁白。"
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

# image_prompt.scene 写作指导：单帧契约 + 分层要素 + 正反例。LLM 对示例的泛化优于对清单的执行。
_SCENE_WRITING_GUIDE = """这段文字将直接生成一张静态图：只描述此刻单帧画面上可见的内容，按主体（姿态 / 表情 / 服装）、环境（陈设 / 物件）、光线、氛围（雾、雨等可见信号）分层给出具体细节，写成连贯的叙述句而非关键词堆叠。光线与氛围在叙述中一笔带过即可，细节分别由 composition.lighting / composition.ambiance 承载。原文若是回忆 / 闪回 / 心理活动，将其改编为此刻可见的载体——人物神态、手中物件、环境痕迹，画面里只保留一个时空。画面元素（材质、装束、道具质感、环境年代特征）须贴合上方 `<style>` 块定义的风格基调，避免与风格相冲的元素混入（例如赛博朋克风下不出现榻榻米，国风水墨下不出现霓虹屏）。
   正例：「林清坐在窗边木桌前，左手撑着下巴，目光落在桌上一封拆开的信纸上；桌面摊着信封与一只褪色的怀表。半边脸笼在右侧落地窗逆光的蓝灰色阴影里，雨丝拍在木格窗棂，玻璃凝着细小水珠。」——主体、环境、光线、氛围各有一笔，全程叙述句。
   正例（原文是回忆）：原文「她想起亡母临终的嘱托」→「沈茹跪坐在灵位前，指尖抵着一串磨得发亮的佛珠，眼睑低垂。案上白烛烧过半，烛泪层层堆叠，烛焰在她侧脸投下摇晃的暖橙光斑；一缕香灰无声落在青砖上。」——回忆的分量由此刻的神态与旧物承载，画面只有一个时空。
   反例（跑偏）：「林清陷入了多年前那个绝望的雨夜，画面基调：忧郁。光影设定：冷调。」——「多年前的雨夜」不在此刻画面上；「忧郁 / 冷调」是抽象标签，不是可见细节。
   反例（过短）：「林清坐在窗边发呆。」——缺少环境元素、光线方向、氛围细节，至少应覆盖主体 / 环境 / 光线 / 氛围中三层。"""

# video_prompt.action 写作指导：i2v 契约（首帧已定、只写运动）+ 分层要素 + 正反例。
_ACTION_WRITING_GUIDE = """首帧画面已定格主体、场景与风格，这段文字驱动它动起来：只描述该时长内发生的运动与变化，不复述画面中的静态内容；镜头运动专由 camera_motion 字段承载，action 只写主体与环境的运动。按主体动作（肢体 / 手势 / 表情过渡）、物件互动（摩挲信纸、推门带起的气流等）、环境动态（衣摆、尘埃、雨势、光影移动）分层写成连贯叙述句；动词应描述物理可观察动作（伸手 / 转身 / 摩挲 / 投向 / 收紧），避免内心动词。优先低缓、连贯的细微动作，动作量与该镜头时长匹配：5 秒级镜头通常完成一个连贯动作 + 一个细节互动；8 秒级可承载一次动作过渡（如「抬头—对视—开口」）；更长的镜头保持单一低缓的动作主线，随时长递增动作段数（如「起身—走到窗前—驻足」），而非叠加多条并行动作。
   正例：「林清缓缓抬起头，眼角微微收紧，手指无意识地摩挲信纸边缘；窗外雨势渐大，桌面投下的雨痕影子在缓慢移动。」——主体动作、物件互动、环境动态各有一笔。
   反例：「林清像蝴蝶般飞舞，思绪在过去与现在之间快速切换。」——「思绪切换」不是可拍摄的运动；「像蝴蝶般」是修辞，不是动作描述。"""

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
    "以本场景当下的单一时空落笔——原文的回忆、闪回、心理活动，改编为此刻可见的载体"
    "（人物神态、手中物件、环境痕迹）；这段描述是后续单帧分镜画面的内容来源。"
    "**台词 / 画外音不要写进这里**——口播统一落在 utterances。"
)
_NORMALIZE_SCENE_RULE_SCREENPLAY = (
    "把作者写下的运镜、景别、舞台提示、视觉场景转写为画面视觉描述。"
    "**台词 / 画外音不要写进这里**——逐字落在 utterances；"
    "排版符号（markdown、△、各类标签、表格、emoji）一律剥离，只留干净文本。"
)

# step1 utterances（场景级有序发声序列）填写规则。条目形状与 kind ⇄ speaker 约束
# （dialogue 必带非空 speaker、voiceover 必无 speaker）由 Utterance schema 强制，此处只写内容指导。
_NORMALIZE_UTTERANCES_NOVEL = (
    "按口播出现顺序产出发声序列，台词（dialogue）的 speaker 必须出现在 characters_in_scene。"
    "叙述、心理独白等不靠画面演出的内容，可按剧情语境判断写为画外音（voiceover）——"
    "是否产出由你依语境创作判断，自然需要则产出。场景无口播则留空。"
)
_NORMALIZE_UTTERANCES_SCREENPLAY = (
    "把作者写下的台词与画外音**逐字照搬**为有序发声序列，按它们在场景中的先后排列："
    "台词（dialogue）的 speaker 填原文说话人——命名角色应来自 characters_in_scene，"
    "路人群演如「老人甲」「村民若干」照填原文称呼即可、可不在 characters_in_scene；"
    "画外音 / 旁白写为 voiceover。不改写、不润色、不删减、不补写。场景无口播则留空。"
)

# step1 source_text（逐字原文锚）填写规则——两源共用
_NORMALIZE_SOURCE_TEXT_GUIDE = "逐字摘录本场景对应的原文片段，尽量与原文一致、宁缺毋造（无把握可留空）。"

# step1 segment_break 规则。novel 分支无增量判断标准（「是否为场景切换点」由 schema
# description 表达），不再单列；screenplay 分支保留「沿用作者场次、不重新切碎」的实质指导。
# 变体自带前导换行，空值时模板中不留空行。
_NORMALIZE_BREAK_RULE_NOVEL = ""
_NORMALIZE_BREAK_RULE_SCREENPLAY = (
    "\n- **segment_break**：沿用剧本自带的场次/场景切换——场次变更（地点 / 时间 / 场景切换）标「是」，"
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
    pacing_block = render_pacing_section("narration") + "\n\n"
    segments_block = _format_narration_step1_segments(step1_segments)

    return f"""# 角色与任务

你是一位资深的短视频分镜编剧，擅长把已定稿的小说片段转化为可直接驱动 AI 图像 / 视频生成的视觉分镜。
你的任务：基于下方已定稿的「片段表」，为**每个片段**产出视觉层（image_prompt 与 video_prompt），按 segment_id 一一对齐。

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
{_format_assets_with_desc(characters)}
</characters>

<scenes>
{_format_assets_with_desc(scenes)}
</scenes>

<props>
{_format_assets_with_desc(props)}
</props>

{_ASSET_APPEARANCE_NOTE}

<segments>
{segments_block}
</segments>

segments 表每个片段已定稿（segment_id、逐字原文、时长、出场角色 / 场景 / 道具、是否场景切换），为只读上下文。

<episode_constraints>
当前正在生成第 {episode} 集。为每个片段输出一条视觉层，其 segment_id 必须与 segments 表逐字一致——逐一对应，不增、不减、不改写。
</episode_constraints>

# 字段写作指引

为每个片段产出下列视觉字段。

## 图片提示词（image_prompt）——切换到「摄影师」视角

- **image_prompt.scene**：{_SCENE_WRITING_GUIDE}
- **image_prompt.composition.shot_type**：从枚举中按画面内容选择，不强加倾向。
- **image_prompt.composition.lighting**：{_LIGHTING_WRITING_GUIDE}
- **image_prompt.composition.ambiance**：{_AMBIANCE_WRITING_GUIDE}

## 视频提示词（video_prompt）——切换到「动作设计师」视角

- **video_prompt.action**：{_ACTION_WRITING_GUIDE}
- **video_prompt.camera_motion**：按画面内容自行选择。
- **video_prompt.ambiance_audio**：{_AMBIANCE_AUDIO_WRITING_GUIDE}
- **video_prompt.dialogue**：speaker 必须出现在该片段的出场角色中。

# 创作目标

输出可直接驱动 AI 生成的、视觉一致、节奏紧凑的分镜视觉层。忠于原文叙事、保留情绪张力。
"""


def render_drama_content_for_step2(content_scenes: list) -> str:
    """把 step1 已定稿的场景内容渲染为 step2 视觉生成的输入块（每分镜一段）。

    口播 / 原文锚仅供 LLM 理解戏剧节奏——「不要复制进视觉字段」由 ``build_drama_prompt`` 在
    ``<shots>`` 块前一次性声明，场景条目内不逐条重复；它们由后端按 scene_id 透传
    （见 ``merge_drama_visual_into_scenes``），step2 只产出 image_prompt / video_prompt。

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
            lines.append("口播：")
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
            lines.append(f"原文锚：{source_block}")
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
    characters: dict | None = None,
    scenes: dict | None = None,
    props: dict | None = None,
) -> str:
    """构建剧集动画模式 step2（视觉层）prompt。

    内容抽取前移到 step1（见 ADR 0041）：场景边界、出场资产、逐字口播 utterances、原文锚
    source_text、视觉改编描述均已在 step1 定稿，``scenes_content`` 是其渲染输入
    （``render_drama_content_for_step2``）。step2 仅产出视觉层（image_prompt / video_prompt），
    LLM 输出按 scene_id 与 step1 内容对齐、由后端合并；不再按 source_kind 分支、不再识别口播、
    不再标注资产或时长——这些都是 step1 的职责。

    ``characters`` / ``scenes`` / ``props`` 注入出场资产的外观描述（project.json 各 bucket），
    供视觉字段写服装 / 材质 / 陈设细节时取材；三者都为 None 时不渲染资产块。
    """
    pacing_block = render_pacing_section("drama") + "\n\n"
    assets_block = ""
    if characters is not None or scenes is not None or props is not None:
        assets_block = f"""<characters>
{_format_assets_with_desc(characters or {})}
</characters>

<scenes>
{_format_assets_with_desc(scenes or {})}
</scenes>

<props>
{_format_assets_with_desc(props or {})}
</props>

{_ASSET_APPEARANCE_NOTE}

"""

    return f"""# 角色与任务

{_DRAMA_VISUAL_ROLE}
你的任务：基于下方已定稿的「分镜内容」，为每个 scene_id 逐条产出视觉层 JSON（image_prompt / video_prompt）。

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

{assets_block}分镜内容中的「口播」与「原文锚」仅供理解戏剧节奏与语境，不要复制进视觉字段。

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
- **video_prompt.camera_motion**：按画面内容自行选择。
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
            f"自然语言字符串值必须使用 {target_language}；JSON 键名 / 枚举值保持英文。"
            "例外（逐字保留原文、不翻译、不改写）：资产引用字段（`characters_in_scene[]` / `scenes[]` / `props[]`，"
            "须逐字等于 project.json 登记名）与逐字字段（`utterances[].text` / `utterances[].speaker` / `source_text`）；"
            "speaker 沿用 characters_in_scene 中登记的角色名原文，群演沿用原文称呼。"
        )
    else:
        language_rule = (
            f"自然语言字符串值必须使用 {target_language}；JSON 键名 / 枚举值保持英文。"
            "例外（逐字保留、不翻译）：资产引用字段（`characters_in_scene[]` / `scenes[]` / `props[]`，"
            "须逐字等于 project.json 登记名）、说话人引用 `utterances[].speaker`"
            "（须等于 characters_in_scene 中登记的角色名）与逐字原文锚 `source_text`。"
        )

    # 规范化 + 校验：空集合或 default 不在集合内都会产出自相矛盾的提示词，
    # 让生成阶段失败比让 LLM 见到"只能取 — 中的值"更便于诊断。
    normalized_durations = sorted({int(d) for d in supported_durations})
    if not normalized_durations:
        raise ValueError("supported_durations 不能为空：必须提供模型支持的秒数集合")
    if default_duration is not None and int(default_duration) not in normalized_durations:
        raise ValueError(f"default_duration={default_duration} 不在 supported_durations={normalized_durations} 内")

    durations_str = ", ".join(str(d) for d in normalized_durations)
    max_dur = normalized_durations[-1]
    if default_duration is not None:
        base_duration_rule = (
            f"从支持的秒数档位（{durations_str}）中按画面内容选择：默认 {default_duration} 秒，"
            f"打斗 / 大场面 / 情绪铺陈等画面可取更长档至 {max_dur} 秒，不要默认选最短档"
        )
    else:
        base_duration_rule = (
            f"从支持的秒数档位（{durations_str}）中按画面内容复杂度匹配合适时长（最长 {max_dur} 秒），不强制默认值"
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
        f"（口播语速约 {speech_rate:g} {unit_label}/秒），在上述档位里取**不低于**这个秒数的最接近档位；"
        "这是单向下界——画面 / 情绪留白可在此之上取更长档位，但台词永不把时长压到念不完的短档，"
        "utterances 为空（纯画面、无口播）的场景没有此下界、按画面自行取值；"
        f"若口播估算已超过最长 {max_dur} 秒，取最长档即可（不删减台词、不强行压进短档），保存时会另有提示"
    )
    duration_rule = f"{base_duration_rule}。{duration_lower_bound_rule}"
    pacing_block = render_pacing_section("drama") + "\n\n"

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

把源文拆为有序分镜，逐条产出结构化场景内容。当前正在生成第 {episode} 集。

## 基础字段

- **scene_id**：`E{episode}S{{两位序号}}` 格式（如 E{episode}S01），按分镜顺序递增，不得用其他集号前缀。
- **duration_seconds**：{duration_rule}。{break_rule}
- **characters_in_scene** / **scenes** / **props**：从下列候选中列出此分镜实际出现的资产。
  - 候选 characters：[{", ".join(character_names) or "（暂无）"}]
  - 候选 scenes：[{", ".join(scene_names) or "（暂无）"}]
  - 候选 props：[{", ".join(prop_names) or "（暂无）"}]
  - 不要发明候选之外的名称；泛指群演（如「老人甲」「村民若干」）不登记为角色资产、不进 characters_in_scene。
- **scene_description**：{scene_rule}

## 逐字内容（内容真相源，定稿后原样保留、不再改写）

- **source_text**：{_NORMALIZE_SOURCE_TEXT_GUIDE}
- **utterances**：{utterances_rule}

每个分镜应为一个独立的视觉画面、可在指定时长内完成；避免在一个分镜内安排多个动作或画面切换。
"""


def build_narration_split_prompt(
    *,
    novel_text: str,
    project_overview: dict,
    characters: dict,
    scenes: dict,
    props: dict,
    default_duration: int | None,
    supported_durations: list[int],
    episode: int,
    target_language: str = "中文",
) -> str:
    """Step-1 说书片段拆分 prompt：源文 → 结构化片段表（逐字 novel_text + 时长 + 资产登记）。

    由 ``split_narration_segments`` MCP tool 消费。输出受 response_schema（``NarrationStep1Draft``）
    约束为结构化 JSON——``novel_text`` 逐字保留原文（配音与透传真相源），视觉层由后续 step2 按
    ``segment_id`` 对齐补齐。片段时长的成员校验（∈ ``supported_durations``）由工具后校验兜底，因静态
    ``NarrationStep1Segment.duration_seconds`` 是 ``ge=1, le=60`` 开区间、不在 schema 层枚举硬约束
    （复用既有片段 schema）。

    ``default_duration`` 为单片段默认秒数偏好；与 ``build_normalize_prompt`` 不同，此处对漂移到
    ``supported_durations`` 之外的 default 按 None 处理（软偏好、可被内容需要覆盖），不 fail-loud——
    与 split-narration-segments subagent 的「default 非成员按 null」口径一致。
    """
    normalized_durations = sorted({int(d) for d in supported_durations})
    if not normalized_durations:
        raise ValueError("supported_durations 不能为空：必须提供模型支持的秒数集合")
    if default_duration is not None and int(default_duration) not in normalized_durations:
        default_duration = None

    durations_str = ", ".join(str(d) for d in normalized_durations)
    max_dur = normalized_durations[-1]
    if default_duration is not None:
        duration_rule = (
            f"单片段默认取 {default_duration} 秒（按朗读语速估算该秒数内能念完的字数）；长句 / 情绪铺陈 / "
            f"关键对话等可从档位中取更长值（至 {max_dur} 秒）——偏好可被内容需要覆盖，硬约束不可"
        )
    else:
        duration_rule = f"按朗读节奏从档位（{durations_str}）中取值（最长 {max_dur} 秒），不强制默认值"
    pacing_block = render_pacing_section("narration") + "\n\n"

    character_names = list(characters.keys())
    scene_names = list(scenes.keys())
    prop_names = list(props.keys())

    return f"""# 角色与任务

你是一位专业的说书内容架构师，本任务是把源文按朗读节奏拆分为适合短视频配音的片段表（step1 内容拆分）。
说书剧本走两段式：本阶段只定内容层——逐字 `novel_text`、片段边界、时长、场景切换标记与出场资产；
视觉层（image_prompt / video_prompt）由后续 step2 按 `segment_id` 对齐生成，`novel_text` 由本阶段定稿后透传、不再重出。

**输出语言**：自然语言字符串值使用 {target_language}；JSON 键名保持英文。
例外（逐字保留、不翻译、不改写）：`novel_text` 逐字等于源文原句（含标点）；资产引用字段
（`characters_in_segment` / `scenes` / `props`）逐字等于下方候选表中的登记名。
**结构约束**：字段 / 枚举 / 必填项由 response_schema 强制；本提示只解释**如何写好每个字段的内容**。

{pacing_block}# 上下文

<overview>
{project_overview.get("synopsis", "")}

题材：{project_overview.get("genre", "")}
主题：{project_overview.get("theme", "")}
世界观：{project_overview.get("world_setting", "")}
</overview>

<characters>
{_format_names(characters)}
</characters>

<scenes>
{_format_names(scenes)}
</scenes>

<props>
{_format_names(props)}
</props>

## 小说原文

<novel>
{novel_text}
</novel>

# 拆分规则

当前正在生成第 {episode} 集。

- **novel_text**：逐字保留小说原文，不改编 / 不删减 / 不添加 / 不改标点（后期配音与透传的真相源）；
  对话片段含完整说话内容与引导语（如「他说道」）。在句号 / 问号 / 感叹号 / 省略号等标点或段落结束处拆分，
  保持语义完整，不拆断完整的语义单元。
- **segment_id**：`E{episode}S{{两位序号}}` 格式（如 E{episode}S01），按顺序递增，不得用其他集号前缀。
- **duration_seconds**：{duration_rule}。取值必须落在支持档位（{durations_str}）内。
- **segment_break**：在真正的场景切换点（时间跳跃 / 空间转换 / 情节转折）标 `true`，同一连续场景内标 `false`，不要滥用。
- **characters_in_segment / scenes / props**：列出该片段 `novel_text` 中实际出现（被叙述或对话提及）的已登记资产，
  名称逐字取自下列候选，不要发明候选之外的名称；泛指群演（老人甲 / 村民若干）不登记、不进 characters_in_segment。
  三个数组均必填，无对应资产时显式写空数组 `[]`。
  - character: {", ".join(character_names) or "（暂无）"}
  - scene: {", ".join(scene_names) or "（暂无）"}
  - prop: {", ".join(prop_names) or "（暂无）"}

请覆盖全部源文，按叙事顺序逐片段产出。
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


def build_overview_prompt(source_content: str, source_kind: str = "novel", target_language: str = "中文") -> str:
    """构建项目概述（overview）生成 prompt。

    ``source_kind="screenplay"`` 时翻为「提取优先」：作者若在剧本内写下创作方案前言
    （题材 / 主题 / 一句话故事 / 世界观，形态不限、无固定标记），优先照用其设定填充
    overview 字段，缺失才退回从正文归纳。``"novel"``（默认，含非法值）维持从正文归纳的原行为。

    overview 产出的字段会注入后续所有生成 prompt，输出语言须与其余 builder 同口径
    （target_language 由调用方按 project.json 的 source_language 解析）。
    """
    task = _OVERVIEW_TASK_SCREENPLAY if source_kind == "screenplay" else _OVERVIEW_TASK_NOVEL
    return f"{task}\n\n**输出语言**：所有字符串值必须使用 {target_language}；JSON 键名 / 枚举值保持英文。\n\n{source_content}"
