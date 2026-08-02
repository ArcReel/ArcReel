"""分镜文稿台词规范行的派生与七条降级可见性 warning。"""

import pytest

from lib.i18n import MESSAGES, _
from lib.reference_video.script_preview import (
    WARN_DIALOGUE_INLINE,
    WARN_REFERENCE_AUDIO_OVERFLOW,
    WARN_SILENT_MODEL,
    WARN_SPEAKER_AUDIO_NEEDS_IMAGE,
    WARN_SPEAKER_WITHOUT_AUDIO,
    WARN_UNCLOSED_BRACE,
    WARN_UNREGISTERED_MENTION,
    WARN_UNREGISTERED_SPEAKER,
    build_script_preview,
)
from lib.reference_video.shot_parser import (
    extract_mentions,
    match_dialogue_line,
    match_voiceover_line,
)

pytestmark = pytest.mark.unit

PROJECT = {
    "characters": {
        "张三": {"description": "x", "reference_audio": "assets/audio/zhangsan.wav"},
        "李四": {"description": "x"},
        "旁白者": {"description": "x", "reference_audio": "assets/audio/pangbai.wav"},
    },
    "scenes": {"酒馆": {"description": "x"}},
    "props": {},
}


def keys(preview) -> list[str]:
    return [w["key"] for w in preview.warnings]


# ---------- 规范行匹配原语 ----------


@pytest.mark.parametrize(
    "line",
    ["@[张三]：{我来了}", "@[张三]:{我来了}", "  @[张三] ： {我来了}  ", "@张三：{我来了}"],
)
def test_dialogue_line_accepts_wrapped_bare_and_both_colons(line: str):
    assert match_dialogue_line(line) == ("张三", "我来了")


@pytest.mark.parametrize(
    "line",
    [
        "中景，@[张三]：{我来了} 说完转身",  # 台词混写描述行
        "@[张三]：我来了",  # 无花括号
        "@[张三]：{我来了",  # 未闭合
        "他说 @[张三]：{我来了}",  # 行首不是 mention
        "@[张三]{我来了}",  # 缺冒号
        "@[ ]：{我来了}",  # speaker 位全为空白
        "@[张三]：{}",  # 空台词
        "@[张三]：{   }",  # 台词只有空白
    ],
)
def test_dialogue_line_rejects_non_normative(line: str):
    assert match_dialogue_line(line) is None


def test_blank_speaker_degrades_to_warning_instead_of_raising():
    """speaker 位空白不得构造非法 Utterance——只读派生要出 warning，不能抛校验错。"""
    preview = build_script_preview("镜头1：中景。\n@[ ]：{我来了}", PROJECT)
    assert preview.utterances == []
    # 非规范行 → 台词混写 warning；空白名同时作为未登记 mention 被点名。
    assert keys(preview) == [WARN_UNREGISTERED_MENTION, WARN_DIALOGUE_INLINE]


def test_voiceover_line_is_bare_braces():
    assert match_voiceover_line("  {那年冬天格外冷}  ") == "那年冬天格外冷"
    assert match_voiceover_line("旁白：{那年冬天}") is None


@pytest.mark.parametrize("line", ["{}", "{   }"])
def test_blank_braces_are_not_utterances(line: str):
    """空台词不派生：``Utterance`` 与 DataValidator 都要求 text 非空。"""
    assert match_voiceover_line(line) is None


def test_blank_braces_degrade_to_warning():
    preview = build_script_preview("镜头1：中景。\n@[张三]：{}\n{   }", PROJECT)
    assert preview.utterances == []
    assert keys(preview) == [WARN_DIALOGUE_INLINE, WARN_DIALOGUE_INLINE]


# ---------- 派生 ----------


def test_normative_lines_derive_dialogue_and_voiceover():
    text = "镜头1：@[张三] 推门进来。\n@[张三]：{我来了}\n{那年冬天格外冷}\n镜头2：@[李四] 抬眼。\n@[李四]：{你迟到了}"
    preview = build_script_preview(text, PROJECT)
    assert [(u.shot_index, u.utterance.kind, u.utterance.speaker, u.utterance.text) for u in preview.utterances] == [
        (1, "dialogue", "张三", "我来了"),
        (1, "voiceover", None, "那年冬天格外冷"),
        (2, "dialogue", "李四", "你迟到了"),
    ]


