"""三段论渲染管线：A/B/C 三档注入差异、编号顺序契约与降级 warning。"""

from __future__ import annotations

import unicodedata
from dataclasses import replace

import pytest

from lib.reference_video.prompt_render import (
    render_unit_prompt,
    resolve_reference_audio_paths,
)
from lib.reference_video.script_preview import (
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
from lib.reference_video.voice_settings import VoiceRenderSettings
from lib.script_models import ReferenceResource


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


#: 带组合附加符的角色名（越南语），两种编码屏幕显示相同、字节不同——资产名比对的坐标系用例。
_NAME_NFC = unicodedata.normalize("NFC", "Hiếu")
_NAME_NFD = unicodedata.normalize("NFD", "Hiếu")


def _refs(*pairs):
    return [ReferenceResource(type=t, name=n) for t, n in pairs]


#: 与声音无关的用例用的声音档：``soft`` 有声、无参考音频。渲染入口的 ``settings`` 必填，
#: 这些用例照样要给一档，取字段默认即可。
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
        style="写实电影感",
    )
    # 音频顺序即请求字段顺序，也即 @音频N 编号
    assert rendered.audio_speakers == ["张三", "李四"]
    assert "<张三>的台词音色参考 @音频1，声音特征：低沉沙哑的男声。" in rendered.prompt
    assert "<李四>的台词音色参考 @音频2，声音特征：清亮少女音。" in rendered.prompt


def test_silent_episode_sends_dialogue_without_any_audio_binding():
    """本集无声：不组装参考音频、prompt 里不出现 @音频N，台词照常下发作口型参考。"""
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
        style="写实电影感",
    )
    assert rendered.audio_speakers == []
    assert rendered.audio_speaker_reference_index == []
    assert "@音频" not in rendered.prompt
    assert "<张三>说 {今晚的酒，我请。}" in rendered.prompt
    assert "<李四>说 {你终于来了。}" in rendered.prompt


def test_silent_episode_injects_no_voice_style_same_as_silent_model():
    """本集关闭音频与模型不产音（C 类）同口径：两条无声路径都不注入「声音特征：…」。

    ``voice_style`` 描述的是听得到的音色，无声成片里注入只会让模型把配额花在用不上的约束上；
    台词不受影响（另有用例逐字比对第二段）。
    """
    refs = _refs(("scene", "酒馆"), ("character", "张三"), ("prop", "长剑"))
    silent_episode = render_unit_prompt(
        _TEXT,
        _project(),
        refs,
        VoiceRenderSettings(voice_consistency="native", requested_generate_audio=False, max_reference_audio=3),
    )
    silent_model = render_unit_prompt(_TEXT, _project(), refs, VoiceRenderSettings(voice_consistency="none"))

    assert "声音特征" not in silent_episode.prompt
    assert "声音特征" not in silent_model.prompt
    # 第一段只剩主体绑定行，两条无声路径逐字同形
    assert silent_episode.prompt.split("\n\n")[0] == silent_model.prompt.split("\n\n")[0]


