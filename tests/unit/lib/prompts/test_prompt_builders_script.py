import pytest

from lib.prompts.prompt_builders_ad import build_ad_prompt
from lib.prompts.prompt_builders_script import (
    build_drama_prompt,
    build_narration_prompt,
    build_narration_split_prompt,
    build_normalize_prompt,
    build_overview_prompt,
    render_drama_content_for_prompt_authoring,
)
from lib.speech.speech_rate import speech_rate_units_per_second


class TestPromptBuildersScript:
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
        # Render script_plan read-only context: segment_id, verbatim novel_text, duration, scene changes, assets.
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
        # Indent multiline novel_text continuations two spaces into source blocks, avoiding flush-left escape from storyboard structure.
        assert "Source: 第一行。\n  第二行。" in prompt

    def test_build_narration_prompt_forbids_dialogue(self):
        """Narration segments contain no dialogue (ADR 0089); narrators relay character speech in bodies, and prompts no longer request dialogue."""
        prompt = build_narration_prompt(
            project_overview={"synopsis": "故事", "genre": "悬疑", "theme": "真相", "world_setting": "古代"},
            style="古风",
            style_description="cinematic",
            characters={"姜月茴": {}},
            scenes={},
            props={},
            script_plan_segments=[
                {
                    "segment_id": "E1S01",
                    "novel_text": "姜月茴说：“等等。”",
                    "duration_seconds": 4,
                    "segment_break": False,
                    "characters_in_segment": ["姜月茴"],
                }
            ],
            aspect_ratio="9:16",
            episode=1,
        )
        assert "video_prompt.dialogue" not in prompt
        assert "Do not write dialogue" in prompt
        assert "retold" in prompt

    def _drama_prompt_authoring_prompt(self, **overrides) -> str:
        """Visual drama prompt_authoring: content finalized in script_plan, accepting rendered content blocks only."""
        kwargs = {
            "project_overview": {"synopsis": "动作", "genre": "动作", "theme": "成长", "world_setting": "近未来"},
            "style": "赛博",
            "style_description": "high contrast",
            "scenes_content": "### E1S01（时长 8 秒）\n视觉改编：天台追逐",
            "episode": 1,
            "aspect_ratio": "16:9",
        }
        kwargs.update(overrides)
        return build_drama_prompt(**kwargs)

    def test_build_drama_prompt_aspect_ratio_vertical(self):
        assert "9:16" in self._drama_prompt_authoring_prompt(aspect_ratio="9:16")

    def test_build_drama_prompt_aspect_ratio_landscape(self):
        assert "16:9" in self._drama_prompt_authoring_prompt(aspect_ratio="16:9")

    def test_no_enum_listing(self):
        """Never repeat schema-declared enum lists in prompts."""
        prompt = self._drama_prompt_authoring_prompt()
        assert "Tracking Shot" not in prompt
        assert "Pan Left, Pan Right" not in prompt
        assert "Over-the-shoulder" not in prompt

    def test_drama_prompt_authoring_is_visual_only(self):
        """prompt_authoring fills visuals only: image_prompt/video_prompt guidance plus rendered content, no new speech/assets/durations."""
        prompt = self._drama_prompt_authoring_prompt()
        assert "image_prompt" in prompt
        assert "video_prompt" in prompt
        # Pass finalized blocks into prompts for understanding only, never copying.
        assert "天台追逐" in prompt
        # prompt_authoring no longer produces speech; omit the 口播序列（utterances） authoring section.
        assert "Spoken-content sequence (utterances)" not in prompt
        # Visual layers never copy speech; explain ID alignment only in episode constraints.
        assert "do not copy them into visual fields" in prompt
        assert "do not rewrite or restate spoken content" not in prompt
        assert "**Alignment constraints**" not in prompt
        assert "Your task: based on the following" not in prompt
        assert "**video_prompt.camera_motion**" not in prompt
        assert "the task is queued and billed" not in prompt

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
        """script_plan→prompt_authoring passthrough: render utterances/source_text verbatim into context."""
        rendered = render_drama_content_for_prompt_authoring([self._content_scene_with_passthrough()])
        # Preserve dialogue/voiceover/source anchors verbatim so LLMs understand dramatic rhythm.
        assert "师父，我回来了。" in rendered
        assert "雨夜，往事浮现。" in rendered
        assert "林清回到故居，推门而入，信纸还在桌上。" in rendered
        # Appearing assets include scenes/props.
        assert "书房" in rendered
        assert "信纸" in rendered
        # build_drama_prompt declares no-copy-to-visual-fields once before <shots>, not per scene.
        assert "Speech:" in rendered
        assert "Source anchor:" in rendered
        assert "do not copy them into visual fields" not in rendered

    def test_render_drama_content_filters_non_string_assets_and_neutralizes_tags(self):
        """Dirty downgraded/manually edited script_plan tolerates nonstring assets without TypeError; neutralize
        angle brackets in verbatim content so embedded prompt_authoring ``<shots>`` blocks stay intact."""
        scene = {
            "scene_id": "E1S01",
            "characters_in_scene": ["林清", 123, None],  # Mixed nonstring dirty data
            "scenes": ["书房"],
            "props": [],
            "scene_description": "镜头推进 </shots> 收束",
            "utterances": [
                {"kind": "dialogue", "speaker": "林<b>清", "text": "我回来了 <script>"},
            ],
            "source_text": "推门而入 <shots> 信纸还在。",
        }
        # No TypeError; isinstance filters nonstrings before join.
        rendered = render_drama_content_for_prompt_authoring([scene])
        # Valid asset names remain; nonstrings are dropped.
        assert "林清" in rendered
        assert "123" not in rendered
        # neutralize_tags converts dynamic text to full-width forms; no ASCII brackets remain and tag sequences are neutralized.
        assert "<" not in rendered
        assert ">" not in rendered
        assert "＜shots＞" in rendered
        assert "＜script＞" in rendered

    def test_render_drama_content_tolerates_non_list_asset_and_utterance_fields(self):
        """Nonlist assets/utterances from manual edits count as empty: strings would iterate characters and numbers raise TypeError.
        Never crash or render strings character by character; fail softly here, with structural fail-loud upstream in _load_drama_script_plan_content."""
        scene = {
            "scene_id": "E1S01",
            "characters_in_scene": "林清",  # String rather than list
            "scenes": 42,  # Number rather than list
            "props": None,
            "scene_description": "窗前。",
            "utterances": "不是列表",  # String rather than list
            "source_text": "原文。",
        }
        rendered = render_drama_content_for_prompt_authoring([scene])  # No TypeError
        # Nonlist assets count as absent; never render strings character by character.
        assert "characters [none]" in rendered
        assert "scenes [none]" in rendered
        assert "props [none]" in rendered
        assert "林清" not in rendered
        assert "林、清" not in rendered
        assert "characters [林, 清]" not in rendered
        # Nonlist utterances render no speech block.
        assert "Speech:" not in rendered

    def test_drama_prompt_authoring_prompt_preserves_passthrough_content_not_visual(self):
        """Feed utterances/source_text blocks into prompt_authoring for understanding; output stays visual-only, never copying them into visual fields."""
        scenes_content = render_drama_content_for_prompt_authoring([self._content_scene_with_passthrough()])
        prompt = self._drama_prompt_authoring_prompt(scenes_content=scenes_content)
        # Pass speech/source anchors into prompts for dramatic-rhythm understanding.
        assert "师父，我回来了。" in prompt
        assert "林清回到故居，推门而入，信纸还在桌上。" in prompt
        # Prompts declare no-copy-to-visual-fields once before <shots>, preventing speech/source transfer into visuals.
        assert "do not copy them into visual fields" in prompt
        # Output remains visual-only.
        assert "image_prompt" in prompt
        assert "video_prompt" in prompt
        assert "Do not add / delete / reorder shots or change their content" in prompt


