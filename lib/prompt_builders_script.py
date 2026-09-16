"""剧本生成 Prompt 构建器（drama / narration 两种 content_mode）。

设计原则：
- 不重复 schema 已声明的枚举（shot_type / camera_motion 等）；让 response_schema 直接约束。
- 多选枚举字段不在 prompt 里写"如何选"判据，避免把人的镜头审美灌给 LLM；
  让模型按画面内容自行决定。
- 不写无法被 LLM 自检的字数硬限制（"≤200 字"）；用示例隐性表达节奏。
- 字段说明用少量正例与带解说的反例传达要求，不堆"必须 / 禁止"清单。
- 提示词编写与旁白切分经内置整段模版渲染；资产外观与只读内容保持代码投影。
"""

from lib.prompt_rules.asset_appearance import asset_reference_names, iter_asset_appearances
from lib.prompt_rules.episode_pacing import render_pacing_section
from lib.prompt_rules.episode_target_duration import render_episode_target_duration_rule
from lib.prompt_templates.builtin import BUILTIN_DIRECTORY, builtin_templates
from lib.speech_rate import speech_rate_units_per_second
from lib.text_metrics import reading_unit_noun

# 附加指令（instructions）注入分节的统一标题：五个分集生成入口（plan / script_plan 三工具 / prompt_authoring）
# 共用，措辞保持中性——遵循强度由附加指令正文自行表达，注入模板不添加任何强度限定词。
_ADDITIONAL_INSTRUCTIONS = (BUILTIN_DIRECTORY / "partials/shared/additional_instructions.md").read_text(
    encoding="utf-8"
)
USER_INSTRUCTIONS_HEADER = _ADDITIONAL_INSTRUCTIONS.split("\n", 1)[0]


def append_user_instructions(prompt: str, instructions: str | None) -> str:
    """把附加指令以中性分节追加到 prompt 末尾；空 / None 时原样返回。"""
    if not instructions:
        return prompt
    return prompt + "\n\n" + _ADDITIONAL_INSTRUCTIONS.replace("{{ instructions }}", instructions)


def _format_names(items: dict, asset_type: str) -> str:
    names = asset_reference_names(asset_type, items)
    if not names:
        return "（暂无）"
    return "\n".join(f"- {name}" for name in names)


def format_duration_constraint(supported_durations: list[int], default_duration: int | None) -> str:
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


# 广告 builder 的兼容导出；正文来自与文本模版共用的片段。
_SCENE_WRITING_GUIDE = (BUILTIN_DIRECTORY / "partials/shared/scene_writing_guide.md").read_text(encoding="utf-8")

_ACTION_WRITING_GUIDE = (BUILTIN_DIRECTORY / "partials/shared/action_writing_guide.md").read_text(encoding="utf-8")

_LIGHTING_WRITING_GUIDE = (BUILTIN_DIRECTORY / "partials/shared/lighting_writing_guide.md").read_text(encoding="utf-8")
_AMBIANCE_WRITING_GUIDE = (BUILTIN_DIRECTORY / "partials/shared/ambiance_writing_guide.md").read_text(encoding="utf-8")
_AMBIANCE_AUDIO_WRITING_GUIDE = (BUILTIN_DIRECTORY / "partials/shared/ambiance_audio_writing_guide.md").read_text(
    encoding="utf-8"
)


# ---------------------------------------------------------------------------
# 两段式分层文案（见 ADR 0041）：script_plan（normalize）= 内容、prompt_authoring（drama）= 视觉。
#
# 内容抽取前移到 script_plan：分镜边界、出场资产、逐字口播 utterances、原文锚 source_text、
# 视觉改编描述 scene_description 一次定稿，并按 source_kind 切「改编 / 提取」口径。
# prompt_authoring 只补视觉层（image_prompt / video_prompt），按 scene_id 透传内容、不再识别口播、
# 不分 source_kind——故 prompt_authoring 文案无 novel/screenplay 分支。
# ---------------------------------------------------------------------------

