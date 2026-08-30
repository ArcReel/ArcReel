"""视频单元正文的发声记号派生与降级可见性 warning。"""

import unicodedata

import pytest

from lib.i18n import MESSAGES, _
from lib.reference_video.script_preview import (
    WARN_BRACES_NOT_SPEECH,
    WARN_REFERENCE_AUDIO_OVERFLOW,
    WARN_SILENT_EPISODE,
    WARN_SILENT_MODEL,
    WARN_SPEAKER_AUDIO_NEEDS_IMAGE,
    WARN_SPEAKER_AUDIO_UNAVAILABLE,
    WARN_SPEAKER_WITHOUT_AUDIO,
    WARN_UNCLOSED_BRACE,
    WARN_UNIT_WITHOUT_SCENE,
    WARN_UNREGISTERED_MENTION,
    WARN_UNREGISTERED_SPEAKER,
    build_script_preview,
    derive_utterances,
    derive_voice_bindings,
)
from lib.reference_video.text_parser import (
    derive_references_from_text,
    extract_mentions,
    line_speech_marks,
)
from lib.reference_video.voice_settings import VoiceRenderSettings

PROJECT = {
    "characters": {
        "张三": {"description": "x", "reference_audio": "assets/audio/zhangsan.wav"},
        "李四": {"description": "x"},
        "旁白者": {"description": "x", "reference_audio": "assets/audio/pangbai.wav"},
    },
    "scenes": {"酒馆": {"description": "x"}},
    "props": {},
}

#: 一张场景资产都没登记的项目：正文没有场景可引用时不发「本单元未引用场景」，与场景无关的
#: 语法 / 声音用例据此保持单一关注点。
PROJECT_WITHOUT_SCENES = {**PROJECT, "scenes": {}}

#: 带组合附加符的角色名（越南语），两种编码屏幕显示相同、字节不同——资产名比对的坐标系用例。
_NAME_NFC = unicodedata.normalize("NFC", "Hiếu")
_NAME_NFD = unicodedata.normalize("NFD", "Hiếu")

#: 与声音无关的用例用的声音档：``soft`` 有声、无参考音频。预览入口的 ``settings`` 必填，
#: 这些用例照样要给一档，取字段默认即可。
_SOFT = VoiceRenderSettings()


def keys(preview) -> list[str]:
    return [w["key"] for w in preview.warnings]


# ---------- 行内记号匹配原语 ----------


def marks(line: str) -> list[tuple[str, str]]:
    return [(mark.speaker, mark.text) for mark in line_speech_marks(line)]


@pytest.mark.parametrize(
    "line",
    [
        "@[张三]：{我来了}",
        "@[张三]:{我来了}",
        "  @[张三] ： {我来了}  ",
        "@张三：{我来了}",
        "@[张三]{我来了}",
        "中景，@[张三] 笑着，@[张三]{我来了} 说完转身",
    ],
)
def test_dialogue_mark_accepts_wrapped_bare_both_colons_and_inline(line: str):
    assert marks(line) == [("张三", "我来了")]


@pytest.mark.parametrize(
    "line",
    [
        "@[张三]：我来了",  # 无花括号
        "@[张三]：{我来了",  # 未闭合
        "@[ ]：{我来了}",  # speaker 位全为空白
        "@[张三]：{}",  # 空台词
        "@[张三]：{   }",  # 台词只有空白
    ],
)
def test_line_without_valid_mark_derives_nothing(line: str):
    assert marks(line) == []


def test_speaker_must_be_adjacent_to_the_braces():
    """隔着描述文字的 mention 不是说话人——不做「行内最近 mention 猜 speaker」。"""
    assert marks("他说 @[张三] 转身，屋里传出 {我来了}") == [("", "我来了")]


