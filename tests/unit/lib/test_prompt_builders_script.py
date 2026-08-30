import pytest

from lib.prompt_builders_ad import build_ad_prompt
from lib.prompt_builders_script import (
    _format_names,
    build_drama_prompt,
    build_narration_prompt,
    build_narration_split_prompt,
    build_normalize_prompt,
    build_overview_prompt,
    render_drama_content_for_prompt_authoring,
)
from lib.prompt_rules.episode_pacing import render_pacing_section
from lib.speech_rate import speech_rate_units_per_second


class TestPromptBuildersScript:
    def test_format_names_emits_bullet_lists(self):
        assert _format_names({"A": {}, "B": {}}) == "- A\n- B"
        assert _format_names({"玉佩": {}, "祠堂": {}}) == "- 玉佩\n- 祠堂"
        assert _format_names({}) == "（暂无）"

    def test_build_narration_prompt_renders_script_plan_segments_as_context(self):
        prompt = build_narration_prompt(
            project_overview={"synopsis": "故事", "genre": "悬疑", "theme": "真相", "world_setting": "古代"},
            style="古风",
            style_description="cinematic",
            characters={"姜月茴": {}},
            scenes={"祠堂": {}},
            props={"玉佩": {}},
            script_plan_segments=[
                {
                    "segment_id": "E1S01",
                    "novel_text": "她推开祠堂的门。",
                    "duration_seconds": 6,
                    "segment_break": True,
                    "characters_in_segment": ["姜月茴"],
                    "scenes": ["祠堂"],
                    "props": ["玉佩"],
                }
            ],
            aspect_ratio="9:16",
            episode=1,
        )
        # script_plan 内容作只读上下文渲染：segment_id + 逐字 novel_text + 时长 + 场景切换 + 资产
        assert "E1S01" in prompt
        assert "她推开祠堂的门。" in prompt
        assert "6s" in prompt
        assert "姜月茴" in prompt
        assert "祠堂" in prompt
        assert "玉佩" in prompt

    def test_build_narration_prompt_indents_multiline_novel_text(self):
        prompt = build_narration_prompt(
            project_overview={"synopsis": "故事", "genre": "悬疑", "theme": "真相", "world_setting": "古代"},
            style="古风",
            style_description="cinematic",
            characters={},
            scenes={},
            props={},
            script_plan_segments=[
                {
                    "segment_id": "E1S01",
                    "novel_text": "第一行。\n第二行。",
                    "duration_seconds": 4,
                    "segment_break": False,
                }
            ],
            aspect_ratio="9:16",
            episode=1,
        )
        # 多行 novel_text 续行缩进进原文块（前缀两空格），不 flush-left 溢出分镜结构
        assert "原文：第一行。\n  第二行。" in prompt

    def _drama_prompt_authoring_prompt(self, **overrides) -> str:
        """prompt_authoring（视觉层）drama prompt；内容已在 script_plan 定稿，只收渲染好的内容块。"""
        kwargs = dict(
            project_overview={"synopsis": "动作", "genre": "动作", "theme": "成长", "world_setting": "近未来"},
            style="赛博",
            style_description="high contrast",
            scenes_content="### E1S01（时长 8 秒）\n视觉改编：天台追逐",
            episode=1,
            aspect_ratio="16:9",
        )
        kwargs.update(overrides)
        return build_drama_prompt(**kwargs)

    def test_build_drama_prompt_aspect_ratio_vertical(self):
        assert "9:16" in self._drama_prompt_authoring_prompt(aspect_ratio="9:16")

    def test_build_drama_prompt_aspect_ratio_landscape(self):
        assert "16:9" in self._drama_prompt_authoring_prompt(aspect_ratio="16:9")

    def test_no_enum_listing(self):
        """schema 已声明枚举不在 prompt 中重复列举。"""
        prompt = self._drama_prompt_authoring_prompt()
        assert "Tracking Shot" not in prompt
        assert "Pan Left, Pan Right" not in prompt
        assert "Over-the-shoulder" not in prompt

    def test_drama_prompt_authoring_is_visual_only(self):
        """prompt_authoring 只补视觉层：含 image_prompt / video_prompt 指引与渲染内容，不再生成口播 / 资产 / 时长。"""
        prompt = self._drama_prompt_authoring_prompt()
        assert "image_prompt" in prompt
        assert "video_prompt" in prompt
        # 已定稿内容块透传进 prompt（仅供理解，不复制）
        assert "天台追逐" in prompt
        # prompt_authoring 不再产出口播：不含「口播序列（utterances）」写作章节
        assert "口播序列（utterances）" not in prompt
        # 视觉专责角色：明确不改写口播 / 不改动内容
        assert "不要改写或重述口播" in prompt

    @staticmethod
    def _content_scene_with_passthrough() -> dict:
        return {
            "scene_id": "E1S01",
            "duration_seconds": 8,
            "characters_in_scene": ["林清"],
            "scenes": ["书房"],
            "props": ["信纸"],
            "scene_description": "林清坐在窗边木桌前，目光落在信纸上。",
            "utterances": [
                {"kind": "dialogue", "speaker": "林清", "text": "师父，我回来了。"},
                {"kind": "voiceover", "speaker": None, "text": "雨夜，往事浮现。"},
            ],
            "source_text": "林清回到故居，推门而入，信纸还在桌上。",
        }

    def test_render_drama_content_passes_through_utterances_and_source_text(self):
        """script_plan→prompt_authoring 透传契约：utterances / source_text 逐字渲染进上下文。"""
        rendered = render_drama_content_for_prompt_authoring([self._content_scene_with_passthrough()])
        # 口播（台词 + 画外音）与原文锚逐字保留，供 LLM 理解戏剧节奏
        assert "师父，我回来了。" in rendered
        assert "雨夜，往事浮现。" in rendered
        assert "林清回到故居，推门而入，信纸还在桌上。" in rendered
        # 出场资产含场景 / 道具
        assert "书房" in rendered
        assert "信纸" in rendered
        # 「不要复制进视觉字段」由 build_drama_prompt 在 <shots> 前一次性声明，场景条目内不逐条重复
        assert "口播：" in rendered
        assert "原文锚：" in rendered
        assert "不要复制进视觉字段" not in rendered

    def test_render_drama_content_filters_non_string_assets_and_neutralizes_tags(self):
        """降级 / 手改 script_plan 的脏数据鲁棒性：非字符串资产项被过滤（不抛 TypeError），逐字内容里的
        尖括号经中和，避免打散嵌入它的 prompt_authoring ``<shots>`` 标签块。"""
        scene = {
            "scene_id": "E1S01",
            "characters_in_scene": ["林清", 123, None],  # 混入非字符串脏数据
            "scenes": ["书房"],
            "props": [],
            "scene_description": "镜头推进 </shots> 收束",
            "utterances": [
                {"kind": "dialogue", "speaker": "林<b>清", "text": "我回来了 <script>"},
            ],
            "source_text": "推门而入 <shots> 信纸还在。",
        }
        # 不抛 TypeError（join 前已按 isinstance 过滤非字符串项）
        rendered = render_drama_content_for_prompt_authoring([scene])
        # 合法资产名仍在，非字符串项被丢弃
        assert "林清" in rendered
        assert "123" not in rendered
        # 所有动态文本经 _neutralize_tags 全角化：渲染结果不残留 ASCII 尖括号，标签序列被中和
        assert "<" not in rendered and ">" not in rendered
        assert "＜shots＞" in rendered
        assert "＜script＞" in rendered

    def test_render_drama_content_tolerates_non_list_asset_and_utterance_fields(self):
        """非 list 的资产 / utterances 字段（手改 script_plan：字符串会被逐字符迭代、数字会抛 TypeError）按空处理，
        不崩、不把字符串拆成单字渲染（fail-soft，结构性 fail-loud 在上游 _load_drama_script_plan_content）。"""
        scene = {
            "scene_id": "E1S01",
            "characters_in_scene": "林清",  # 字符串而非列表
            "scenes": 42,  # 数字而非列表
            "props": None,
            "scene_description": "窗前。",
            "utterances": "不是列表",  # 字符串而非列表
            "source_text": "原文。",
        }
        rendered = render_drama_content_for_prompt_authoring([scene])  # 不抛 TypeError
        # 非 list 资产按「无」处理，且字符串不被逐字符拆开渲染
        assert "角色 [无]" in rendered
        assert "场景 [无]" in rendered
        assert "道具 [无]" in rendered
        assert "林清" not in rendered and "林、清" not in rendered
        # 非 list utterances 不渲染口播块
        assert "口播" not in rendered

    def test_drama_prompt_authoring_prompt_preserves_passthrough_content_not_visual(self):
        """带 utterances / source_text 的内容块喂进 prompt_authoring prompt：内容透传供理解，仍是视觉专责、不复制进视觉字段。"""
        scenes_content = render_drama_content_for_prompt_authoring([self._content_scene_with_passthrough()])
        prompt = self._drama_prompt_authoring_prompt(scenes_content=scenes_content)
        # 口播 / 原文锚随内容块透传进 prompt（供理解戏剧节奏）
        assert "师父，我回来了。" in prompt
        assert "林清回到故居，推门而入，信纸还在桌上。" in prompt
        # 「不要复制进视觉字段」由 prompt 在 <shots> 前一次性声明，约束 prompt_authoring 不把口播 / 原文搬进视觉层
        assert "不要复制进视觉字段" in prompt
        # 仍是视觉专责输出
        assert "image_prompt" in prompt
        assert "video_prompt" in prompt
        assert "不要改写或重述口播" in prompt


