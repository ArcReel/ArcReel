"""Three-part rendering: A/B/C injection differences, numbering-order contracts, and degradation warnings."""

from __future__ import annotations

import unicodedata
from dataclasses import replace

import pytest

from lib.prompts.prompt_templates.builtin import builtin_templates
from lib.script.reference_video.prompt_render import (
    render_unit_prompt,
    resolve_reference_audio_paths,
)
from lib.script.reference_video.script_preview import (
    WARN_REFERENCE_AUDIO_OVERFLOW,
    WARN_SILENT_EPISODE,
    WARN_SILENT_MODEL,
    WARN_SPEAKER_AUDIO_NEEDS_IMAGE,
    WARN_SPEAKER_AUDIO_UNAVAILABLE,
    WARN_SPEAKER_WITHOUT_AUDIO,
    WARN_UNCLOSED_BRACE,
    WARN_UNREGISTERED_MENTION,
    WARN_UNREGISTERED_SPEAKER,
    build_script_preview,
)
from lib.script.reference_video.voice_settings import VoiceRenderSettings
from lib.script.script_models import ReferenceResource


def _project(**overrides):
    project = {
        "style": "写实电影感",
        "characters": {
            "张三": {"voice_style": "低沉沙哑的男声", "reference_audio": "characters/refs_audio/张三.wav"},
            "李四": {"voice_style": "清亮少女音", "reference_audio": "characters/refs_audio/李四.mp3"},
            "旁白人": {"voice_style": "温和中年男声"},
        },
        "scenes": {"酒馆": {}},
        "props": {"长剑": {}},
    }
    project.update(overrides)
    return project


#: Vietnamese combining-character names: visually identical, byte-distinct encodings test asset-name comparison coordinates.
_NAME_NFC = unicodedata.normalize("NFC", "Hiếu")
_NAME_NFD = unicodedata.normalize("NFD", "Hiếu")


def _refs(*pairs):
    return [ReferenceResource(type=t, name=n) for t, n in pairs]


#: Voice settings for nonvoice cases: ``soft`` has audio without reference clips. Rendering requires ``settings``,
#: so these cases still supply a tier using field defaults.
_SOFT = VoiceRenderSettings()


_TEXT = "\n".join(
    [
        "镜头1：夜色下的 @[酒馆]，@[张三] 推门而入，手按 @[长剑]。",
        "@[张三]：{今晚的酒，我请。}",
        "镜头2：吧台后有人抬头。",
        "@[李四]：{你终于来了。}",
    ]
)


def test_native_tier_binds_audio_in_speaker_first_appearance_order():
    rendered = render_unit_prompt(
        _TEXT,
        _project(),
        _refs(("scene", "酒馆"), ("character", "张三"), ("prop", "长剑")),
        VoiceRenderSettings(voice_consistency="native", max_reference_audio=3, model_id="doubao-seedance-2-0"),
    )
    # Audio order follows request fields and @音频N numbering.
    assert rendered.audio_speakers == ["张三", "李四"]
    assert (
        "<张三>: Dialogue voice reference @音频1; use only timbre, speech rate, and emotion; the body text is authoritative for dialogue; Voice characteristics: 低沉沙哑的男声."
        in rendered.prompt
    )
    assert (
        "<李四>: Dialogue voice reference @音频2; use only timbre, speech rate, and emotion; the body text is authoritative for dialogue; Voice characteristics: 清亮少女音."
        in rendered.prompt
    )


def test_silent_episode_sends_dialogue_without_any_audio_binding():
    """Silent episode: assemble no reference audio or @音频N in prompts; still send dialogue for lip-sync reference."""
    rendered = render_unit_prompt(
        _TEXT,
        _project(),
        _refs(("scene", "酒馆"), ("character", "张三"), ("prop", "长剑")),
        VoiceRenderSettings(
            voice_consistency="native",
            requested_generate_audio=False,
            max_reference_audio=3,
            model_id="doubao-seedance-2-0",
        ),
    )
    assert rendered.audio_speakers == []
    assert rendered.audio_speaker_reference_index == []
    assert "@音频" not in rendered.prompt
    assert "<张三> says {今晚的酒，我请。}" in rendered.prompt
    assert "<李四> says {你终于来了。}" in rendered.prompt