def test_blank_speaker_degrades_to_warning_instead_of_raising():
    """speaker 位空白不得构造非法 Utterance——只读派生要出 warning，不能抛校验错。"""
    preview = build_script_preview("中景。\n@[ ]：{我来了}", PROJECT_WITHOUT_SCENES, _SOFT)
    assert preview.utterances == []
    # 未成记号 → 花括号未识别 warning；空白名同时作为未登记 mention 被点名。
    assert keys(preview) == [WARN_UNREGISTERED_MENTION, WARN_BRACES_NOT_SPEECH]


def test_voiceover_mark_is_bare_braces_anywhere():
    assert marks("  {那年冬天格外冷}  ") == [("", "那年冬天格外冷")]
    assert marks("旁白：{那年冬天}") == [("", "那年冬天")]
    assert marks("镜头切到窗外。{那年冬天}") == [("", "那年冬天")]


@pytest.mark.parametrize("line", ["{}", "{   }"])
def test_blank_braces_are_not_utterances(line: str):
    """空台词不派生：``Utterance`` 与 DataValidator 都要求 text 非空。"""
    assert marks(line) == []


def test_blank_braces_degrade_to_warning():
    preview = build_script_preview("中景。\n@[张三]：{}\n{   }", PROJECT_WITHOUT_SCENES, _SOFT)
    assert preview.utterances == []
    assert keys(preview) == [WARN_BRACES_NOT_SPEECH, WARN_BRACES_NOT_SPEECH]


# ---------- 派生 ----------


def test_normative_lines_derive_dialogue_and_voiceover():
    text = "@[张三] 推门进来。\n@[张三]：{我来了}\n{那年冬天格外冷}\n@[李四] 抬眼。\n@[李四]：{你迟到了}"
    preview = build_script_preview(text, PROJECT, _SOFT)
    assert [(u.kind, u.speaker, u.text) for u in preview.utterances] == [
        ("dialogue", "张三", "我来了"),
        ("voiceover", None, "那年冬天格外冷"),
        ("dialogue", "李四", "你迟到了"),
    ]


def test_inline_dialogue_is_derived_alongside_the_description():
    """台词跟在同一行画面描述之后照常派生；说话人只绑声音，描述里的资产照常进参考图。"""
    text = "@[酒馆] 内景，@[张三] 推门。@[张三]{我来了}"
    preview = build_script_preview(text, PROJECT, _SOFT)
    assert [(u.kind, u.speaker, u.text) for u in preview.utterances] == [("dialogue", "张三", "我来了")]
    assert preview.warnings == []
    assert [r.name for r in derive_references_from_text(text, PROJECT)[0]] == ["酒馆", "张三"]


def test_marks_recognized_on_a_line_do_not_suppress_residual_brace_warning():
    preview = build_script_preview("中景。@[张三]{我来了} 然后 {坏", PROJECT_WITHOUT_SCENES, _SOFT)
    assert [u.text for u in preview.utterances] == ["我来了"]
    assert keys(preview) == [WARN_UNCLOSED_BRACE]


def test_script_without_dialogue_symbols_derives_nothing():
    """没有台词符号的正文 → utterances 自然为空、无 warning。"""
    text = "中景，@[张三] 推门进 @[酒馆]。"
    preview = build_script_preview(text, PROJECT, _SOFT)
    assert preview.utterances == []
    assert preview.warnings == []
    assert [r.name for r in derive_references_from_text(text, PROJECT)[0]] == ["张三", "酒馆"]


# ---------- speaker 位不计入参考图 ----------


def test_speaker_position_is_excluded_from_references():
    text = "@[酒馆] 内景，人声嘈杂。\n@[张三]：{我来了}"
    preview = build_script_preview(text, PROJECT, _SOFT)
    assert [r.name for r in derive_references_from_text(text, PROJECT)[0]] == ["酒馆"]
    # 纯画外角色没有参考图，但 utterance 照常
    assert [u.speaker for u in preview.utterances] == ["张三"]


