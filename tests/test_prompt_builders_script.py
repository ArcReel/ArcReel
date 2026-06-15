from lib.prompt_builders_script import (
    _format_names,
    build_drama_prompt,
    build_narration_prompt,
    build_normalize_prompt,
    build_overview_prompt,
)


class TestPromptBuildersScript:
    def test_format_names_emits_bullet_lists(self):
        assert _format_names({"A": {}, "B": {}}) == "- A\n- B"
        assert _format_names({"玉佩": {}, "祠堂": {}}) == "- 玉佩\n- 祠堂"
        assert _format_names({}) == "（暂无）"

    def test_build_narration_prompt_contains_dynamic_durations(self):
        prompt = build_narration_prompt(
            project_overview={"synopsis": "故事", "genre": "悬疑", "theme": "真相", "world_setting": "古代"},
            style="古风",
            style_description="cinematic",
            characters={"姜月茴": {}},
            scenes={"祠堂": {}},
            props={"玉佩": {}},
            segments_md="E1S01 | 文本",
            supported_durations=[4, 6, 8],
            default_duration=4,
            aspect_ratio="9:16",
            episode=1,
        )
        assert "4, 6, 8" in prompt
        assert "默认 4 秒" in prompt
        assert "祠堂" in prompt
        assert "玉佩" in prompt

    def test_build_narration_prompt_auto_duration(self):
        prompt = build_narration_prompt(
            project_overview={"synopsis": "故事", "genre": "悬疑", "theme": "真相", "world_setting": "古代"},
            style="古风",
            style_description="cinematic",
            characters={"姜月茴": {}},
            scenes={},
            props={"玉佩": {}},
            segments_md="E1S01 | 文本",
            supported_durations=[5, 10],
            default_duration=None,
            aspect_ratio="9:16",
            episode=1,
        )
        assert "5, 10" in prompt
        assert "按内容节奏自行决定" in prompt

    def test_build_drama_prompt_aspect_ratio_vertical(self):
        prompt = build_drama_prompt(
            project_overview={"synopsis": "动作", "genre": "动作", "theme": "成长", "world_setting": "近未来"},
            style="赛博",
            style_description="high contrast",
            characters={"林": {}},
            scenes={"天台": {}},
            props={"芯片": {}},
            scenes_md="E1S01 | 追逐",
            supported_durations=[4, 8, 12],
            default_duration=8,
            aspect_ratio="9:16",
            episode=1,
        )
        assert "竖屏构图" in prompt

    def test_build_drama_prompt_aspect_ratio_landscape(self):
        prompt = build_drama_prompt(
            project_overview={"synopsis": "动作", "genre": "动作", "theme": "成长", "world_setting": "近未来"},
            style="赛博",
            style_description="high contrast",
            characters={"林": {}},
            scenes={"天台": {}},
            props={"芯片": {}},
            scenes_md="E1S01 | 追逐",
            supported_durations=[4, 6, 8],
            default_duration=8,
            aspect_ratio="16:9",
            episode=1,
        )
        assert "横屏构图" in prompt

    def test_no_enum_listing(self):
        """schema 已声明枚举不在 prompt 中重复列举。"""
        prompt = build_drama_prompt(
            project_overview={"synopsis": "S", "genre": "G", "theme": "T", "world_setting": "W"},
            style="动漫",
            style_description="",
            characters={"林": {}},
            scenes={"天台": {}},
            props={},
            scenes_md="E1S01 | 追逐",
            supported_durations=[4, 6, 8],
            default_duration=8,
            aspect_ratio="16:9",
            episode=1,
        )
        assert "Tracking Shot" not in prompt
        assert "Pan Left, Pan Right" not in prompt
        assert "Over-the-shoulder" not in prompt


class TestScreenplaySourceKind:
    """source_kind=screenplay 下 step1 normalize / step2 drama 两段 prompt 翻为「提取/逐字保留」。

    只断言语义关键词在场（提取 / 逐字 / 画外音）与「改编」缺席，不锁逐字措辞、不测 LLM 提取质量。
    """

    def _drama_prompt(self, source_kind: str) -> str:
        return build_drama_prompt(
            project_overview={"synopsis": "S", "genre": "G", "theme": "T", "world_setting": "W"},
            style="动漫",
            style_description="",
            characters={"林清": {}},
            scenes={"庭院": {}},
            props={},
            scenes_md="E1S01 | 对峙",
            supported_durations=[4, 6, 8],
            default_duration=8,
            aspect_ratio="16:9",
            episode=1,
            source_kind=source_kind,
        )

    def _normalize_prompt(self, source_kind: str) -> str:
        return build_normalize_prompt(
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

    def test_drama_novel_default_keeps_adaptation_semantics(self):
        prompt = self._drama_prompt("novel")
        # 默认 novel 维持原「改编/创作」语义，dialogue 仍要求 speaker ∈ characters_in_scene
        assert "改编" in prompt
        assert "characters_in_scene" in prompt
        # novel-drama 无画外音轨：voiceover 字段引导要求留空
        assert "voiceover" in prompt

    def test_drama_screenplay_flips_to_verbatim_extraction(self):
        prompt = self._drama_prompt("screenplay")
        # 台词逐字照搬、画外音逐字提取的指令在场
        assert "逐字" in prompt
        assert "画外音" in prompt
        assert "voiceover" in prompt
        # 翻面为「转写而非再创作」，不含「改编」语义
        assert "改编" not in prompt

    def test_drama_screenplay_language_rule_exempts_audible_fields(self):
        # screenplay 下输出语言约束须把台词 / 说话人 / 画外音逐字字段排除在目标语言要求之外，
        # 否则与逐字提取冲突、诱导模型翻译原文；novel 维持无条件目标语言、无此豁免。
        screenplay = self._drama_prompt("screenplay")
        novel = self._drama_prompt("novel")
        assert "不翻译" in screenplay
        assert "不翻译" not in novel
        # speaker 是角色资产引用键，翻译会与登记角色名失配，须一并豁免
        assert "video_prompt.dialogue[].speaker" in screenplay
        assert "video_prompt.dialogue[].speaker" not in novel

    def test_normalize_novel_default_keeps_adaptation_semantics(self):
        prompt = self._normalize_prompt("novel")
        assert "改编" in prompt
        assert "小说原文" in prompt

    def test_normalize_screenplay_flips_to_extract_first(self):
        prompt = self._normalize_prompt("screenplay")
        # 提取/逐字保留语义在场，剧本原文为输入，不含「改编」
        assert "提取" in prompt
        assert "逐字" in prompt
        assert "画外音" in prompt
        assert "剧本原文" in prompt
        assert "改编" not in prompt


class TestOverviewPrompt:
    """source_kind=screenplay 下 overview prompt 翻为「提取优先」：作者写下的创作方案前言优先照用、
    缺失才退回从正文归纳。只断言语义关键词在场/缺席与分支路由，不锁逐字措辞、不测 LLM 提取质量。"""

    def test_novel_default_keeps_source_text_and_novel_framing(self):
        prompt = build_overview_prompt("正文内容", source_kind="novel")
        assert "正文内容" in prompt
        assert "小说" in prompt
        # novel 维持从正文归纳，不引入「创作方案」前言概念
        assert "创作方案" not in prompt

    def test_screenplay_flips_to_preamble_extract_first(self):
        prompt = build_overview_prompt("剧本正文", source_kind="screenplay")
        assert "剧本正文" in prompt
        # 优先识别作者写下的创作方案前言并照用
        assert "创作方案" in prompt
        # 前言缺失则退回从正文归纳
        assert "归纳" in prompt

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