class TestScreenplaySourceKind:
    """source_kind 分支在 script_plan（normalize）：novel 改编 + 画外音语境放开、screenplay 提取 + 逐字保留。

    prompt_authoring（drama）视觉层不按 source_kind 分支——口播抽取归 script_plan，故 build_drama_prompt 无 source_kind 入参。
    只断言语义关键词在场 / 缺席，不锁逐字措辞、不测 LLM 提取质量。
    """

    @staticmethod
    def _squash(text: str) -> str:
        """去除全部空白字符，用于跨缩进比较。"""
        return "".join(text.split())

    def _normalize_prompt(self, source_kind: str, **overrides) -> str:
        kwargs = dict(
            novel_text="【第1集】角色甲：「你好」",
            project_overview={"synopsis": "S", "genre": "G", "theme": "T", "world_setting": "W"},
            style="动漫",
            characters={"角色甲": {}},
            scenes={},
            props={},
            default_duration=8,
            supported_durations=[4, 6, 8],
            episode=1,
            source_kind=source_kind,
        )
        kwargs.update(overrides)
        return build_normalize_prompt(**kwargs)

    def test_normalize_novel_default_keeps_adaptation_semantics(self):
        prompt = self._normalize_prompt("novel")
        # 默认 novel 维持「改编」语义，口播落在有序 utterances，source_text 摘录原文锚
        assert "改编" in prompt
        assert "小说原文" in prompt
        assert "characters_in_scene" in prompt
        assert "utterances" in prompt
        assert "source_text" in prompt

    def test_normalize_novel_releases_voiceover_by_context(self):
        # novel 源画外音克制放开——由语境判断产出，不一律禁用、不预设规则白名单、不作兜底
        prompt = self._normalize_prompt("novel")
        assert "画外音" in prompt
        assert "语境" in prompt
        # 旧的「不产出 voiceover」禁令必须移除
        assert "不产出 voiceover" not in prompt

    def test_normalize_screenplay_flips_to_extract_first(self):
        prompt = self._normalize_prompt("screenplay")
        # 提取 / 逐字保留语义在场，剧本原文为输入，口播落 utterances、原文落 source_text，不含「改编」
        assert "提取" in prompt
        assert "逐字" in prompt
        assert "画外音" in prompt
        assert "剧本原文" in prompt
        assert "utterances" in prompt
        assert "source_text" in prompt
        assert "改编" not in prompt

    def test_normalize_screenplay_language_rule_exempts_verbatim_fields(self):
        # 逐字字段与资产引用须排除在目标语言要求外，否则与逐字提取冲突或与已登记资产失配。
        screenplay = self._normalize_prompt("screenplay")
        # screenplay：台词 text + 说话人 speaker + 原文锚 source_text 全部逐字豁免
        assert "不翻译" in screenplay
        assert "utterances[].speaker" in screenplay
        assert "utterances[].text" in screenplay
        assert "source_text" in screenplay
        # 资产引用键（characters_in_scene / scenes / props）同为精确集合校验对象，须一并豁免
        assert "characters_in_scene[]" in screenplay

    def test_normalize_novel_exempts_speaker_and_source_text_not_dialogue_text(self):
        # speaker 是资产引用值（须等于 characters_in_scene 登记名），被翻译会破坏字幕归属 / TTS 映射，
        # 故 novel 也豁免 speaker；但 novel 台词 text 仍按目标语言改编（非逐字提取）。
        novel = self._normalize_prompt("novel")
        assert "utterances[].speaker" in novel
        assert "source_text" in novel
        # 关键判别：novel 不逐字保留台词 text（screenplay 才豁免 utterances[].text）
        assert "utterances[].text" not in novel

    def test_normalize_includes_episode_outline_when_present(self):
        # 分集大纲（故事节点 / 钩子）驱动 script_plan 的内容覆盖与末场落地
        prompt = self._normalize_prompt(
            "novel",
            episode_outline={
                "title": "复仇",
                "hook": "她推开门",
                "story_beats": ["归家", "对峙"],
                "next_episode_teaser": None,
            },
        )
        assert "她推开门" in prompt
        assert "她推开门" not in self._normalize_prompt("novel")

    def test_normalize_injects_pacing(self):
        # script_plan（normalize）与 prompt_authoring 一样无条件注入节奏建议，二者共享同一份 render_pacing_section("drama")
        assert self._squash(render_pacing_section("drama")) in self._squash(self._normalize_prompt("novel"))