def test_inline_dialogue_is_not_derived_and_warns():
    preview = build_script_preview("镜头1：中景，@[张三] 笑着说 {我来了}。", PROJECT)
    assert preview.utterances == []
    assert WARN_DIALOGUE_INLINE in keys(preview)


def test_script_without_dialogue_symbols_derives_nothing():
    """存量文稿零迁移：没有台词符号 → utterances 自然为空、无 warning。"""
    preview = build_script_preview("镜头1：中景，@[张三] 推门进 @[酒馆]。", PROJECT)
    assert preview.utterances == []
    assert preview.warnings == []
    assert [r.name for r in preview.references] == ["张三", "酒馆"]


# ---------- speaker 位不计入参考图 ----------


def test_speaker_position_is_excluded_from_references():
    text = "镜头1：@[酒馆] 内景，人声嘈杂。\n@[张三]：{我来了}"
    preview = build_script_preview(text, PROJECT)
    assert [r.name for r in preview.references] == ["酒馆"]
    # 纯画外角色没有参考图，但 utterance 照常
    assert [u.utterance.speaker for u in preview.utterances] == ["张三"]


def test_extract_mentions_skips_speaker_position():
    """两条派生路径（step1 工具与审阅回写）共用的口径出口。"""
    text = "镜头1：@[酒馆] 内景。\n@[张三]：{我来了}\n镜头2：@[张三] 抬眼。"
    assert extract_mentions(text) == ["酒馆", "张三"]
    assert extract_mentions("@[张三]：{我来了}") == []


def test_dialogue_on_shot_header_line_derives_utterance_without_reference():
    """写在 header 同一行的台词：切分后即规范行，参考图与 utterance 两侧口径须一致。"""
    text = "镜头1：@[张三]：{我来了}"
    preview = build_script_preview(text, PROJECT)
    assert preview.references == []
    assert [(u.utterance.kind, u.utterance.speaker) for u in preview.utterances] == [("dialogue", "张三")]
    assert extract_mentions(text) == []


# ---------- 七条 warning ----------


def test_warn_unregistered_mention():
    preview = build_script_preview("镜头1：@[王五] 推门。", PROJECT)
    assert keys(preview) == [WARN_UNREGISTERED_MENTION]
    assert preview.warnings[0]["params"] == {"name": "王五"}


def test_warn_unclosed_brace():
    preview = build_script_preview("镜头1：他说 {我来了。", PROJECT)
    assert keys(preview) == [WARN_UNCLOSED_BRACE]
    assert preview.warnings[0]["params"]["shot"] == 1


def test_warn_dialogue_inline():
    preview = build_script_preview("镜头1：@[张三] 说 {我来了}。", PROJECT)
    assert keys(preview) == [WARN_DIALOGUE_INLINE]


def test_warn_unregistered_speaker():
    preview = build_script_preview("镜头1：开场。\n@[王五]：{我来了}", PROJECT)
    assert keys(preview) == [WARN_UNREGISTERED_SPEAKER]
    assert preview.warnings[0]["params"] == {"name": "王五"}


def test_warn_speaker_without_reference_audio_only_on_native():
    text = "镜头1：开场。\n@[李四]：{你迟到了}"
    native = build_script_preview(text, PROJECT, voice_consistency="native", max_reference_audio=3)
    assert keys(native) == [WARN_SPEAKER_WITHOUT_AUDIO]
    soft = build_script_preview(text, PROJECT, voice_consistency="soft")
    assert keys(soft) == []