# script_plan（build_normalize_prompt）开篇任务句
_NORMALIZE_TASK_NOVEL = (
    "你的任务是将小说原文**改编**为结构化的分镜内容（含视觉改编描述、逐字口播 utterances "
    "与原文锚 source_text），用于后续 AI 视频生成。"
)
_NORMALIZE_TASK_SCREENPLAY = (
    "你的任务是从作者已写好的剧本中**提取**结构化的分镜内容："
    "逐字保留台词与画外音（落在 utterances）、摘录原文锚 source_text、把视觉层转写为分镜视觉描述，"
    "用于后续 AI 视频生成。这是成品剧本、不是待加工的素材——只做提取、不做再创作。"
)

# script_plan scene_description（视觉改编自由文本）填写规则——只承载视觉内容，口播不内嵌
_NORMALIZE_SCENE_RULE_NOVEL = (
    "改编后的视觉化描述：角色动作、神态、环境、光影氛围，适合画面呈现。"
    "以本分镜当下的单一时空落笔——原文的回忆、闪回、心理活动，改编为此刻可见的载体"
    "（人物神态、手中物件、环境痕迹）；这段描述是后续单帧分镜画面的内容来源。"
    "**台词 / 画外音不要写进这里**——口播统一落在 utterances。"
)
_NORMALIZE_SCENE_RULE_SCREENPLAY = (
    "把作者写下的运镜、景别、舞台提示、视觉场面转写为画面视觉描述。"
    "**台词 / 画外音不要写进这里**——逐字落在 utterances；"
    "排版符号（markdown、△、各类标签、表格、emoji）一律剥离，只留干净文本。"
)

# script_plan utterances（分镜级有序发声序列）填写规则。条目形状与 kind ⇄ speaker 约束
# （dialogue 必带非空 speaker、voiceover 必无 speaker）由 Utterance schema 强制，此处只写内容指导。
_NORMALIZE_UTTERANCES_NOVEL = (
    "按口播出现顺序产出发声序列，台词（dialogue）的 speaker 必须出现在 characters_in_scene。"
    "叙述、心理独白等不靠画面演出的内容，可按剧情语境判断写为画外音（voiceover）——"
    "是否产出由你依语境创作判断，自然需要则产出。分镜无口播则留空。"
)
_NORMALIZE_UTTERANCES_SCREENPLAY = (
    "把作者写下的台词与画外音**逐字照搬**为有序发声序列，按它们在分镜中的先后排列："
    "台词（dialogue）的 speaker 填原文说话人——命名角色应来自 characters_in_scene，"
    "路人群演如「老人甲」「村民若干」照填原文称呼即可、可不在 characters_in_scene；"
    "画外音 / 旁白写为 voiceover。不改写、不润色、不删减、不补写。分镜无口播则留空。"
)

# script_plan source_text（逐字原文锚）填写规则——两源共用
_NORMALIZE_SOURCE_TEXT_GUIDE = "逐字摘录本分镜对应的原文片段，尽量与原文一致、宁缺毋造（无把握可留空）。"

# script_plan segment_break 规则。novel 分支无增量判断标准（「是否为场景切换点」由 schema
# description 表达），不再单列；screenplay 分支保留「沿用作者场次、不重新切碎」的实质指导。
# 变体自带前导换行，空值时模板中不留空行。
_NORMALIZE_BREAK_RULE_NOVEL = ""
_NORMALIZE_BREAK_RULE_SCREENPLAY = (
    "\n- **segment_break**：沿用剧本自带的场次/场景切换——场次变更（地点 / 时间 / 场景切换）标「是」，"
    "同一场次内标「否」；不要重新切碎作者的场次"
)

# ---------------------------------------------------------------------------
# Builder
# ---------------------------------------------------------------------------