def test_silent_episode_injects_no_voice_style_same_as_silent_model():
    """Episode audio disabled and nonaudio-producing models (class C) agree: neither silent path injects 声音特征：….

    ``voice_style`` describes audible timbre; injecting it into silent video wastes model capacity on unused constraints.
    Dialogue is unaffected, with separate cases comparing the second part exactly.
    """
    refs = _refs(("scene", "酒馆"), ("character", "张三"), ("prop", "长剑"))
    silent_episode = render_unit_prompt(
        _TEXT,
        _project(),
        refs,
        VoiceRenderSettings(voice_consistency="native", requested_generate_audio=False, max_reference_audio=3),
    )
    silent_model = render_unit_prompt(_TEXT, _project(), refs, VoiceRenderSettings(voice_consistency="none"))

    assert "Voice characteristics" not in silent_episode.prompt
    assert "Voice characteristics" not in silent_model.prompt
    # Part one contains only subject bindings; both silent paths are byte-identical.
    assert silent_episode.prompt.split("\n\n")[0] == silent_model.prompt.split("\n\n")[0]


@pytest.mark.parametrize(
    "silencing",
    [
        pytest.param({"requested_generate_audio": False}, id="silent_episode"),
        pytest.param({"voice_consistency": "none"}, id="silent_model"),
    ],
)
def test_silent_paths_keep_the_whole_body_identical_to_the_audible_path(silencing: dict):
    """Part two exactly matches the audible path; only part-one voice-reference lines disappear.

    Compare the whole body, not its first sentence: audio numbering can diverge only from the second speaker onward, so checking
    the opening alone misses later binding differences. Compare each silent path separately.
    """
    settings = VoiceRenderSettings(
        voice_consistency="native",
        max_reference_audio=3,
        model_id="doubao-seedance-2-0",
        audio_ready={"张三", "李四"},
    )
    refs = _refs(("scene", "酒馆"), ("character", "张三"), ("prop", "长剑"))
    audible = render_unit_prompt(_TEXT, _project(), refs, settings)
    silent = render_unit_prompt(_TEXT, _project(), refs, replace(settings, **silencing))

    def _body(prompt: str) -> str:
        return prompt.split("\n\n")[1]

    # The audible side must bind two clips for meaningful comparison; otherwise both lack audio and assertions are trivial.
    assert audible.audio_speakers == ["张三", "李四"]
    assert "你终于来了。" in _body(audible.prompt)
    assert _body(silent.prompt) == _body(audible.prompt)


@pytest.mark.parametrize(
    ("silencing", "expected"),
    [
        pytest.param(
            {"requested_generate_audio": False},
            {"key": WARN_SILENT_EPISODE, "params": {}},
            id="silent_episode",
        ),
        pytest.param(
            {"voice_consistency": "none"},
            {"key": WARN_SILENT_MODEL, "params": {"provider": "", "model": "doubao-seedance-2-0"}},
            id="silent_model",
        ),
    ],
)
def test_silent_preview_and_execution_reach_the_same_conclusion(silencing: dict, expected: dict):
    """The same body yields identical silent conclusions in preview and execution.

    Their ``VoiceRenderSettings`` come from different construction points (preview ``from_caps``, execution per-field);
    divergence would show bound audio in preview but yield a silent final video.
    """
    settings = replace(
        VoiceRenderSettings(
            voice_consistency="native",
            max_reference_audio=3,
            model_id="doubao-seedance-2-0",
        ),
        **silencing,
    )
    refs = _refs(("scene", "酒馆"), ("character", "张三"), ("prop", "长剑"))
    preview = build_script_preview(_TEXT, _project(), settings)
    rendered = render_unit_prompt(_TEXT, _project(), refs, settings)

    assert preview.warnings == [expected]
    assert rendered.warnings == [expected]
    # Match promises to paid execution: if preview says no audible episode, execution must bind no clips.
    assert rendered.audio_speakers == []
    assert "@音频" not in rendered.prompt
    assert [u.text for u in preview.utterances] == ["今晚的酒，我请。", "你终于来了。"]