def test_padded_speaker_uses_registered_character_without_warning():
    preview = build_script_preview(
        "开场。\n@[ 张三 ]：{我来了}",
        PROJECT_WITHOUT_SCENES,
        VoiceRenderSettings(voice_consistency="native", max_reference_audio=3, audio_ready={" 张三 "}),
    )

    assert [u.speaker for u in preview.utterances] == ["张三"]
    assert preview.warnings == []


def test_extract_mentions_skips_speaker_position():
    """两条派生路径（script_plan 工具与审阅回写）共用的口径出口。"""
    text = "@[酒馆] 内景。\n@[张三]：{我来了}\n@[张三] 抬眼。"
    assert extract_mentions(text) == ["酒馆", "张三"]
    assert extract_mentions("@[张三]：{我来了}") == []


def test_dialogue_only_line_derives_utterance_without_reference():
    """整行只有台词：参考图与 utterance 两侧口径须一致，说话人不进参考图。"""
    text = "@[张三]：{我来了}"
    preview = build_script_preview(text, PROJECT, _SOFT)
    assert derive_references_from_text(text, PROJECT)[0] == []
    assert [(u.kind, u.speaker) for u in preview.utterances] == [("dialogue", "张三")]
    assert extract_mentions(text) == []


# ---------- 降级可见性 warning ----------


def test_warn_unit_without_scene_when_project_has_scene_assets():
    """未引用场景的单元，画面地点由模型自由决定——项目登记了场景才发得出这条提示。"""
    preview = build_script_preview("中景，@[张三] 推门。", PROJECT, _SOFT)
    assert keys(preview) == [WARN_UNIT_WITHOUT_SCENE]
    assert preview.warnings[0]["params"] == {}


def test_no_scene_warning_when_unit_references_a_scene():
    preview = build_script_preview("@[酒馆] 内景，@[张三] 推门。", PROJECT, _SOFT)
    assert keys(preview) == []


def test_no_scene_warning_when_project_registers_no_scene():
    preview = build_script_preview("中景，@[张三] 推门。", PROJECT_WITHOUT_SCENES, _SOFT)
    assert keys(preview) == []


def test_scene_warning_ignores_reference_image_clipping():
    """场景引用被能力上限裁掉不改变判定：这条问的是作者写没写引用，不是发出去几张图。"""
    preview = build_script_preview(
        "@[张三] 推门进 @[酒馆]。",
        PROJECT,
        _SOFT,
        max_reference_images=1,  # 只留 @[张三]，@[酒馆] 的图被裁掉
    )
    assert keys(preview) == []


def test_warn_unregistered_mention():
    preview = build_script_preview("@[王五] 推门。", PROJECT_WITHOUT_SCENES, _SOFT)
    assert keys(preview) == [WARN_UNREGISTERED_MENTION]
    assert preview.warnings[0]["params"] == {"name": "王五"}


def test_warn_unclosed_brace():
    preview = build_script_preview("他说 {我来了。", PROJECT_WITHOUT_SCENES, _SOFT)
    assert keys(preview) == [WARN_UNCLOSED_BRACE]
    assert preview.warnings[0]["params"]["line"] == 1


def test_warn_braces_not_speech():
    """说话人位写坏时不静默降级成画外音，出 warning 让作者看见这段没被认成台词。"""
    preview = build_script_preview("@[]：{我来了}。", PROJECT_WITHOUT_SCENES, _SOFT)
    assert keys(preview) == [WARN_BRACES_NOT_SPEECH]


def test_warn_unregistered_speaker():
    preview = build_script_preview("开场。\n@[王五]：{我来了}", PROJECT_WITHOUT_SCENES, _SOFT)
    assert keys(preview) == [WARN_UNREGISTERED_SPEAKER]
    assert preview.warnings[0]["params"] == {"name": "王五"}


def test_warn_speaker_without_reference_audio_only_on_native():
    text = "开场。\n@[李四]：{你迟到了}"
    native = build_script_preview(
        text, PROJECT_WITHOUT_SCENES, VoiceRenderSettings(voice_consistency="native", max_reference_audio=3)
    )
    assert keys(native) == [WARN_SPEAKER_WITHOUT_AUDIO]
    soft = build_script_preview(text, PROJECT_WITHOUT_SCENES, VoiceRenderSettings(voice_consistency="soft"))
    assert keys(soft) == []