def _neutralize_tags(value: str) -> str:
    """中和动态文本里的尖括号：novel_text / 资产名出现 </segments> 等标签序列时，避免打散
    标签化 prompt 的块结构。属 prompt 鲁棒性——prompt_authoring 输出仍由 response_schema 强制，无安全边界。
    """
    return value.replace("<", "＜").replace(">", "＞")


def _format_narration_script_plan_segments(script_plan_segments: list[dict]) -> str:
    """把 script_plan 结构化分镜渲染为 prompt_authoring 的只读上下文：segment_id + 内容字段 + 逐字原文。

    这些字段在 script_plan 已定、prompt_authoring 透传不重出；此处仅作为「为该分镜写好视觉层」的依据呈现。
    """
    if not script_plan_segments:
        return "（无分镜）"
    lines: list[str] = []
    for seg in script_plan_segments:
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
    script_plan_segments: list[dict],
    episode: int,
    aspect_ratio: str = "9:16",
    target_language: str = "中文",
    instructions: str | None = None,
) -> str:
    """构建旁白/解说模式 prompt_authoring（视觉层）prompt。

    script_plan 已定的 novel_text / 时长 / segment_break / 出场角色 / 场景 / 道具按 segment_id
    透传，prompt_authoring 只产 image_prompt 与 video_prompt。``<segments>`` 块为只读上下文，
    LLM 不重出这些字段——novel_text 由此不再经 prompt_authoring 的 LLM 扩写漂移。
    """
    return builtin_templates.render(
        "text/narration_prompt_authoring",
        project_overview={key: project_overview.get(key) for key in ("synopsis", "genre", "theme", "world_setting")},
        style=style,
        style_description=style_description,
        aspect_ratio=aspect_ratio,
        aspect_ratio_label=_format_aspect_ratio_desc(aspect_ratio),
        assets=_project_asset_appearances(characters, scenes, props),
        segments_content=_format_narration_script_plan_segments(script_plan_segments),
        episode=episode,
        target_language=target_language,
        instructions=instructions,
    )