def test_silent_preview_and_execution_stay_silent_without_any_utterance():
    """Truly silent scenes with no speech markers produce no silence notice on either path."""
    settings = VoiceRenderSettings(voice_consistency="none", model_id="doubao-seedance-2-0")
    text = "镜头1：夜色下的 @[酒馆]，@[张三] 推门而入，手按 @[长剑]。"
    preview = build_script_preview(text, _project(), settings)
    rendered = render_unit_prompt(
        text, _project(), _refs(("scene", "酒馆"), ("character", "张三"), ("prop", "长剑")), settings
    )

    assert preview.warnings == []
    assert rendered.warnings == []


def test_first_segment_binds_images_in_reference_order():
    rendered = render_unit_prompt(
        _TEXT,
        _project(),
        _refs(("scene", "酒馆"), ("character", "张三"), ("prop", "长剑")),
        VoiceRenderSettings(voice_consistency="soft"),
    )
    assert rendered.prompt.startswith("<酒馆>@图片1、<张三>@图片2、<长剑>@图片3。")


def test_speaker_position_never_produces_a_reference_image():
    """李四 appears only as a dialogue speaker: no reference-image binding, but voice declarations and dialogue render normally."""
    rendered = render_unit_prompt(
        _TEXT,
        _project(),
        _refs(("scene", "酒馆"), ("character", "张三"), ("prop", "长剑")),
        VoiceRenderSettings(voice_consistency="native", max_reference_audio=3),
    )
    assert "<李四>@图片" not in rendered.prompt
    assert "<李四>: Dialogue voice reference @音频2" in rendered.prompt
    assert "<李四> says {你终于来了。}" in rendered.prompt


def test_soft_tier_declares_voice_style_without_audio_designation():
    rendered = render_unit_prompt(
        _TEXT,
        _project(),
        _refs(("character", "张三")),
        VoiceRenderSettings(voice_consistency="soft", max_reference_audio=3),
    )
    assert rendered.audio_speakers == []
    assert "@音频" not in rendered.prompt
    assert "<张三>: Voice characteristics: 低沉沙哑的男声." in rendered.prompt
    assert "<李四>: Voice characteristics: 清亮少女音." in rendered.prompt


def test_silent_tier_keeps_dialogue_lines_but_injects_no_voice_declaration():
    rendered = render_unit_prompt(
        _TEXT,
        _project(),
        _refs(("character", "张三")),
        VoiceRenderSettings(voice_consistency="none", model_id="minimax-01"),
    )
    assert "Voice characteristics" not in rendered.prompt
    assert "@音频" not in rendered.prompt
    # Render dialogue normally for lip-sync and performance.
    assert "<张三> says {今晚的酒，我请。}" in rendered.prompt
    assert {"key": WARN_SILENT_MODEL, "params": {"provider": "", "model": "minimax-01"}} in rendered.warnings


def test_reference_audio_overflow_truncates_and_warns():
    rendered = render_unit_prompt(
        _TEXT,
        _project(),
        _refs(("character", "张三")),
        VoiceRenderSettings(voice_consistency="native", max_reference_audio=1),
    )
    assert rendered.audio_speakers == ["张三"]
    assert "<李四>: Voice characteristics: 清亮少女音." in rendered.prompt
    assert "<李四>: Dialogue voice reference" not in rendered.prompt
    assert {"key": WARN_REFERENCE_AUDIO_OVERFLOW, "params": {"limit": 1, "name": "李四"}} in rendered.warnings


def test_speaker_without_reference_audio_warns_and_keeps_voice_style():
    text = "镜头1：黑场。\n@[旁白人]：{很久很久以前。}"
    rendered = render_unit_prompt(
        text, _project(), [], VoiceRenderSettings(voice_consistency="native", max_reference_audio=3)
    )
    assert rendered.audio_speakers == []
    assert "<旁白人>: Voice characteristics: 温和中年男声." in rendered.prompt
    assert {"key": WARN_SPEAKER_WITHOUT_AUDIO, "params": {"name": "旁白人"}} in rendered.warnings


def test_unattributed_narration_mark_is_dropped_from_the_prompt():
    """Unattributed narration belongs to TTS / postproduction voiceover and is entirely omitted from video models; render surrounding visual description normally."""
    rendered = render_unit_prompt("镜头1：空镜。\n{多年以后他仍记得这句话。}", _project(), [], _SOFT)
    assert "画外音说" not in rendered.prompt
    assert "多年以后他仍记得这句话" not in rendered.prompt
    assert "镜头1：空镜。" in rendered.prompt