class TestScreenplaySourceKind:
    """source_kind branches in script_plan normalize: novels adapt with context-allowed voiceovers; screenplays extract verbatim.

    Drama prompt_authoring visuals never branch on source_kind; speech extraction belongs to
    script_plan, so build_drama_prompt takes no source_kind. Assert semantic keyword presence/
    absence only, neither exact wording nor LLM extraction quality.
    """

    @staticmethod
    def _squash(text: str) -> str:
        """Remove all whitespace for cross-indentation comparisons."""
        return "".join(text.split())

    def _normalize_prompt(self, source_kind: str, **overrides) -> str:
        kwargs = {
            "novel_text": "【第1集】角色甲：「你好」",
            "project_overview": {"synopsis": "S", "genre": "G", "theme": "T", "world_setting": "W"},
            "style": "动漫",
            "characters": {"角色甲": {}},
            "scenes": {},
            "props": {},
            "default_duration": 8,
            "supported_durations": [4, 6, 8],
            "episode": 1,
            "source_kind": source_kind,
        }
        kwargs.update(overrides)
        return build_normalize_prompt(**kwargs)

    def test_normalize_novel_default_keeps_adaptation_semantics(self):
        prompt = self._normalize_prompt("novel")
        # Default novels retain adaptation semantics; ordered utterances carry speech, source_text carries verbatim anchors.
        assert "adapt" in prompt
        assert "original novel" in prompt
        assert "characters_in_scene" in prompt
        assert "utterances" in prompt
        assert "source_text" in prompt

    def test_normalize_novel_releases_voiceover_by_context(self):
        # Novel voiceovers are cautiously context-driven: neither universally forbidden, predefined by whitelists, nor fallback content.
        prompt = self._normalize_prompt("novel")
        assert "off-screen speech" in prompt
        assert "context" in prompt
        # Remove the old prohibition on voiceover output.
        assert "do not produce voiceover" not in prompt

    def test_normalize_screenplay_flips_to_extract_first(self):
        prompt = self._normalize_prompt("screenplay")
        # Extraction/verbatim semantics exist: screenplay source feeds utterances/source_text, without adaptation.
        assert "extract" in prompt
        assert "verbatim" in prompt
        assert "off-screen speech" in prompt
        assert "<screenplay>\n【第1集】角色甲：「你好」\n</screenplay>" in prompt
        assert "utterances" in prompt
        assert "source_text" in prompt
        assert "adapt" not in prompt

    def test_normalize_screenplay_language_rule_exempts_verbatim_fields(self):
        # Exempt verbatim fields/asset references from target-language requirements to avoid extraction conflicts or registered-name mismatches.
        screenplay = self._normalize_prompt("screenplay")
        # Screenplays exempt dialogue text, speaker, and source_text verbatim.
        assert "without translation" in screenplay
        assert "utterances[].speaker" in screenplay
        assert "utterances[].text" in screenplay
        assert "source_text" in screenplay
        # Asset-reference fields characters_in_scene/scenes/props undergo exact-set validation and need exemptions too.
        assert "characters_in_scene[]" in screenplay

    def test_normalize_novel_exempts_speaker_and_source_text_not_dialogue_text(self):
        # speaker references assets and must equal registered characters_in_scene names; translating it breaks subtitle/TTS mapping,
        # so novels also exempt speaker, but adapt dialogue text into target languages rather than extracting verbatim.
        novel = self._normalize_prompt("novel")
        assert "utterances[].speaker" in novel
        assert "source_text" in novel
        # Key distinction: novels do not preserve dialogue text verbatim; only screenplays exempt utterances[].text.
        assert "utterances[].text" not in novel

    def test_normalize_includes_episode_outline_when_present(self):
        # Episode outline story beats/hooks drive script_plan content coverage and final-scene implementation.
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

    def test_normalize_source_block_has_one_heading_and_kind_tag(self):
        for source_kind, other in (("novel", "screenplay"), ("screenplay", "novel")):
            prompt = self._normalize_prompt(source_kind)
            assert f"## Source text\n\n<{source_kind}>\n【第1集】角色甲：「你好」\n</{source_kind}>" in prompt
            assert f"<{other}>" not in prompt
            assert "## Novel source" not in prompt
            assert "## Screenplay source" not in prompt

    def test_normalize_segment_break_rule_for_both_source_kinds(self):
        novel = self._normalize_prompt("novel")
        screenplay = self._normalize_prompt("screenplay")
        assert (
            "\n- **segment_break**: Use your judgment during adaptation: mark the first shot after a location / time jump or scene change “是”"
            in novel
        )
        assert "\n- **segment_break**: Retain the screenplay’s scene divisions / scene changes" in screenplay
        assert "do not fragment the author’s scenes again" not in novel

    def test_normalize_duration_rule_splits_into_three_sub_items(self):
        prompt = self._normalize_prompt("novel", episode_target_duration=90)
        section = prompt.split("- **duration_seconds**:\n", 1)[1].split("\n- **segment_break**", 1)[0]
        lines = section.split("\n")
        assert [line.split(":", 1)[0] for line in lines] == [
            "  - Tiers",
            "  - Spoken-content lower bound",
            "  - Episode target",
        ]
        assert "default 8 seconds" in lines[0]
        assert "not below" in lines[1]
        assert "The target finished duration of this episode is approximately 90 seconds" in lines[2]
        assert "a separate notice will appear on save" not in prompt

        without_target = self._normalize_prompt("novel")
        assert "  - Episode target:" not in without_target
        assert "  - Spoken-content lower bound:" in without_target

    def test_normalize_output_language_keeps_main_clause_for_both_source_kinds(self):
        for source_kind in ("novel", "screenplay"):
            prompt = self._normalize_prompt(source_kind, target_language="English")
            assert (
                "**Output language**: natural-language string values must use English; keep JSON keys / enum values in English. Exceptions (preserve"
                in prompt
            )

    def test_normalize_mentions_extras_exclusion_once(self):
        for source_kind in ("novel", "screenplay"):
            prompt = self._normalize_prompt(source_kind)
            assert prompt.count("old man A") == 1
            assert "do not register as character assets" not in prompt
        assert "whether to produce it is your creative judgment based on context" not in self._normalize_prompt("novel")

    def test_normalize_lists_every_asset_type_when_multiple_are_empty(self):
        prompt = self._normalize_prompt(
            "novel",
            characters={"姜月茴": {}},
            scenes={},
            props={},
        )
        assert "<characters>\n- 姜月茴\n</characters>" in prompt
        assert "<scenes>\n(none yet)\n</scenes>" in prompt
        assert "<props>\n(none yet)\n</props>" in prompt
        assert "  - Candidate scenes: [(none yet)]" in prompt
        assert "  - Candidate props: [(none yet)]" in prompt

    def test_normalize_renders_null_overview_fields_as_blank(self):
        prompt = self._normalize_prompt(
            "novel",
            project_overview={"synopsis": None, "genre": None, "theme": "真相", "world_setting": None},
        )
        assert "<overview>\n\nGenre: \nCore theme: 真相\nWorld setting: \n</overview>" in prompt

    def test_narration_prompts_keep_every_empty_asset_block(self):
        empty_assets = {"characters": {}, "scenes": {}, "props": {}}
        overview = {"synopsis": "S", "genre": "G", "theme": "T", "world_setting": "W"}
        prompts = [
            build_narration_prompt(
                project_overview=overview,
                style="古风",
                style_description="",
                script_plan_segments=[],
                episode=1,
                **empty_assets,
            ),
            build_narration_split_prompt(
                novel_text="原文",
                project_overview=overview,
                default_duration=None,
                supported_durations=[4],
                episode=1,
                **empty_assets,
            ),
        ]
        for prompt in prompts:
            for tag in ("characters", "scenes", "props"):
                assert f"<{tag}>\n(none yet)\n</{tag}>" in prompt

    def test_normalize_omits_blank_or_non_text_outline_fields(self):
        prompt = self._normalize_prompt(
            "novel",
            episode_outline={"title": 42, "story_beats": ["  ", "踏进祖宅"], "hook": "  ", "next_episode_teaser": None},
        )
        assert (
            "<episode_outline>\nThis episode’s outline (designed during episode planning; the script must cover every story beat):\nStory beats:\n- 踏进祖宅\n</episode_outline>"
            in prompt
        )
        assert "The final scene (the last shot or shots)" not in prompt
        blank = {"title": " ", "story_beats": [""], "hook": " ", "next_episode_teaser": " "}
        assert self._normalize_prompt("novel", episode_outline=blank) == self._normalize_prompt("novel")

    def test_normalize_renders_both_outlines_with_their_beats(self):
        prompt = self._normalize_prompt(
            "novel",
            episode_outline={"title": "归家", "story_beats": ["踏进祖宅"], "hook": "信纸落下"},
            next_episode_outline={"title": "族谱", "story_beats": ["族谱缺页"]},
        )
        assert (
            "<episode_outline>\nThis episode’s outline (designed during episode planning; the script must cover every story beat):\nThis episode’s title: 归家\n"
            "Story beats:\n- 踏进祖宅\nEpisode-end hook: 信纸落下\n</episode_outline>"
        ) in prompt
        assert (
            "The final scene (the last shot or shots) must actually depict the dramatic content of the episode-end hook in visuals and dialogue"
            in prompt
        )
        assert (
            "<next_episode_outline>\nNext episode’s outline (only for designing the connection at this episode’s end; do not bring next episode’s plot into this episode):\n"
            "Next episode’s title: 族谱\nStory beats:\n- 族谱缺页\n</next_episode_outline>"
        ) in prompt

    def test_normalize_omits_hook_landing_without_hook_or_teaser(self):
        prompt = self._normalize_prompt("novel", episode_outline={"title": "归家", "story_beats": ["踏进祖宅"]})
        assert "<episode_outline>" in prompt
        assert "The final scene (the last shot or shots)" not in prompt

    def test_normalize_appends_instructions_after_single_blank_line(self):
        for source_kind in ("novel", "screenplay"):
            prompt = self._normalize_prompt(source_kind, instructions="多用近景。\n少用旁白。")
            assert prompt.endswith("empty for `merge` and `skip`.\n\n# Additional instructions\n多用近景。\n少用旁白。")
            plain = self._normalize_prompt(source_kind)
            assert "# Additional instructions" not in plain
            assert plain == self._normalize_prompt(source_kind, instructions="")

    def test_normalize_injects_pacing(self):
        # script_plan always provides opening-rhythm guidance.
        assert "Thefirst~4secondsserveasahook" in self._squash(self._normalize_prompt("novel"))