@pytest.mark.parametrize(
    "silencing",
    [
        pytest.param({"requested_generate_audio": False}, id="silent_episode"),
        pytest.param({"voice_consistency": "none"}, id="silent_model"),
    ],
)
def test_silent_paths_keep_the_whole_body_identical_to_the_audible_path(silencing: dict):
    """第二段与有声路径逐字同形——只有第一段的音色参考行消失。

    整段正文比对而非只比第一句：音频编号从第二个说话人起才可能出现分叉，只比开头会漏掉
    后续绑定位上的差异。两条无声路径各比一次。
    """
    settings = VoiceRenderSettings(
        voice_consistency="native",
        max_reference_audio=3,
        model_id="doubao-seedance-2-0",
        audio_ready={"张三", "李四"},
    )
    refs = _refs(("scene", "酒馆"), ("character", "张三"), ("prop", "长剑"))
    audible = render_unit_prompt(_TEXT, _project(), refs, settings, style="写实电影感")
    silent = render_unit_prompt(_TEXT, _project(), refs, replace(settings, **silencing), style="写实电影感")

    def _body(prompt: str) -> str:
        return prompt.split("\n\n")[1]

    # 有声侧确实绑定了两段音频，比对才有区分度（否则两侧本就无音频，断言恒真）
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
            {"key": WARN_SILENT_MODEL, "params": {"model": "doubao-seedance-2-0"}},
            id="silent_model",
        ),
    ],
)
def test_silent_preview_and_execution_reach_the_same_conclusion(silencing: dict, expected: dict):
    """同一份正文在预览与执行两条路径上给出同一组无声结论。

    两侧的 ``VoiceRenderSettings`` 由不同的构造点拼出（预览侧 ``from_caps``、执行侧逐字段），
    结论分叉时用户会在预览里看到声音已绑定、生成完才发现是无声成片。
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
    # 承诺与实付一致：预览说这一集听不到声音，执行侧就不该绑上任何音频段
    assert rendered.audio_speakers == []
    assert "@音频" not in rendered.prompt
    assert [u.text for u in preview.utterances] == ["今晚的酒，我请。", "你终于来了。"]


def test_silent_preview_and_execution_stay_silent_without_any_utterance():
    """真「无声场景」（正文没有任何发声记号）：两条路径都不发无声知会。"""
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
    """李四只在台词记号的 speaker 位出现：无参考图绑定，但音色声明与台词渲染照常。"""
    rendered = render_unit_prompt(
        _TEXT,
        _project(),
        _refs(("scene", "酒馆"), ("character", "张三"), ("prop", "长剑")),
        VoiceRenderSettings(voice_consistency="native", max_reference_audio=3),
    )
    assert "<李四>@图片" not in rendered.prompt
    assert "<李四>的台词音色参考 @音频2" in rendered.prompt
    assert "<李四>说 {你终于来了。}" in rendered.prompt


def test_soft_tier_declares_voice_style_without_audio_designation():
    rendered = render_unit_prompt(
        _TEXT,
        _project(),
        _refs(("character", "张三")),
        VoiceRenderSettings(voice_consistency="soft", max_reference_audio=3),
    )
    assert rendered.audio_speakers == []
    assert "@音频" not in rendered.prompt
    assert "<张三>的声音特征：低沉沙哑的男声。" in rendered.prompt
    assert "<李四>的声音特征：清亮少女音。" in rendered.prompt


def test_silent_tier_keeps_dialogue_lines_but_injects_no_voice_declaration():
    rendered = render_unit_prompt(
        _TEXT,
        _project(),
        _refs(("character", "张三")),
        VoiceRenderSettings(voice_consistency="none", model_id="minimax-01"),
    )
    assert "声音特征" not in rendered.prompt
    assert "@音频" not in rendered.prompt
    # 台词照常渲染：供口型与表演
    assert "<张三>说 {今晚的酒，我请。}" in rendered.prompt
    assert {"key": WARN_SILENT_MODEL, "params": {"model": "minimax-01"}} in rendered.warnings


def test_reference_audio_overflow_truncates_and_warns():
    rendered = render_unit_prompt(
        _TEXT,
        _project(),
        _refs(("character", "张三")),
        VoiceRenderSettings(voice_consistency="native", max_reference_audio=1),
    )
    assert rendered.audio_speakers == ["张三"]
    assert "<李四>的声音特征：清亮少女音。" in rendered.prompt
    assert "<李四>的台词音色参考" not in rendered.prompt
    assert {"key": WARN_REFERENCE_AUDIO_OVERFLOW, "params": {"limit": 1, "name": "李四"}} in rendered.warnings


def test_speaker_without_reference_audio_warns_and_keeps_voice_style():
    text = "镜头1：黑场。\n@[旁白人]：{很久很久以前。}"
    rendered = render_unit_prompt(
        text, _project(), [], VoiceRenderSettings(voice_consistency="native", max_reference_audio=3)
    )
    assert rendered.audio_speakers == []
    assert "<旁白人>的声音特征：温和中年男声。" in rendered.prompt
    assert {"key": WARN_SPEAKER_WITHOUT_AUDIO, "params": {"name": "旁白人"}} in rendered.warnings


def test_unattributed_narration_mark_is_dropped_from_the_prompt():
    """无归属旁白由 TTS / 后期配音承担，整段不下发视频模型；同段的画面描述照常渲染。"""
    rendered = render_unit_prompt("镜头1：空镜。\n{多年以后他仍记得这句话。}", _project(), [], _SOFT)
    assert "画外音说" not in rendered.prompt
    assert "多年以后他仍记得这句话" not in rendered.prompt
    assert "镜头1：空镜。" in rendered.prompt


def test_inline_speech_renders_the_same_official_phrasing_as_the_whole_line_form():
    """内联记号与整行写法渲染出同一段官方句式，只是行文位置随作者所写。"""
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

    assert "<张三>说 {今晚的酒，我请。}" in legacy.prompt
    assert "<张三> 推门而入。<张三>说 {今晚的酒，我请。}" in inline.prompt
    assert inline.warnings == legacy.warnings
    assert inline.audio_speakers == legacy.audio_speakers


def test_inline_speech_keeps_the_description_around_it_in_place():
    """记号就地重组，两侧描述留在原处——一行的行文顺序原样传给供应商。"""
    rendered = render_unit_prompt(
        "镜头1：@[张三] 推门，@[张三]{我来了。}，他按住 @[长剑]。",
        _project(),
        _refs(("character", "张三"), ("prop", "长剑")),
        _SOFT,
    )
    assert "<张三> 推门，<张三>说 {我来了。}，他按住 <长剑>。" in rendered.prompt


def test_inline_unattributed_narration_is_dropped_without_losing_the_line():
    """行内旁白记号整段丢弃，同一行的描述与主体记号留在原处，整行不被丢掉。"""
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
    """记号两侧只有空白时并成一个空格——空白不该因为记号消失而在正文里堆起来。"""
    rendered = render_unit_prompt(
        "镜头1：@[张三] 推门   {夜风灌进来}   他按住剑。",
        _project(),
        _refs(("character", "张三")),
        _SOFT,
    )
    assert "<张三> 推门 他按住剑。" in rendered.prompt


def test_line_leading_narration_does_not_leave_a_dangling_separator():
    """记号写在行首时，它右侧的分隔标点没有左侧可接——整行不以「，」开头。"""
    rendered = render_unit_prompt(
        "{夜风灌进来}，@[张三] 推门。",
        _project(),
        _refs(("character", "张三")),
        _SOFT,
    )
    assert "<张三> 推门。" in rendered.prompt
    assert "，<张三>" not in rendered.prompt


def test_line_trailing_narration_drops_the_dangling_joiner_but_keeps_a_terminator():
    """记号写在行尾时，左侧悬空的连接标点丢掉；收句的终止标点是作者写完的一句，保留。"""
    joined = render_unit_prompt("@[张三] 推门，{夜风灌进来}", _project(), _refs(("character", "张三")), _SOFT)
    assert "<张三> 推门" in joined.prompt
    assert "推门，" not in joined.prompt

    terminated = render_unit_prompt("@[张三] 推门。{夜风灌进来}", _project(), _refs(("character", "张三")), _SOFT)
    assert "<张三> 推门。" in terminated.prompt


def test_unregistered_speaker_line_is_sent_verbatim_with_warning():
    rendered = render_unit_prompt("镜头1：黑场。\n@[路人]：{你好。}", _project(), [], _SOFT)
    assert "@[路人]：{你好。}" in rendered.prompt
    assert "说 {你好。}" not in rendered.prompt
    assert {"key": WARN_UNREGISTERED_SPEAKER, "params": {"name": "路人"}} in rendered.warnings


def test_clipped_reference_still_renders_as_subject():
    """被能力上限裁掉参考图的已登记名字仍是画面主体：渲染 <X>，不把编辑器语法发给模型。"""
    rendered = render_unit_prompt(_TEXT, _project(), _refs(("scene", "酒馆")), _SOFT)
    assert "@[张三]" not in rendered.prompt
    assert "@[长剑]" not in rendered.prompt
    assert "<张三> 推门而入，手按 <长剑>。" in rendered.prompt
    # 主体记号与图号解耦：只有随请求发出的那张图才有绑定行
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
    rendered = render_unit_prompt(_TEXT, _project(), _refs(("character", "张三")), _SOFT, style="写实电影感")
    assert "[图" not in rendered.prompt
    assert "参考图对照" not in rendered.prompt
    assert "禁止出现：BGM、文字字幕、水印。" not in rendered.prompt
    assert "s)" not in rendered.prompt


def test_third_segment_anchors_style_and_constraint_packs():
    rendered = render_unit_prompt("镜头1：空镜。", _project(), [], _SOFT, style="写实电影感")
    assert "整体视觉风格：写实电影感。" in rendered.prompt
    assert "保持无字幕" in rendered.prompt
    assert "不要生成水印" in rendered.prompt
    assert "禁止出现背景音乐。" in rendered.prompt


def test_twin_guard_only_when_two_or_more_character_images():
    single = render_unit_prompt("镜头1：@[张三] 独行。", _project(), _refs(("character", "张三")), _SOFT)
    assert "双胞胎" not in single.prompt
    both = render_unit_prompt(
        "镜头1：@[张三] 与 @[李四] 对峙。",
        _project(),
        _refs(("character", "张三"), ("character", "李四")),
        _SOFT,
    )
    assert "双胞胎" in both.prompt


def test_script_without_dialogue_still_renders_three_segments():
    """无台词记号的正文照样出三段：绑定 + 正文 + 约束包齐备，语义不回退。

    正文里的 `镜头N：` 只是普通文字，逐字进提示词，不被识别为结构。
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
    """字段指向已删文件时不绑定：编号与实际发出的音频段数严格等长，且降级 warning 指向
    「音频不可用」而非「未设置」——张三字段有值，只是不在 audio_ready 内。"""
    rendered = render_unit_prompt(
        _TEXT,
        _project(),
        _refs(("character", "张三")),
        VoiceRenderSettings(voice_consistency="native", max_reference_audio=3, audio_ready={"李四"}),
    )
    assert rendered.audio_speakers == ["李四"]
    assert "<李四>的台词音色参考 @音频1" in rendered.prompt
    assert {"key": WARN_SPEAKER_AUDIO_UNAVAILABLE, "params": {"name": "张三"}} in rendered.warnings
    assert {"key": WARN_SPEAKER_WITHOUT_AUDIO, "params": {"name": "张三"}} not in rendered.warnings