def test_inline_speech_renders_the_same_official_phrasing_as_the_whole_line_form():
    """Inline and whole-line markers render the same official phrasing, with placement following the author's text."""
    legacy = render_unit_prompt(
        "镜头1：@[张三] 推门而入。\n@[张三]：{今晚的酒，我请。}",
        _project(),
        _refs(("character", "张三")),
        _SOFT,
    )
    inline = render_unit_prompt(
        "镜头1：@[张三] 推门而入。@[张三]{今晚的酒，我请。}",
        _project(),
        _refs(("character", "张三")),
        _SOFT,
    )

    assert "<张三> says {今晚的酒，我请。}" in legacy.prompt
    assert "<张三> 推门而入。<张三> says {今晚的酒，我请。}" in inline.prompt
    assert inline.warnings == legacy.warnings
    assert inline.audio_speakers == legacy.audio_speakers


def test_inline_speech_keeps_the_description_around_it_in_place():
    """Reconstruct markers in place, keeping descriptions on either side; pass the original line order to providers."""
    rendered = render_unit_prompt(
        "镜头1：@[张三] 推门，@[张三]{我来了。}，他按住 @[长剑]。",
        _project(),
        _refs(("character", "张三"), ("prop", "长剑")),
        _SOFT,
    )
    assert "<张三> 推门，<张三> says {我来了。}，他按住 <长剑>。" in rendered.prompt


def test_inline_unattributed_narration_is_dropped_without_losing_the_line():
    """Remove entire inline narration markers, retaining descriptions/subject markers in that line rather than discarding the whole line."""
    rendered = render_unit_prompt(
        "镜头1：@[张三] 推门，{夜风灌进来}，他按住 @[长剑]。",
        _project(),
        _refs(("character", "张三"), ("prop", "长剑")),
        _SOFT,
    )
    assert "夜风灌进来" not in rendered.prompt
    assert "<张三> 推门" in rendered.prompt
    assert "他按住 <长剑>。" in rendered.prompt


def test_whitespace_around_a_dropped_narration_collapses_to_one_space():
    """Whitespace alone on both sides collapses into one space; removing markers must not accumulate body whitespace."""
    rendered = render_unit_prompt(
        "镜头1：@[张三] 推门   {夜风灌进来}   他按住剑。",
        _project(),
        _refs(("character", "张三")),
        _SOFT,
    )
    assert "<张三> 推门 他按住剑。" in rendered.prompt


def test_line_leading_narration_does_not_leave_a_dangling_separator():
    """A line-leading marker leaves its right separator without a left attachment; the line must not start with ，."""
    rendered = render_unit_prompt(
        "{夜风灌进来}，@[张三] 推门。",
        _project(),
        _refs(("character", "张三")),
        _SOFT,
    )
    assert "<张三> 推门。" in rendered.prompt
    assert "，<张三>" not in rendered.prompt


def test_line_trailing_narration_drops_the_dangling_joiner_but_keeps_a_terminator():
    """For line-ending markers, remove dangling left connector punctuation; preserve author-written sentence-ending punctuation."""
    joined = render_unit_prompt("@[张三] 推门，{夜风灌进来}", _project(), _refs(("character", "张三")), _SOFT)
    assert "<张三> 推门" in joined.prompt
    assert "推门，" not in joined.prompt

    terminated = render_unit_prompt("@[张三] 推门。{夜风灌进来}", _project(), _refs(("character", "张三")), _SOFT)
    assert "<张三> 推门。" in terminated.prompt


def test_unregistered_speaker_line_is_sent_verbatim_with_warning():
    rendered = render_unit_prompt("镜头1：黑场。\n@[路人]：{你好。}", _project(), [], _SOFT)
    assert "@[路人]：{你好。}" in rendered.prompt
    assert " says {你好。}" not in rendered.prompt
    assert {"key": WARN_UNREGISTERED_SPEAKER, "params": {"name": "路人"}} in rendered.warnings