def test_warn_speaker_audio_unavailable_distinguished_from_unset():
    """``audio_ready`` 非 None 时，字段有值但音频不可用（不在 audio_ready 内）与字段未设置
    要发不同的 warning：前者字段已填好、该去查它指向的音频，后者该去角色设置里补音频。"""
    text = "开场。\n@[张三]：{我来了}\n@[李四]：{你迟到了}"
    preview = build_script_preview(
        text, PROJECT, VoiceRenderSettings(voice_consistency="native", max_reference_audio=3)
    )
    bindings = derive_voice_bindings(
        preview.utterances,
        PROJECT["characters"],
        VoiceRenderSettings(
            voice_consistency="native",
            max_reference_audio=3,
            # 张三字段有值、李四未设置；audio_ready 为空表示执行层一段都没解析出来。
            audio_ready=set(),
        ),
    )
    assert {"key": WARN_SPEAKER_AUDIO_UNAVAILABLE, "params": {"name": "张三"}} in bindings.warnings
    assert {"key": WARN_SPEAKER_WITHOUT_AUDIO, "params": {"name": "张三"}} not in bindings.warnings
    assert {"key": WARN_SPEAKER_WITHOUT_AUDIO, "params": {"name": "李四"}} in bindings.warnings
    assert {"key": WARN_SPEAKER_AUDIO_UNAVAILABLE, "params": {"name": "李四"}} not in bindings.warnings


@pytest.mark.parametrize("registered", [_NAME_NFC, _NAME_NFD], ids=["登记NFC", "登记NFD"])
@pytest.mark.parametrize("written", [_NAME_NFC, _NAME_NFD], ids=["出场NFC", "出场NFD"])
def test_combining_char_speaker_binds_audio_in_every_encoding_pairing(registered: str, written: str):
    """组合字符角色名的四种 NFC/NFD 配对绑定结果一致：不得因编码形式差异静默降级。

    「未登记」与「无可用音频」两条 warning 都不发——它们不阻断生成，漏发的后果是用户拿到
    一条没绑上音色的成片，而不是一个能排查的报错。
    """
    project = {
        "characters": {registered: {"reference_audio": "assets/audio/x.wav"}},
        "scenes": {},
        "props": {},
    }
    text = f"开场。\n@[{written}]：{{Tôi đến rồi.}}"

    preview = build_script_preview(
        text, project, VoiceRenderSettings(voice_consistency="native", max_reference_audio=3)
    )
    assert keys(preview) == []

    # 执行层口径：audio_ready 由 resolve_reference_audio_paths 按资产表的 key 建，同样可能是任一形式
    bindings = derive_voice_bindings(
        preview.utterances,
        project["characters"],
        VoiceRenderSettings(
            voice_consistency="native",
            max_reference_audio=3,
            audio_ready={registered},
            requires_reference_image=True,
        ),
        speakers_with_reference_image={registered},
    )
    assert bindings.speakers == [_NAME_NFC]
    assert bindings.audio_speakers == [_NAME_NFC]
    assert bindings.warnings == []


def test_derive_voice_bindings_degrades_on_malformed_character_entry():
    """执行层传入 ``audio_ready`` 时，角色条目非 dict（外部写坏 project.json）不得崩溃——
    只是 audio_field_set 判定不到值，按「未设置」降级，而不是让 ``.get`` 抛 AttributeError。"""
    text = "开场。\n@[张三]：{我来了}"
    preview = build_script_preview(
        text, {"characters": {"张三": "bad"}}, VoiceRenderSettings(voice_consistency="native")
    )
    bindings = derive_voice_bindings(
        preview.utterances,
        {"张三": "bad"},
        VoiceRenderSettings(voice_consistency="native", max_reference_audio=3, audio_ready=set()),
    )
    assert {"key": WARN_SPEAKER_WITHOUT_AUDIO, "params": {"name": "张三"}} in bindings.warnings