class TestOverviewPrompt:
    """source_kind=screenplay 下 overview prompt 翻为「提取优先」：作者写下的创作方案前言优先照用、
    缺失才退回从正文归纳。只断言语义关键词在场/缺席与分支路由，不锁逐字措辞、不测 LLM 提取质量。"""

    def test_novel_default_keeps_source_text(self):
        prompt = build_overview_prompt("正文内容", source_kind="novel")
        assert "正文内容" in prompt

    def test_screenplay_keeps_source_text(self):
        prompt = build_overview_prompt("剧本正文", source_kind="screenplay")
        assert "剧本正文" in prompt

    def test_screenplay_differs_from_novel(self):
        content = "同一段源文本"
        assert build_overview_prompt(content, source_kind="screenplay") != build_overview_prompt(
            content, source_kind="novel"
        )

    def test_unknown_source_kind_falls_back_to_novel(self):
        content = "源文本"
        assert build_overview_prompt(content, source_kind="bogus") == build_overview_prompt(
            content, source_kind="novel"
        )

    def test_default_source_kind_is_novel(self):
        content = "源文本"
        assert build_overview_prompt(content) == build_overview_prompt(content, source_kind="novel")


class TestDramaDurationSpeechLowerBound:
    """drama script_plan 时长指引的「台词口播时长」单向下界软指引（生成期，纯 prompt 软约束）。

    语速从 lib.speech_rate 单一真相源按项目 source_language 注入；drama prompt 内不写死语速数字。
    单向：画面 / 留白可把时长撑长，台词永不把时长压短；空 utterances 无下界、行为同今日。
    narration / ad / prompt_authoring 视觉层不受影响。只断言语义关键词与注入值，不锁逐字措辞。
    """

    _SPEECH_MARKER = "口播语速约"

    def _normalize(self, **overrides) -> str:
        kwargs = dict(
            novel_text="【第1集】角色甲：「你好」",
            project_overview={"synopsis": "S", "genre": "G", "theme": "T", "world_setting": "W"},
            style="动漫",
            characters={"角色甲": {}},
            scenes={},
            props={},
            default_duration=8,
            supported_durations=[4, 6, 8],
            episode=1,
        )
        kwargs.update(overrides)
        return build_normalize_prompt(**kwargs)

    def test_speech_rate_injected_from_single_source(self):
        # 语速数字来自 lib.speech_rate 单一真相源，按 source_language 取；zh 计字、en / vi 计词
        for lang in ("zh", "en", "vi"):
            rate = speech_rate_units_per_second(lang)
            assert f"{rate:g}" in self._normalize(source_language=lang)
        assert "字/秒" in self._normalize(source_language="zh")
        assert "词/秒" in self._normalize(source_language="en")
        assert "词/秒" in self._normalize(source_language="vi")

    def test_no_hardcoded_speech_rate_number(self):
        # 语速随语言变化 → 证明是注入而非写死；zh（字/秒）与 en（词/秒）语速不同则两处数字不同
        zh_rate = speech_rate_units_per_second("zh")
        en_rate = speech_rate_units_per_second("en")
        assert zh_rate != en_rate  # 前置：两语言语速确实不同
        assert f"{zh_rate:g} 字/秒" in self._normalize(source_language="zh")
        assert f"{en_rate:g} 词/秒" in self._normalize(source_language="en")

    def test_missing_source_language_falls_back_to_default_rate(self):
        # source_language 缺省 → 回退默认语速（speech_rate 单一真相源同口径），向后兼容
        default_rate = speech_rate_units_per_second(None)
        assert f"{default_rate:g}" in self._normalize()

    def test_non_string_source_language_falls_back_to_default_rate(self):
        # source_language 为非字符串脏数据（project.json 类型未强校验）→ 回退默认语速不崩溃，
        # 与保存期上界 warning 同口径守卫；回退 None 走 zh 计字口径
        default_rate = speech_rate_units_per_second(None)
        for dirty in (5, ["zh"]):
            assert f"{default_rate:g} 字/秒" in self._normalize(source_language=dirty)

    def test_project_override_wins_over_language_default(self):
        # 项目级语速覆盖生效时，注入 prompt 的是覆盖值而非语言默认；量词仍随语言
        assert "7.5 字/秒" in self._normalize(source_language="zh", speech_rate_override=7.5)
        assert "7.5 词/秒" in self._normalize(source_language="en", speech_rate_override=7.5)

    def test_narration_and_prompt_authoring_drama_have_no_speech_lower_bound(self):
        # 生成期时长下界只在 drama script_plan（normalize）；narration prompt_authoring 与 drama prompt_authoring 视觉层不含
        narration = build_narration_prompt(
            project_overview={"synopsis": "S", "genre": "G", "theme": "T", "world_setting": "W"},
            style="古风",
            style_description="cinematic",
            characters={"角色甲": {}},
            scenes={},
            props={},
            script_plan_segments=[
                {"segment_id": "E1S01", "novel_text": "原文", "duration_seconds": 4, "segment_break": False}
            ],
            episode=1,
        )
        assert self._SPEECH_MARKER not in narration
        drama_prompt_authoring = build_drama_prompt(
            project_overview={"synopsis": "S", "genre": "G", "theme": "T", "world_setting": "W"},
            style="动漫",
            style_description="cinematic",
            scenes_content="### E1S01（时长 4 秒）",
            episode=1,
        )
        assert self._SPEECH_MARKER not in drama_prompt_authoring

    def test_ad_prompt_unaffected(self):
        # 广告脚本规划走字数→时长折算，不注入台词标记。
        ad = build_ad_prompt(
            project_overview={"synopsis": "S", "genre": "G", "theme": "T", "world_setting": "W"},
            style="动漫",
            style_description="cinematic",
            characters={"角色甲": {}},
            scenes={},
            props={},
            products={},
            brief="卖点",
            target_duration=30,
            generation_mode="image",
            supported_durations=[4, 6, 8],
        )
        assert self._SPEECH_MARKER not in ad