def test_clipped_reference_still_renders_as_subject():
    """Registered names whose references are trimmed by capability limits remain visual subjects: render <X>, never editor syntax."""
    rendered = render_unit_prompt(_TEXT, _project(), _refs(("scene", "酒馆")), _SOFT)
    assert "@[张三]" not in rendered.prompt
    assert "@[长剑]" not in rendered.prompt
    assert "<张三> 推门而入，手按 <长剑>。" in rendered.prompt
    # Subject markers and image numbers are independent; only images actually sent receive binding lines.
    assert rendered.prompt.startswith("<酒馆>@图片1。")
    assert "<张三>@图片" not in rendered.prompt


def test_unregistered_mention_kept_verbatim_with_warning():
    rendered = render_unit_prompt("镜头1：@[未知资产] 出现。", _project(), [], _SOFT)
    assert "@[未知资产]" in rendered.prompt
    assert {"key": WARN_UNREGISTERED_MENTION, "params": {"name": "未知资产"}} in rendered.warnings


def test_unclosed_brace_line_sent_verbatim_with_warning():
    rendered = render_unit_prompt(
        "镜头1：@[张三] 开口。\n@[张三]：{没有闭合", _project(), _refs(("character", "张三")), _SOFT
    )
    assert "{没有闭合" in rendered.prompt
    assert any(w["key"] == WARN_UNCLOSED_BRACE for w in rendered.warnings)


def test_legend_and_absolute_seconds_are_gone():
    rendered = render_unit_prompt(_TEXT, _project(), _refs(("character", "张三")), _SOFT)
    assert "[图" not in rendered.prompt
    assert "参考图对照" not in rendered.prompt
    assert "禁止出现：BGM、文字字幕、水印。" not in rendered.prompt
    assert "s)" not in rendered.prompt


def _video_avoid_line() -> str:
    """The storyboard video prompt's Avoid line is its negative video prompt."""
    return builtin_templates.render("storyboard/video", body="正文").splitlines()[-1]


def _avoid_line(prompt: str) -> str:
    lines = [line for line in prompt.splitlines() if line.startswith("Avoid:")]
    assert len(lines) == 1
    return lines[0]


def test_third_segment_anchors_style_and_video_negative_prompt():
    rendered = render_unit_prompt("镜头1：空镜。", _project(style_description="冷色调，胶片颗粒"), [], _SOFT)
    assert rendered.prompt.split("\n\n")[-1] == "\n".join(
        ["Style: 写实电影感", "Visual style: 冷色调，胶片颗粒", _video_avoid_line()]
    )


def test_third_segment_drops_quality_and_stability_packs():
    rendered = render_unit_prompt("镜头1：@[张三] 独行。", _project(), _refs(("character", "张三")), _SOFT)
    for phrase in ("高清", "细节丰富", "电影质感", "光影柔和", "稳定不变形", "五官清晰", "穿模", "卡顿"):
        assert phrase not in rendered.prompt


def test_third_segment_without_style_keeps_only_the_avoid_line():
    rendered = render_unit_prompt("镜头1：空镜。", _project(style=""), [], _SOFT)
    assert rendered.prompt == f"镜头1：空镜。\n\n{_video_avoid_line()}"


def test_twin_exclusion_only_when_two_or_more_characters():
    single = render_unit_prompt("镜头1：@[张三] 独行。", _project(), _refs(("character", "张三")), _SOFT)
    assert _avoid_line(single.prompt) == _video_avoid_line()
    clipped = render_unit_prompt(
        "镜头1：@[张三] 与 @[李四] 对峙。",
        _project(),
        _refs(("character", "张三")),
        _SOFT,
    )
    assert clipped.prompt.startswith("<张三>@图片1。")
    assert "<李四>@图片" not in clipped.prompt
    assert (
        _avoid_line(clipped.prompt) == f"{_video_avoid_line()}, doubles or twins with identical appearance and clothing"
    )


def test_product_fidelity_tail_follows_the_third_segment():
    rendered = render_unit_prompt(
        "镜头1：@[商品甲] 与 @[商品乙] 摆在桌上。",
        _project(products={"商品甲": {}, "商品乙": {}}),
        _refs(("product", "商品甲"), ("product", "商品乙"), ("product", "商品甲")),
        _SOFT,
    )
    tail = rendered.prompt.split("\n\n")[-1]
    assert tail.startswith(
        "High-fidelity product reproduction (highest priority, overriding the text / logo prohibitions above): products 「商品甲」「商品乙」 in the frame must"
    )
    assert rendered.prompt.index(_video_avoid_line()) < rendered.prompt.index(tail)