def test_warn_speaker_audio_needs_image_when_backend_requires_per_image_attachment():
    """纯画外 speaker（台词记号的 speaker 位不产生参考图）遇到要求逐图挂载音频的 backend
    （如 wan2.7-r2v）时须与执行层同一份判定：预览不能显示已绑定，执行时才降级。"""
    text = "@[张三]：{我来了}"
    preview = build_script_preview(
        text,
        PROJECT_WITHOUT_SCENES,
        VoiceRenderSettings(voice_consistency="native", max_reference_audio=3, requires_reference_image=True),
    )
    assert keys(preview) == [WARN_SPEAKER_AUDIO_NEEDS_IMAGE]
    assert preview.warnings[0]["params"] == {"name": "张三"}


def test_warn_speaker_audio_needs_image_when_image_clipped_by_reference_limit():
    """执行层会先把 references 裁到能力上限再渲染（图片N 编号与实际发出的参考图严格等长），
    预览须按同一条裁剪线判定，否则超限角色的图被裁掉后预览仍显示音频已绑定，执行时才降级。"""
    # 张三写在描述位上才会派生参考图；上限 1 只留 @[酒馆]，张三的图被裁掉才是告警的唯一致因。
    text = "@[酒馆] 内景，@[张三] 推门。\n@[张三]：{我来了}"
    preview = build_script_preview(
        text,
        PROJECT,
        VoiceRenderSettings(voice_consistency="native", max_reference_audio=3, requires_reference_image=True),
        max_reference_images=1,  # 只留 @[酒馆]，张三的图被裁掉
    )
    assert keys(preview) == [WARN_SPEAKER_AUDIO_NEEDS_IMAGE]
    assert preview.warnings[0]["params"] == {"name": "张三"}


def test_warn_reference_audio_overflow():
    text = "开场。\n@[张三]：{我来了}\n@[旁白者]：{我也在}"
    preview = build_script_preview(
        text, PROJECT_WITHOUT_SCENES, VoiceRenderSettings(voice_consistency="native", max_reference_audio=1)
    )
    assert keys(preview) == [WARN_REFERENCE_AUDIO_OVERFLOW]
    assert preview.warnings[0]["params"] == {"limit": 1, "name": "旁白者"}


def test_warn_silent_model_notice():
    text = "开场。\n@[张三]：{我来了}"
    preview = build_script_preview(
        text, PROJECT_WITHOUT_SCENES, VoiceRenderSettings(voice_consistency="none", model_id="minimax-01")
    )
    assert keys(preview) == [WARN_SILENT_MODEL]
    assert preview.warnings[0]["params"] == {"model": "minimax-01"}


def test_warn_silent_model_notice_covers_voiceover_only_script():
    """画外音同样要渲染，纯画外文稿在无声模型上也该知会。"""
    preview = build_script_preview(
        "开场。\n{那年冬天格外冷}",
        PROJECT_WITHOUT_SCENES,
        VoiceRenderSettings(voice_consistency="none", model_id="minimax-01"),
    )
    assert keys(preview) == [WARN_SILENT_MODEL]


def test_silent_model_notice_not_emitted_without_any_utterance():
    preview = build_script_preview(
        "开场。", PROJECT_WITHOUT_SCENES, VoiceRenderSettings(voice_consistency="none", model_id="m")
    )
    assert preview.warnings == []


# ---------- 本集无声（requested_generate_audio=False） ----------


def test_silent_episode_drops_audio_bindings_on_native_model():
    """无声开关关掉后，A 类模型也不再绑定参考音频——请求里不会带音频段。"""
    text = "开场。\n@[张三]：{我来了}"
    bindings = derive_voice_bindings(
        derive_utterances(text)[0],
        PROJECT["characters"],
        VoiceRenderSettings(voice_consistency="native", requested_generate_audio=False, max_reference_audio=3),
    )
    assert bindings.audio_speakers == []
    assert bindings.speakers == ["张三"]
    assert [w["key"] for w in bindings.warnings] == [WARN_SILENT_EPISODE]