def test_audio_speaker_reference_index_tracks_image_slot_by_name_not_position():
    """参考音频顺序（台词 speaker 首现）与参考图顺序（mention 首现）独立派生：references 里
    场景先于张三出现，但张三先开口——``audio_speaker_reference_index`` 须按名字取图 1（0-based）
    的下标，不能按位置假设第 1 段音频配第 1 张图。"""
    rendered = render_unit_prompt(
        _TEXT,
        _project(),
        _refs(("scene", "酒馆"), ("character", "张三")),
        VoiceRenderSettings(voice_consistency="native", max_reference_audio=3),
    )
    assert rendered.audio_speakers == ["张三", "李四"]
    # references[0]=酒馆, references[1]=张三 → 张三的 0-based 下标是 1；李四未随请求发图
    assert rendered.audio_speaker_reference_index == [1, None]


def test_requires_reference_image_downgrades_offscreen_speaker_with_warning():
    """backend 要求音频逐段挂图（如 wan2.7-r2v）时，纯画外 speaker（无参考图）不绑定音频，
    编号与 warning 都在渲染期同步产生，避免 @音频N 承诺一段实际不会发出的绑定。"""
    rendered = render_unit_prompt(
        _TEXT,
        _project(),
        _refs(("scene", "酒馆"), ("character", "张三")),
        VoiceRenderSettings(voice_consistency="native", max_reference_audio=3, requires_reference_image=True),
    )
    # 李四没有参考图（纯画外），即使有可用音频也不绑定
    assert rendered.audio_speakers == ["张三"]
    assert rendered.audio_speaker_reference_index == [1]
    assert "<李四>的台词音色参考" not in rendered.prompt
    assert {"key": WARN_SPEAKER_AUDIO_NEEDS_IMAGE, "params": {"name": "李四"}} in rendered.warnings