def test_script_without_dialogue_still_renders_three_segments():
    """Bodies without dialogue markers still have all three parts: bindings, body, and constraints, without semantic fallback.

    Body `镜头N：` is ordinary text, passed verbatim into prompts without structural recognition.
    """
    rendered = render_unit_prompt(
        "镜头1：@[张三] 走进 @[酒馆]。\n镜头2：他坐下。",
        _project(),
        _refs(("character", "张三"), ("scene", "酒馆")),
        VoiceRenderSettings(voice_consistency="native", max_reference_audio=3),
    )
    assert rendered.audio_speakers == []
    assert rendered.warnings == []
    assert "<张三>@图片1、<酒馆>@图片2。" in rendered.prompt
    assert "镜头1：<张三> 走进 <酒馆>。\n镜头2：他坐下。" in rendered.prompt


def test_audio_ready_overrides_field_presence(tmp_path):
    """Do not bind fields pointing to deleted files: numbering exactly matches sent clip count, and degradation warnings say
    audio unavailable rather than unconfigured; 张三 has a field value but is absent from audio_ready."""
    rendered = render_unit_prompt(
        _TEXT,
        _project(),
        _refs(("character", "张三")),
        VoiceRenderSettings(voice_consistency="native", max_reference_audio=3, audio_ready={"李四"}),
    )
    assert rendered.audio_speakers == ["李四"]
    assert "<李四>: Dialogue voice reference @音频1" in rendered.prompt
    assert {"key": WARN_SPEAKER_AUDIO_UNAVAILABLE, "params": {"name": "张三"}} in rendered.warnings
    assert {"key": WARN_SPEAKER_WITHOUT_AUDIO, "params": {"name": "张三"}} not in rendered.warnings


def test_audio_speaker_reference_index_tracks_image_slot_by_name_not_position():
    """Audio order (first dialogue speaker) and image order (first mention) derive independently: references place the scene
    before 张三, who speaks first. ``audio_speaker_reference_index`` must find image index 1 (0-based) by name,
    not assume first audio corresponds to first image."""
    rendered = render_unit_prompt(
        _TEXT,
        _project(),
        _refs(("scene", "酒馆"), ("character", "张三")),
        VoiceRenderSettings(voice_consistency="native", max_reference_audio=3),
    )
    assert rendered.audio_speakers == ["张三", "李四"]
    # references[0]=酒馆, references[1]=张三 → 张三 has 0-based index 1; no 李四 image was sent.
    assert rendered.audio_speaker_reference_index == [1, None]


def test_requires_reference_image_downgrades_offscreen_speaker_with_warning():
    """When backends require per-image audio attachment (such as wan2.7-r2v), offscreen speakers without references bind no audio.
    Generate numbering/warnings together during rendering so @音频N never promises an unsent binding."""
    rendered = render_unit_prompt(
        _TEXT,
        _project(),
        _refs(("scene", "酒馆"), ("character", "张三")),
        VoiceRenderSettings(voice_consistency="native", max_reference_audio=3, requires_reference_image=True),
    )
    # 李四 has no reference image (entirely offscreen), so even usable audio stays unbound.
    assert rendered.audio_speakers == ["张三"]
    assert rendered.audio_speaker_reference_index == [1]
    assert "<李四>: Dialogue voice reference" not in rendered.prompt
    assert {"key": WARN_SPEAKER_AUDIO_NEEDS_IMAGE, "params": {"name": "李四"}} in rendered.warnings