def test_silent_episode_notice_replaces_per_speaker_audio_warnings():
    """无声时不再逐角色报「未设参考音频」——原因是本集无声，不是角色没配音频。"""
    text = "开场。\n@[张三]：{我来了}\n@[李四]：{我也在}"
    preview = build_script_preview(
        text, PROJECT_WITHOUT_SCENES, VoiceRenderSettings(voice_consistency="native", requested_generate_audio=False)
    )
    assert keys(preview) == [WARN_SILENT_EPISODE]
    assert preview.warnings[0]["params"] == {}


def test_silent_episode_notice_takes_precedence_over_silent_model():
    preview = build_script_preview(
        "开场。\n@[张三]：{我来了}",
        PROJECT_WITHOUT_SCENES,
        VoiceRenderSettings(voice_consistency="none", requested_generate_audio=False, model_id="minimax-01"),
    )
    assert keys(preview) == [WARN_SILENT_EPISODE]


def test_silent_episode_notice_not_emitted_without_any_utterance():
    preview = build_script_preview(
        "开场。",
        PROJECT_WITHOUT_SCENES,
        VoiceRenderSettings(voice_consistency="native", requested_generate_audio=False),
    )
    assert preview.warnings == []


def test_silent_episode_keeps_utterances_for_lip_sync():
    """台词照常派生：无声视频里台词仍下发，供应商可用作口型参考。"""
    preview = build_script_preview(
        "开场。\n@[张三]：{我来了}",
        PROJECT,
        VoiceRenderSettings(voice_consistency="native", requested_generate_audio=False),
    )
    assert [u.text for u in preview.utterances] == ["我来了"]


# ---------- i18n ----------

WARNING_KEYS = [
    WARN_UNREGISTERED_MENTION,
    WARN_UNCLOSED_BRACE,
    WARN_BRACES_NOT_SPEECH,
    WARN_UNREGISTERED_SPEAKER,
    WARN_SPEAKER_WITHOUT_AUDIO,
    WARN_REFERENCE_AUDIO_OVERFLOW,
    WARN_SILENT_MODEL,
    WARN_SILENT_EPISODE,
    WARN_SPEAKER_AUDIO_NEEDS_IMAGE,
]

WARNING_PARAMS = {
    WARN_UNREGISTERED_MENTION: {"name": "王五"},
    WARN_UNCLOSED_BRACE: {"line": 1, "excerpt": "他说 {我来了。"},
    WARN_BRACES_NOT_SPEECH: {"line": 2},
    WARN_UNREGISTERED_SPEAKER: {"name": "王五"},
    WARN_SPEAKER_WITHOUT_AUDIO: {"name": "李四"},
    WARN_REFERENCE_AUDIO_OVERFLOW: {"limit": 3, "name": "李四"},
    WARN_SILENT_MODEL: {"model": "minimax-01"},
    WARN_SILENT_EPISODE: {},
    WARN_SPEAKER_AUDIO_NEEDS_IMAGE: {"name": "李四"},
}


@pytest.mark.parametrize("locale", ["zh", "en", "vi"])
@pytest.mark.parametrize("key", WARNING_KEYS)
def test_warning_messages_render_in_all_locales(locale: str, key: str):
    assert key in MESSAGES[locale]
    text = _(key, locale=locale, **WARNING_PARAMS[key])
    assert text != key
    # 占位符全部被替换（转义的示例语法 `{{台词}}` 渲染成字面花括号，不算残留占位符）
    for param in WARNING_PARAMS[key]:
        assert f"{{{param}}}" not in text


def test_zh_braces_warning_shows_literal_syntax_example():
    text = _(WARN_BRACES_NOT_SPEECH, locale="zh", line=2)
    assert "@[角色]{台词}" in text