class TestPromptAuthoringPromptGuards:
    """prompt_authoring（视觉层）prompt 骨架守卫：节奏建议始终注入、schema 枚举不重复列举、
    无字数硬限制、episode 约束在场且 scene_id 对齐要求不施加固定格式。"""

    @staticmethod
    def _squash(text: str) -> str:
        """去除全部空白字符，用于跨缩进比较。"""
        return "".join(text.split())

    def _narration_prompt(self) -> str:
        return build_narration_prompt(
            project_overview={"synopsis": "S", "genre": "G", "theme": "T", "world_setting": "W"},
            style="动漫",
            style_description="日漫半厚涂",
            characters={"主角": {"description": "X"}},
            scenes={"庙宇": {"description": "Y"}},
            props={"玉佩": {"description": "Z"}},
            script_plan_segments=[
                {"segment_id": "E2S01", "novel_text": "原文", "duration_seconds": 4, "segment_break": False}
            ],
            episode=2,
        )

    def _drama_prompt(self) -> str:
        return build_drama_prompt(
            project_overview={"synopsis": "S", "genre": "G", "theme": "T", "world_setting": "W"},
            style="动漫",
            style_description="日漫半厚涂",
            scenes_content="### E2S01（时长 4 秒）\n视觉改编：xxx",
            episode=2,
        )

    def test_drama_prompt_injects_pacing(self):
        assert self._squash(render_pacing_section("drama")) in self._squash(self._drama_prompt())

    def test_narration_prompt_injects_pacing(self):
        assert self._squash(render_pacing_section("narration")) in self._squash(self._narration_prompt())

    def test_drama_no_enum_dump_in_prompt(self):
        """schema 已声明的枚举不再在 prompt 中重复列举（节省 token + 防漂移）。"""
        text = self._drama_prompt()
        assert "Tracking Shot" not in text
        assert "Pan Left, Pan Right" not in text

    def test_drama_no_hard_char_limit(self):
        """LLM 无法精确数字数，prompt 不写硬性字数上限。"""
        text = self._drama_prompt()
        assert "200 字以内" not in text
        assert "150 字以内" not in text

    def test_drama_injects_episode_constraints(self):
        """drama prompt 必须明确告知 LLM 当前 episode，避免 ID 跨集污染。"""
        text = self._drama_prompt()
        assert "E2S" in text

    def test_drama_prompt_authoring_scene_id_preserves_edit_suffix_no_fixed_format(self):
        """prompt_authoring 视觉层 scene_id 须逐字保留 script_plan 原 ID（含拆分/编辑后缀如 E2S02_1）；
        不得施加 E{集}S{两位序号} 固定格式约束——模型若去掉后缀，merge 按精确串对齐会整集失败。"""
        text = self._drama_prompt()
        assert "逐字等于" in text
        assert "后缀" in text
        assert "两位序号" not in text

    def test_narration_injects_episode_constraints(self):
        """narration prompt 须告知 episode；script_plan 已分配 E{N}S 前缀，prompt 渲染该 segment_id 并要求逐字对齐。"""
        text = self._narration_prompt()
        assert "E2S" in text

    def test_narration_injects_asset_appearance(self):
        """prompt_authoring 资产块携带外观描述并声明取材口径，视觉字段写细节时从登记描述取材、不自行发明。"""
        text = self._narration_prompt()
        assert "- 主角：X" in text
        assert "- 庙宇：Y" in text
        assert "- 玉佩：Z" in text

    def test_drama_injects_asset_appearance_when_provided(self):
        """drama prompt_authoring 传入资产 bucket 时渲染外观词典；缺描述的资产退化为纯名字。"""
        text = build_drama_prompt(
            project_overview={"synopsis": "S", "genre": "G", "theme": "T", "world_setting": "W"},
            style="动漫",
            style_description="日漫半厚涂",
            scenes_content="### E2S01（时长 4 秒）\n视觉改编：xxx",
            episode=2,
            characters={"主角": {"description": "X"}},
            scenes={"庙宇": {"description": "Y"}},
            props={"玉佩": {}},
        )
        assert "- 主角：X" in text
        assert "- 庙宇：Y" in text
        assert "- 玉佩" in text

    @pytest.mark.parametrize("builder", ["drama", "narration", "ad"])
    def test_prompt_authoring_warns_off_task_type_trigger_words(self, builder: str):
        """三条 prompt_authoring 路径都须把任务类型触发词避讳带给真正落笔 video_prompt 的文案模型。

        编排层 CLAUDE.*.md 里的同名约束进不了这次调用，故此处锁的是 prompt 正文本身。
        """
        if builder == "drama":
            text = self._drama_prompt()
        elif builder == "narration":
            text = self._narration_prompt()
        else:
            text = build_ad_prompt(
                project_overview={"synopsis": "S", "genre": "G", "theme": "T", "world_setting": "W"},
                style="动漫",
                style_description="cinematic",
                characters={"角色甲": {}},
                scenes={},
                props={},
                products={},
                brief="卖点",
                target_duration=30,
                generation_mode="image",
                supported_durations=[4, 6, 8],
            )
        assert "任务类型触发词" in text
        assert "改成" in text and "延长" in text

    def test_drama_omits_asset_block_without_assets(self):
        """兼容旧调用：不传资产参数时 drama prompt_authoring 不渲染资产块与取材注记。"""
        text = self._drama_prompt()
        assert "<characters>" not in text
        assert "资产外观以上述描述为准" not in text