@pytest.mark.parametrize("registered", [_NAME_NFC, _NAME_NFD], ids=["登记NFC", "登记NFD"])
@pytest.mark.parametrize("written", [_NAME_NFC, _NAME_NFD], ids=["出场NFC", "出场NFD"])
def test_combining_char_name_renders_identically_in_every_encoding_pairing(registered: str, written: str):
    """All four NFC/NFD combining-name pairings render identical prompts and audio bindings.

    At this chain endpoint, missing normalization in speaker, subject mention, image numbering, or voice declaration silently leaks
    ``@[名称]`` syntax to provider requests, prevents official dialogue reconstruction, or fails audio binding,
    leaving users with wrong faces and voices instead of errors.
    """
    project = {
        "style": "写实电影感",
        "characters": {registered: {"voice_style": "清亮少女音", "reference_audio": "characters/refs_audio/x.wav"}},
        "scenes": {},
        "props": {},
    }
    text = f"镜头1：夜色下，@[{written}] 推门而入。\n@[{written}]：{{Tôi đến rồi.}}"

    rendered = render_unit_prompt(
        text,
        project,
        _refs(("character", written)),
        VoiceRenderSettings(voice_consistency="native", max_reference_audio=3, audio_ready={registered}),
    )

    assert rendered.audio_speakers == [_NAME_NFC]
    assert rendered.audio_speaker_reference_index == [0]
    assert rendered.warnings == []
    assert f"<{_NAME_NFC}>@图片1" in rendered.prompt
    assert (
        f"<{_NAME_NFC}>: Dialogue voice reference @音频1; use only timbre, speech rate, and emotion; the body text is authoritative for dialogue; Voice characteristics: 清亮少女音."
        in rendered.prompt
    )
    assert f"<{_NAME_NFC}> 推门而入" in rendered.prompt
    assert f"<{_NAME_NFC}> says {{Tôi đến rồi.}}" in rendered.prompt
    # No editor reference syntax may leak into provider requests.
    assert "@[" not in rendered.prompt


def test_padded_mention_and_speaker_render_with_canonical_asset_name():
    rendered = render_unit_prompt(
        "镜头1：@[ 张三 ] 推门而入。\n@[ 张三 ]：{我来了}",
        _project(),
        _refs(("character", " 张三 ")),
        VoiceRenderSettings(voice_consistency="native", max_reference_audio=3, audio_ready={" 张三 "}),
    )

    assert rendered.audio_speakers == ["张三"]
    assert rendered.audio_speaker_reference_index == [0]
    assert "<张三>@图片1" in rendered.prompt
    assert "<张三> 推门而入" in rendered.prompt
    assert "<张三> says {我来了}" in rendered.prompt
    assert "@[" not in rendered.prompt


def test_resolve_reference_audio_paths_only_returns_existing_files_under_refs_audio(tmp_path):
    refs_audio = tmp_path / "characters" / "refs_audio"
    refs_audio.mkdir(parents=True)
    (refs_audio / "张三.wav").write_bytes(b"RIFF")
    (tmp_path / "project.json").write_text("{}", encoding="utf-8")
    project = {
        "characters": {
            "张三": {"reference_audio": "characters/refs_audio/张三.wav"},
            "李四": {"reference_audio": "characters/refs_audio/李四.mp3"},  # File nonexistent.
            "越界": {"reference_audio": "project.json"},  # Outside refs_audio.
            "未设": {},
        }
    }
    resolved = resolve_reference_audio_paths(project, tmp_path)
    assert set(resolved) == {"张三"}
    assert resolved["张三"] == refs_audio / "张三.wav"


def test_out_of_bounds_audio_path_also_degrades_as_unavailable(tmp_path):
    """Files outside ``refs_audio`` may exist perfectly well but have invalid paths;
    ``resolve_reference_audio_paths`` also excludes them. Therefore warn only unavailable, without claiming
    file absence and sending users toward the wrong diagnosis."""
    refs_audio = tmp_path / "characters" / "refs_audio"
    refs_audio.mkdir(parents=True)
    (tmp_path / "project.json").write_text("{}", encoding="utf-8")
    project = _project(characters={"张三": {"reference_audio": "project.json"}})

    audio_ready = resolve_reference_audio_paths(project, tmp_path)
    assert audio_ready == {}

    rendered = render_unit_prompt(
        "镜头1：开场。\n@[张三]：{我来了}",
        project,
        _refs(("character", "张三")),
        VoiceRenderSettings(
            voice_consistency="native",
            max_reference_audio=3,
            model_id="doubao-seedance-2-0",
            audio_ready=set(audio_ready),
        ),
    )
    assert rendered.audio_speakers == []
    assert {"key": WARN_SPEAKER_AUDIO_UNAVAILABLE, "params": {"name": "张三"}} in rendered.warnings
    assert {"key": WARN_SPEAKER_WITHOUT_AUDIO, "params": {"name": "张三"}} not in rendered.warnings