def render_drama_content_for_prompt_authoring(content_scenes: list) -> str:
    """把 script_plan 已定稿的分镜内容渲染为 prompt_authoring 视觉生成的输入块（每分镜一段）。

    口播 / 原文锚仅供 LLM 理解戏剧节奏——「不要复制进视觉字段」由 ``build_drama_prompt`` 在
    ``<shots>`` 块前一次性声明，分镜条目内不逐条重复；它们由后端按 scene_id 透传
    （见 ``merge_drama_visual_into_scenes``），prompt_authoring 只产出 image_prompt / video_prompt。

    渲染结果嵌入 prompt_authoring prompt 的 ``<shots>`` 块：资产名 / utterances 字段先判 ``isinstance(_, list)``——
    降级 / 手改 script_plan 可能写成非列表值（字符串会被逐字符迭代、数字会抛 TypeError），非列表按空处理；
    列表内再按 ``isinstance(_, str)`` 过滤非字符串脏数据。所有动态文本过 ``_neutralize_tags`` 中和尖括号——
    逐字 source_text / utterances / scene_description 含 ``<...>`` 时不致打散标签块结构（与 narration 的
    ``_format_narration_script_plan_segments`` 同口径）。本函数 fail-soft：结构性 fail-loud 在上游 _load_drama_script_plan_content。
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


def _project_asset_appearances(characters: dict | None, scenes: dict | None, props: dict | None) -> dict:
    """资产外观与引用名的键齐全投影；动态尖括号中和后作为模版数据。"""
    return {
        key: [
            {"name": _neutralize_tags(name), "appearance": _neutralize_tags(appearance) or None}
            for name, appearance in iter_asset_appearances(asset_type, bucket)
        ]
        for key, asset_type, bucket in (
            ("characters", "character", characters),
            ("scenes", "scene", scenes),
            ("props", "prop", props),
        )
    }


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
    instructions: str | None = None,
) -> str:
    """构建剧情演绎 prompt_authoring（视觉层）prompt。

    内容抽取前移到 script_plan（见 ADR 0041）：分镜边界、出场资产、逐字口播 utterances、原文锚
    source_text、视觉改编描述均已在 script_plan 定稿，``scenes_content`` 是其渲染输入
    （``render_drama_content_for_prompt_authoring``）。prompt_authoring 仅产出视觉层（image_prompt / video_prompt），
    LLM 输出按 scene_id 与 script_plan 内容对齐、由后端合并；不再按 source_kind 分支、不再识别口播、
    不再标注资产或时长——这些都是 script_plan 的职责。

    ``characters`` / ``scenes`` / ``props`` 注入出场资产的外观描述（project.json 各 bucket），
    供视觉字段写服装 / 材质 / 陈设细节时取材；三者都为 None 时不渲染资产块。
    """
    return builtin_templates.render(
        "text/drama_prompt_authoring",
        project_overview={key: project_overview.get(key) for key in ("synopsis", "genre", "theme", "world_setting")},
        style=style,
        style_description=style_description,
        aspect_ratio=aspect_ratio,
        aspect_ratio_label=_format_aspect_ratio_desc(aspect_ratio),
        assets=(
            _project_asset_appearances(characters, scenes, props)
            if characters is not None or scenes is not None or props is not None
            else None
        ),
        scenes_content=scenes_content,
        episode=episode,
        target_language=target_language,
        instructions=instructions,
    )


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
    speech_rate_override: float | None = None,
    episode_target_duration: int | None = None,
    episode_outline: dict | None = None,
    next_episode_outline: dict | None = None,
) -> str:
    """脚本规划的规范化 prompt：源文 → 结构化分镜内容（utterances + source_text + 视觉改编描述）。

    由 ``generate_script_plan`` 的剧情变体消费。内容抽取前移（见 ADR 0041）：script_plan 一次定稿场景
    边界、出场资产、逐字口播、原文锚与视觉改编描述，prompt_authoring 仅透传 + 补视觉。输出受 response_schema
    （``DramaNormalizedScript``）约束为结构化 JSON。

    ``source_kind="screenplay"`` 翻为「提取/逐字保留」：台词与画外音逐字落 utterances、视觉转写为
    scene_description；默认 ``"novel"`` 维持「改编」语义、画外音由语境判断放开。``episode_outline`` /
    ``next_episode_outline`` 来自分集账本，驱动内容覆盖故事节点、末场落地集尾钩子。

    ``source_language`` 供时长指引的「台词口播时长」单向下界软指引取语速（阅读单位 / 秒，来自
    ``lib.speech_rate`` 单一真相源，与保存期上界 warning、字幕派生同口径）；缺省 / 未登记回退默认语速。
    ``speech_rate_override`` 是项目级语速覆盖（由调用方经 ``project_speech_rate_override`` 解析），
    ``None`` 即无覆盖、回退语言默认。

    ``episode_target_duration`` 是项目级「单集目标时长」偏好（秒，由调用方经
    ``project_episode_target_duration`` 解析），驱动模型决定本集拆多少个场景；``None`` 即未设目标、
    不注入该段。它与 ``default_duration`` 是两个尺度（整集体量 vs 单场默认秒数），同为软偏好。
    """
    char_list = _format_names(characters, "character")
    scene_list = _format_names(scenes, "scene")
    prop_list = _format_names(props, "prop")
    character_names = asset_reference_names("character", characters)
    scene_names = asset_reference_names("scene", scenes)
    prop_names = asset_reference_names("prop", props)

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
    # 语速（阅读单位 / 秒）从 lib.speech_rate 单一真相源取（项目级覆盖优先、否则按 source_language 的
    # 语言默认）、不写死，与保存期上界 warning、字幕派生同口径。纯软约束：只在 prompt 里下发靠模型遵守，
    # 不加生成后机械改写、不加硬阻塞。source_language 来自 project.json，可能是非字符串脏数据；非字符串
    # 回退 None，避免下游 speech_rate / reading_unit_noun 的 .strip() 触发 AttributeError
    # （与保存期上界 warning 同口径守卫）。
    source_language = source_language if isinstance(source_language, str) else None
    speech_rate = speech_rate_units_per_second(source_language, speech_rate_override)
    unit_label = reading_unit_noun(source_language)
    duration_lower_bound_rule = (
        "再按台词口播长度设下界：先估算该场 utterances（台词 + 画外音）念完约需的秒数"
        f"（口播语速约 {speech_rate:g} {unit_label}/秒），在上述档位里取**不低于**这个秒数的最接近档位；"
        "这是单向下界——画面 / 情绪留白可在此之上取更长档位，但台词永不把时长压到念不完的短档，"
        "utterances 为空（纯画面、无口播）的场景没有此下界、按画面自行取值；"
        f"若口播估算已超过最长 {max_dur} 秒，取最长档即可（不删减台词、不强行压进短档），保存时会另有提示"
    )
    # 单集目标时长（整集体量）与上面两条（单场秒数）尺度不同，缀在同一条时长规则末尾共同呈现：
    # 模型据它决定拆多少场，据上面两条决定每场多长。未设目标时该段为空、规则退回现状。
    episode_target_rule = render_episode_target_duration_rule(episode_target_duration)
    duration_rule = f"{base_duration_rule}。{duration_lower_bound_rule}"
    if episode_target_rule:
        duration_rule = f"{duration_rule}。{episode_target_rule}"
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

把源文拆为有序分镜，逐条产出结构化分镜内容。当前正在生成第 {episode} 集。

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
    episode_target_duration: int | None = None,
    instructions: str | None = None,
) -> str:
    """脚本规划的旁白/解说分镜拆分 prompt：源文 → 结构化分镜表（逐字 novel_text + 时长 + 资产登记）。

    由 ``generate_script_plan`` 的旁白变体消费。输出受 response_schema（``NarrationScriptPlanDraft``）
    约束为结构化 JSON——``novel_text`` 逐字保留原文（配音与透传真相源），视觉层由后续 prompt_authoring 按
    ``segment_id`` 对齐补齐。分镜时长的成员校验（∈ ``supported_durations``）由工具后校验兜底，因静态
    ``NarrationScriptPlanSegment.duration_seconds`` 是 ``ge=1, le=60`` 开区间、不在 schema 层枚举硬约束
    （复用既有分镜 schema）。

    ``default_duration`` 为单分镜默认秒数偏好；与 ``build_normalize_prompt`` 不同，此处对漂移到
    ``supported_durations`` 之外的 default 按 None 处理（软偏好、可被内容需要覆盖），不 fail-loud——
    与 split-narration-segments 子智能体的「default 非成员按 null」口径一致。

    ``episode_target_duration`` 是项目级「单集目标时长」偏好（秒），驱动模型决定本集拆多少个分镜；
    ``None`` 即未设目标、不注入该段。与 ``default_duration`` 是两个尺度，同为软偏好。
    """
    normalized_durations = sorted({int(d) for d in supported_durations})
    if not normalized_durations:
        raise ValueError("supported_durations 不能为空：必须提供模型支持的秒数集合")
    if default_duration is not None and int(default_duration) not in normalized_durations:
        default_duration = None

    return builtin_templates.render(
        "text/narration_script_plan",
        project_overview={key: project_overview.get(key) for key in ("synopsis", "genre", "theme", "world_setting")},
        novel_text=novel_text,
        character_names=asset_reference_names("character", characters),
        scene_names=asset_reference_names("scene", scenes),
        prop_names=asset_reference_names("prop", props),
        durations=", ".join(str(d) for d in normalized_durations),
        max_duration=normalized_durations[-1],
        default_duration=default_duration,
        episode_target_duration=episode_target_duration,
        episode=episode,
        target_language=target_language,
        instructions=instructions,
    )


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