class TestOverviewPrompt:
    """Screenplay overview prompts prioritize extraction: retain author-written creative-plan introductions first,
    summarizing bodies only if absent. Assert semantic presence/absence and routing, not exact wording or LLM quality."""

    def test_novel_default_keeps_source_text(self):
        prompt = build_overview_prompt("正文内容", source_kind="novel")
        assert "正文内容" in prompt

    def test_screenplay_keeps_source_text(self):
        prompt = build_overview_prompt("剧本正文", source_kind="screenplay")
        assert "剧本正文" in prompt

    def test_prompt_has_task_language_and_source_in_order(self):
        prompt = build_overview_prompt("正文内容", source_kind="novel", target_language="English")
        assert prompt == (
            "Analyze the following novel content and extract key information:\n\n"
            "**Output language**: all string values must use English; keep JSON keys / enum values in English.\n\n"
            "正文内容"
        )

    def test_screenplay_prefers_author_creative_plan(self):
        prompt = build_overview_prompt("剧本正文", source_kind="screenplay")
        assert prompt.startswith("Analyze the following finished screenplay and derive a project overview")
        assert "prioritize the author’s existing premises" in prompt
        assert "Analyze the following novel content" not in prompt

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
    """Drama script_plan dialogue-duration guidance supplies a one-way soft lower bound during generation.

    Inject rates from lib.speech.speech_rate by source_language, never hardcode them in drama
    prompts. One-way means visuals/pauses may lengthen scenes, never shorten them due to dialogue;
    empty utterances have no lower bound and keep current behavior. Narration/ad/visual
    prompt_authoring are unaffected. Assert semantic keywords/injected values, not exact wording.
    """

    _SPEECH_MARKER = "Spoken-content lower bound:"

    def _normalize(self, **overrides) -> str:
        kwargs = {
            "novel_text": "【第1集】角色甲：「你好」",
            "project_overview": {"synopsis": "S", "genre": "G", "theme": "T", "world_setting": "W"},
            "style": "动漫",
            "characters": {"角色甲": {}},
            "scenes": {},
            "props": {},
            "default_duration": 8,
            "supported_durations": [4, 6, 8],
            "episode": 1,
        }
        kwargs.update(overrides)
        return build_normalize_prompt(**kwargs)

    def test_speech_rate_injected_from_single_source(self):
        # Rates come solely from lib.speech.speech_rate by source_language: zh characters, en/vi words.
        assert self._SPEECH_MARKER in self._normalize()
        for lang in ("zh", "en", "vi"):
            rate = speech_rate_units_per_second(lang)
            assert f"{rate:g}" in self._normalize(source_language=lang)
        assert "字/second" in self._normalize(source_language="zh")
        assert "词/second" in self._normalize(source_language="en")
        assert "词/second" in self._normalize(source_language="vi")

    def test_no_hardcoded_speech_rate_number(self):
        # Language-varying rates prove injection, not hardcoding; different zh characters/second and en words/second yield different numbers.
        zh_rate = speech_rate_units_per_second("zh")
        en_rate = speech_rate_units_per_second("en")
        assert zh_rate != en_rate  # Precondition: both language rates really differ.
        assert f"{zh_rate:g} 字/second" in self._normalize(source_language="zh")
        assert f"{en_rate:g} 词/second" in self._normalize(source_language="en")

    def test_missing_source_language_falls_back_to_default_rate(self):
        # Missing source_language uses default rates from the same source, preserving backward compatibility.
        default_rate = speech_rate_units_per_second(None)
        assert f"{default_rate:g}" in self._normalize()

    def test_non_string_source_language_falls_back_to_default_rate(self):
        # Nonstring dirty source_language (project.json not strictly typed) uses defaults without crashing,
        # matching save-time upper-bound warnings; None falls back to zh character units.
        default_rate = speech_rate_units_per_second(None)
        for dirty in (5, ["zh"]):
            assert f"{default_rate:g} 字/second" in self._normalize(source_language=dirty)

    def test_project_override_wins_over_language_default(self):
        # Effective project overrides inject overridden rates, not defaults; units still follow language.
        assert "7.5 字/second" in self._normalize(source_language="zh", speech_rate_override=7.5)
        assert "7.5 词/second" in self._normalize(source_language="en", speech_rate_override=7.5)

    def test_narration_and_prompt_authoring_drama_have_no_speech_lower_bound(self):
        # Generation lower bounds apply only to drama script_plan normalize, not narration/drama visual prompt_authoring.
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
        # Ad script planning converts text counts into durations, without dialogue markers.
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
    """Visual prompt_authoring skeleton guards: always-injected rhythm guidance, no repeated schema enums,
    no hard text-count cap, episode constraints present, and no fixed scene_id alignment format."""

    @staticmethod
    def _squash(text: str) -> str:
        """Remove all whitespace for cross-indentation comparisons."""
        return "".join(text.split())

    def _narration_prompt(self, **overrides) -> str:
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
            **overrides,
        )

    def _drama_prompt(self) -> str:
        return build_drama_prompt(
            project_overview={"synopsis": "S", "genre": "G", "theme": "T", "world_setting": "W"},
            style="动漫",
            style_description="日漫半厚涂",
            scenes_content="### E2S01（时长 4 秒）\n视觉改编：xxx",
            episode=2,
        )

    def test_drama_prompt_injects_pacing(self, monkeypatch, tmp_path):
        monkeypatch.setenv("ARCREEL_PROFILE_DIR", str(tmp_path / "missing-profile"))
        assert "Thefirst~4secondsserveasahook" in self._squash(self._drama_prompt())

    @pytest.mark.parametrize("instructions", [None, "", "末镜保留雨声。"])
    def test_narration_instructions_are_an_optional_section(self, instructions):
        text = self._narration_prompt(instructions=instructions)
        if instructions:
            assert text.endswith("\n\n# Additional instructions\n末镜保留雨声。")
            assert text.count("# Additional instructions") == 1
        else:
            assert "# Additional instructions" not in text
        assert "the task is queued and billed" not in text
        assert "**video_prompt.action**" in text
        assert "None" not in text

    def test_narration_prompt_injects_pacing(self, monkeypatch, tmp_path):
        monkeypatch.setenv("ARCREEL_PROFILE_DIR", str(tmp_path / "missing-profile"))
        assert "Theopeningvisualsegment(thefirst~4secondsofreading)servesthehook" in self._squash(
            self._narration_prompt()
        )

    def test_drama_no_enum_dump_in_prompt(self):
        """Omit repeated schema enum lists from prompts, saving tokens and preventing drift."""
        text = self._drama_prompt()
        assert "Tracking Shot" not in text
        assert "Pan Left, Pan Right" not in text

    def test_drama_no_hard_char_limit(self):
        """LLMs cannot count characters precisely; prompts contain no hard character-count caps."""
        text = self._drama_prompt()
        assert "within 200 characters" not in text
        assert "within 150 characters" not in text

    def test_drama_injects_episode_constraints(self):
        """Drama prompts explicitly identify the current episode to prevent cross-episode ID pollution."""
        text = self._drama_prompt()
        assert "E2S" in text

    def test_drama_prompt_authoring_scene_id_preserves_edit_suffix_no_fixed_format(self):
        """Visual prompt_authoring scene_id preserves exact script_plan IDs, including split/edit suffixes such as E2S02_1.
        Never impose E{集}S{两位序号}; models dropping suffixes would fail whole-episode exact-string merges."""
        text = self._drama_prompt()
        assert "exactly match" in text
        assert "suffixes" in text
        assert "two-digit sequence" not in text

    def test_narration_injects_episode_constraints(self):
        """Narration prompts identify episodes; script_plan already allocated E{N}S prefixes, so render segment_id and require exact alignment."""
        text = self._narration_prompt()
        assert "E2S" in text

    def test_narration_injects_asset_appearance(self):
        """prompt_authoring asset blocks carry appearance descriptions/source rules; visuals draw registered details rather than inventing them."""
        text = self._narration_prompt()
        assert "- 主角: X" in text
        assert "- 庙宇: Y" in text
        assert "- 玉佩: Z" in text

    def test_drama_injects_asset_appearance_when_provided(self):
        """Drama asset buckets render appearance dictionaries; undescribed assets fall back to names only."""
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
        assert "- 主角: X" in text
        assert "- 庙宇: Y" in text
        assert "- 玉佩" in text

    @pytest.mark.parametrize("builder", ["drama", "narration", "ad"])
    def test_prompt_authoring_warns_off_task_type_trigger_words(self, builder: str):
        """All three prompt_authoring routes deliver task-type-trigger avoidance to the text model actually writing video_prompt.

        Orchestration CLAUDE.*.md constraints never reach this invocation, so pin prompt bodies themselves.
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
        assert "task-type trigger words" in text
        assert "change to" in text
        assert "extend" in text

    def test_drama_omits_asset_block_without_assets(self):
        """Legacy calls omitting assets render neither asset blocks nor sourcing notes in drama prompt_authoring."""
        text = self._drama_prompt()
        assert "<characters>" not in text
        assert "The descriptions above are authoritative for asset appearance" not in text


class TestBuildNarrationSplitPrompt:
    """Narration script_plan storyboard-splitting prompt: source text to structured storyboard table."""

    def _prompt(self, **overrides):
        kwargs = {
            "novel_text": "张三走向村口，久久凝望。",
            "project_overview": {"synopsis": "S", "genre": "G", "theme": "T", "world_setting": "W"},
            "characters": {"张三": {"description": "主角"}},
            "scenes": {"村口": {"description": "黄昏村口"}},
            "props": {},
            "default_duration": 4,
            "supported_durations": [4, 6, 8],
            "episode": 1,
        }
        kwargs.update(overrides)
        return build_narration_split_prompt(**kwargs)

    def test_injects_episode_prefix_assets_and_durations(self):
        text = self._prompt()
        assert "E1S" in text
        assert "张三" in text
        assert "村口" in text
        # Inject tiers/default preferences into prompts.
        assert "4, 6, 8" in text
        assert "default to 4 seconds" in text

    def test_mirrors_narration_pacing_rules(self, monkeypatch, tmp_path):
        monkeypatch.setenv("ARCREEL_PROFILE_DIR", str(tmp_path / "missing-profile"))
        text = self._prompt()
        assert "Pacing suggestions for narration / commentary:" in text

    def test_drifted_default_treated_as_null_not_raised(self):
        """Defaults outside supported_durations behave as null rather than failing loudly, preserving soft-preference rules."""
        assert self._prompt(default_duration=5)

    def test_empty_supported_durations_raises(self):
        import pytest

        with pytest.raises(ValueError, match=r"supported_durations 不能为空"):
            self._prompt(supported_durations=[])

    def test_novel_text_verbatim_instruction(self):
        text = self._prompt()
        assert "张三走向村口，久久凝望。" in text


#: Character table with derivatives: list `姜月茴/劲装`, whose appearance combines base descriptions and derivative changes.
_CHARACTERS_WITH_DERIVATIVE = {
    "姜月茴": {"description": "青衣少女", "derivatives": {"劲装": {"description": "换上黑色劲装"}}}
}


class TestDerivativeAssetCandidates:
    """Character candidates list `本体/衍生`, combining base descriptions and derivative appearance changes (ADR 0072)."""

    def test_narration_split_prompt_lists_the_derivative_candidate(self):
        prompt = build_narration_split_prompt(
            project_overview={"synopsis": "故事", "genre": "悬疑", "theme": "真相", "world_setting": "古代"},
            novel_text="她推开祠堂的门。",
            characters=_CHARACTERS_WITH_DERIVATIVE,
            scenes={"祠堂": {}},
            props={},
            supported_durations=[8],
            default_duration=None,
            episode=1,
        )

        assert "- 姜月茴/劲装" in prompt
        assert "character: 姜月茴, 姜月茴/劲装" in prompt

    def test_narration_prompt_asset_block_composes_the_derivative_appearance(self):
        prompt = build_narration_prompt(
            project_overview={"synopsis": "故事", "genre": "悬疑", "theme": "真相", "world_setting": "古代"},
            style="古风",
            style_description="cinematic",
            characters=_CHARACTERS_WITH_DERIVATIVE,
            scenes={},
            props={},
            script_plan_segments=[],
            aspect_ratio="9:16",
            episode=1,
        )

        assert "- 姜月茴/劲装: 青衣少女\n  Current appearance: 换上黑色劲装" in prompt