def test_resolve_reference_audio_paths_ignores_non_dict_characters_bucket(tmp_path):
    (tmp_path / "project.json").write_text("{}", encoding="utf-8")
    project = {"characters": [{}]}  # Validators do not reject nondict buckets; data_validator validates only dicts.

    resolved = resolve_reference_audio_paths(project, tmp_path)

    assert resolved == {}


def _project_with_derivatives(**overrides):
    project = _project(**overrides)
    project["characters"]["张三"] = {
        **project["characters"]["张三"],
        "derivatives": {"劲装": {"description": "换上黑色劲装"}, "兽化": {"description": "半兽化"}},
    }
    return project


def test_derivative_subject_marks_declare_one_character_in_several_forms():
    """Base and derivative coexist: bind each reference image and declare different appearances of the same person."""
    rendered = render_unit_prompt(
        "@[张三] 与 @[张三/劲装] 对峙。",
        _project_with_derivatives(),
        _refs(("character", "张三"), ("character", "张三/劲装")),
        _SOFT,
    )

    assert rendered.prompt.startswith("<张三>@图片1、<张三/劲装>@图片2。")
    assert (
        "<张三> and <张三/劲装> are different appearances of the same character; depict each according to its corresponding reference image."
        in rendered.prompt
    )


def test_several_derivatives_of_one_character_share_a_single_form_declaration():
    rendered = render_unit_prompt(
        "@[张三/劲装] 与 @[张三/兽化] 同框。",
        _project_with_derivatives(),
        _refs(("character", "张三/劲装"), ("character", "张三/兽化")),
        _SOFT,
    )

    assert (
        "<张三/劲装> and <张三/兽化> are different appearances of the same character; depict each according to its corresponding reference image."
        in rendered.prompt
    )


def test_one_character_in_two_forms_does_not_trigger_the_twin_exclusion():
    """Duplicate-person exclusions count distinct characters: base plus derivative are two images of one person, so exclusion would contradict the appearance explanation."""
    rendered = render_unit_prompt(
        "@[张三] 与 @[张三/劲装] 对峙。",
        _project_with_derivatives(),
        _refs(("character", "张三"), ("character", "张三/劲装")),
        _SOFT,
    )

    assert _avoid_line(rendered.prompt) == _video_avoid_line()


def test_two_characters_still_trigger_the_twin_exclusion_when_one_is_a_derivative():
    rendered = render_unit_prompt(
        "@[张三/劲装] 与 @[李四] 对峙。",
        _project_with_derivatives(),
        _refs(("character", "张三/劲装"), ("character", "李四")),
        _SOFT,
    )

    assert (
        _avoid_line(rendered.prompt)
        == f"{_video_avoid_line()}, doubles or twins with identical appearance and clothing"
    )


def test_a_lone_form_needs_no_declaration():
    rendered = render_unit_prompt(
        "@[张三/劲装] 推门。",
        _project_with_derivatives(),
        _refs(("character", "张三/劲装")),
        _SOFT,
    )

    assert "different appearances of the same character" not in rendered.prompt


def test_speaker_position_renders_the_form_written_down():
    """Speakers and descriptions use the same subject markers; derivative notation renders the derivative while binding the base voice."""
    rendered = render_unit_prompt(
        "@[张三/劲装]：{今晚的酒，我请。}",
        _project_with_derivatives(),
        _refs(("character", "张三/劲装")),
        VoiceRenderSettings(voice_consistency="soft"),
    )

    assert "<张三/劲装> says {今晚的酒，我请。}" in rendered.prompt
    assert "<张三>说" not in rendered.prompt
    assert "<张三>: Voice characteristics: 低沉沙哑的男声." in rendered.prompt


def test_unregistered_speaker_derivative_falls_back_to_the_ontology_mark():
    rendered = render_unit_prompt(
        "@[张三/不存在]：{今晚的酒，我请。}",
        _project_with_derivatives(),
        _refs(("character", "张三")),
        _SOFT,
    )

    assert "<张三> says {今晚的酒，我请。}" in rendered.prompt