class TestBuildNarrationSplitPrompt:
    """script_plan 说书分镜拆分 prompt（源文 → 结构化分镜表）。"""

    def _prompt(self, **overrides):
        kwargs = dict(
            novel_text="张三走向村口，久久凝望。",
            project_overview={"synopsis": "S", "genre": "G", "theme": "T", "world_setting": "W"},
            characters={"张三": {"description": "主角"}},
            scenes={"村口": {"description": "黄昏村口"}},
            props={},
            default_duration=4,
            supported_durations=[4, 6, 8],
            episode=1,
        )
        kwargs.update(overrides)
        return build_narration_split_prompt(**kwargs)

    def test_injects_episode_prefix_assets_and_durations(self):
        text = self._prompt()
        assert "E1S" in text
        assert "张三" in text
        assert "村口" in text
        # 档位与默认偏好进 prompt
        assert "4, 6, 8" in text
        assert "默认取 4 秒" in text

    def test_mirrors_narration_pacing_rules(self):
        text = self._prompt()
        assert render_pacing_section("narration")[:40] in text

    def test_drifted_default_treated_as_null_not_raised(self):
        """default 漂移到 supported_durations 之外时按 null 处理、不 fail-loud（软偏好口径）。"""
        assert self._prompt(default_duration=5)

    def test_empty_supported_durations_raises(self):
        import pytest

        with pytest.raises(ValueError):
            self._prompt(supported_durations=[])

    def test_novel_text_verbatim_instruction(self):
        text = self._prompt()
        assert "张三走向村口，久久凝望。" in text