def test_warn_speaker_audio_needs_image_when_backend_requires_per_image_attachment():
    """纯画外 speaker（台词行 speaker 位不产生参考图）遇到要求逐图挂载音频的 backend
    （如 wan2.7-r2v）时须与执行层同一份判定：预览不能显示已绑定，执行时才降级。"""
    text = "@[张三]：{我来了}"
    preview = build_script_preview(
        text,
        PROJECT,
        voice_consistency="native",
        max_reference_audio=3,
        audio_requires_reference_image=True,
    )
    assert keys(preview) == [WARN_SPEAKER_AUDIO_NEEDS_IMAGE]
    assert preview.warnings[0]["params"] == {"name": "张三"}


def test_warn_speaker_audio_needs_image_when_image_clipped_by_reference_limit():
    """执行层会先把 references 裁到能力上限再渲染（图片N 编号与实际发出的参考图严格等长），
    预览须按同一条裁剪线判定，否则超限角色的图被裁掉后预览仍显示音频已绑定，执行时才降级。"""
    text = "镜头1：@[酒馆] 内景。\n@[张三]：{我来了}"
    preview = build_script_preview(
        text,
        PROJECT,
        voice_consistency="native",
        max_reference_audio=3,
        audio_requires_reference_image=True,
        max_reference_images=1,  # 只留 @[酒馆]，张三的图被裁掉
    )
    assert keys(preview) == [WARN_SPEAKER_AUDIO_NEEDS_IMAGE]
    assert preview.warnings[0]["params"] == {"name": "张三"}


def test_warn_reference_audio_overflow():
    text = "镜头1：开场。\n@[张三]：{我来了}\n@[旁白者]：{我也在}"
    preview = build_script_preview(text, PROJECT, voice_consistency="native", max_reference_audio=1)
    assert keys(preview) == [WARN_REFERENCE_AUDIO_OVERFLOW]
    assert preview.warnings[0]["params"] == {"limit": 1, "name": "旁白者"}


def test_warn_silent_model_notice():
    text = "镜头1：开场。\n@[张三]：{我来了}"
    preview = build_script_preview(text, PROJECT, voice_consistency="none", model_id="minimax-01")
    assert keys(preview) == [WARN_SILENT_MODEL]
    assert preview.warnings[0]["params"] == {"model": "minimax-01"}


def test_warn_silent_model_notice_covers_voiceover_only_script():
    """画外音同样要渲染，纯画外文稿在无声模型上也该知会。"""
    preview = build_script_preview(
        "镜头1：开场。\n{那年冬天格外冷}", PROJECT, voice_consistency="none", model_id="minimax-01"
    )
    assert keys(preview) == [WARN_SILENT_MODEL]


def test_silent_model_notice_not_emitted_without_any_utterance():
    preview = build_script_preview("镜头1：开场。", PROJECT, voice_consistency="none", model_id="m")
    assert preview.warnings == []


# ---------- i18n ----------

WARNING_KEYS = [
    WARN_UNREGISTERED_MENTION,
    WARN_UNCLOSED_BRACE,
    WARN_DIALOGUE_INLINE,
    WARN_UNREGISTERED_SPEAKER,
    WARN_SPEAKER_WITHOUT_AUDIO,
    WARN_REFERENCE_AUDIO_OVERFLOW,
    WARN_SILENT_MODEL,
    WARN_SPEAKER_AUDIO_NEEDS_IMAGE,
]

WARNING_PARAMS = {
    WARN_UNREGISTERED_MENTION: {"name": "王五"},
    WARN_UNCLOSED_BRACE: {"shot": 1, "excerpt": "他说 {我来了。"},
    WARN_DIALOGUE_INLINE: {"shot": 2},
    WARN_UNREGISTERED_SPEAKER: {"name": "王五"},
    WARN_SPEAKER_WITHOUT_AUDIO: {"name": "李四"},
    WARN_REFERENCE_AUDIO_OVERFLOW: {"limit": 3, "name": "李四"},
    WARN_SILENT_MODEL: {"model": "minimax-01"},
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


def test_zh_inline_warning_shows_literal_syntax_example():
    text = _(WARN_DIALOGUE_INLINE, locale="zh", shot=2)
    assert "@[角色]：{台词}" in text