@pytest.mark.parametrize("registered", [_NAME_NFC, _NAME_NFD], ids=["登记NFC", "登记NFD"])
@pytest.mark.parametrize("written", [_NAME_NFC, _NAME_NFD], ids=["出场NFC", "出场NFD"])
def test_combining_char_name_renders_identically_in_every_encoding_pairing(registered: str, written: str):
    """组合字符角色名的四种 NFC/NFD 配对渲染出完全相同的 prompt 与音频绑定。

    这是链路末端：说话人、mention 主体记号、参考图编号、音色声明四处判定任一漏归一，都不会
    报错，而是让 ``@[名称]`` 这个引用语法记号原样漏进供应商请求、台词不重组成官方句式、或音频
    不绑——用户拿到的是一条脸和声音都不对的成片。
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
    assert f"<{_NAME_NFC}>的台词音色参考 @音频1，声音特征：清亮少女音。" in rendered.prompt
    assert f"<{_NAME_NFC}> 推门而入" in rendered.prompt
    assert f"<{_NAME_NFC}>说 {{Tôi đến rồi.}}" in rendered.prompt
    # 引用语法记号一个都不该漏进供应商请求
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
    assert "<张三>说 {我来了}" in rendered.prompt
    assert "@[" not in rendered.prompt


def test_resolve_reference_audio_paths_only_returns_existing_files_under_refs_audio(tmp_path):
    refs_audio = tmp_path / "characters" / "refs_audio"
    refs_audio.mkdir(parents=True)
    (refs_audio / "张三.wav").write_bytes(b"RIFF")
    (tmp_path / "project.json").write_text("{}", encoding="utf-8")
    project = {
        "characters": {
            "张三": {"reference_audio": "characters/refs_audio/张三.wav"},
            "李四": {"reference_audio": "characters/refs_audio/李四.mp3"},  # 文件不存在
            "越界": {"reference_audio": "project.json"},  # refs_audio 之外
            "未设": {},
        }
    }
    resolved = resolve_reference_audio_paths(project, tmp_path)
    assert set(resolved) == {"张三"}
    assert resolved["张三"] == refs_audio / "张三.wav"


def test_out_of_bounds_audio_path_also_degrades_as_unavailable(tmp_path):
    """字段指到 ``refs_audio`` 之外时文件本身可能好端端存在，只是路径不合法——同样被
    ``resolve_reference_audio_paths`` 排除。这条 warning 因此只说「不可用」，不能断言是
    文件缺失，否则又把用户导向错误的排查方向。"""
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
    project = {"characters": [{}]}  # 校验器不拒绝非 dict 桶（data_validator 只在 dict 时才校验）

    resolved = resolve_reference_audio_paths(project, tmp_path)

    assert resolved == {}
