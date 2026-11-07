"""Derive speech markers from video-unit bodies and expose degradation through warnings."""

import unicodedata

import pytest

from lib.i18n import MESSAGES, _
from lib.script.reference_video.script_preview import (
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
from lib.script.reference_video.text_parser import (
    derive_references_from_text,
    extract_mentions,
    line_speech_marks,
)
from lib.script.reference_video.voice_settings import VoiceRenderSettings

PROJECT = {
    "characters": {
        "张三": {"description": "x", "reference_audio": "assets/audio/zhangsan.wav"},
        "李四": {"description": "x"},
        "旁白者": {"description": "x", "reference_audio": "assets/audio/pangbai.wav"},
    },
    "scenes": {"酒馆": {"description": "x"}},
    "props": {},
}

#: A project with no registered scenes: no unreferenced-scene notice when no scene can be mentioned, keeping unrelated
#: syntax/voice cases focused on one concern.
PROJECT_WITHOUT_SCENES = {**PROJECT, "scenes": {}}

#: Vietnamese combining-character names: visually identical, byte-distinct encodings test asset-name comparison coordinates.
_NAME_NFC = unicodedata.normalize("NFC", "Hiếu")
_NAME_NFD = unicodedata.normalize("NFD", "Hiếu")

#: Voice settings for nonvoice cases: ``soft`` has audio without reference clips. Preview requires ``settings``,
#: so these cases still supply a tier using field defaults.
_SOFT = VoiceRenderSettings()


def keys(preview) -> list[str]:
    return [w["key"] for w in preview.warnings]


# Inline-marker matching primitives.


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
        "@[张三]：我来了",  # No braces.
        "@[张三]：{我来了",  # Unclosed.
        "@[ ]：{我来了}",  # Whitespace-only speaker.
        "@[张三]：{}",  # Empty dialogue.
        "@[张三]：{   }",  # Whitespace-only dialogue.
    ],
)
def test_line_without_valid_mark_derives_nothing(line: str):
    assert marks(line) == []


def test_speaker_must_be_adjacent_to_the_braces():
    """A mention separated by descriptive text is not a speaker; do not guess from the nearest inline mention."""
    assert marks("他说 @[张三] 转身，屋里传出 {我来了}") == [("", "我来了")]


def test_blank_speaker_degrades_to_warning_instead_of_raising():
    """Whitespace-only speakers must not construct invalid Utterance; read-only derivation warns instead of raising validation errors."""
    preview = build_script_preview("中景。\n@[ ]：{我来了}", PROJECT_WITHOUT_SCENES, _SOFT)
    assert preview.utterances == []
    # Invalid marker yields an unrecognized-braces warning; also name the whitespace mention as unregistered.
    assert keys(preview) == [WARN_UNREGISTERED_MENTION, WARN_BRACES_NOT_SPEECH]


def test_voiceover_mark_is_bare_braces_anywhere():
    assert marks("  {那年冬天格外冷}  ") == [("", "那年冬天格外冷")]
    assert marks("旁白：{那年冬天}") == [("", "那年冬天")]
    assert marks("镜头切到窗外。{那年冬天}") == [("", "那年冬天")]


@pytest.mark.parametrize("line", ["{}", "{   }"])
def test_blank_braces_are_not_utterances(line: str):
    """Do not derive empty dialogue; both ``Utterance`` and DataValidator require nonempty text."""
    assert marks(line) == []


def test_blank_braces_degrade_to_warning():
    preview = build_script_preview("中景。\n@[张三]：{}\n{   }", PROJECT_WITHOUT_SCENES, _SOFT)
    assert preview.utterances == []
    assert keys(preview) == [WARN_BRACES_NOT_SPEECH, WARN_BRACES_NOT_SPEECH]


# Derivation.


def test_normative_lines_derive_dialogue_and_voiceover():
    text = "@[张三] 推门进来。\n@[张三]：{我来了}\n{那年冬天格外冷}\n@[李四] 抬眼。\n@[李四]：{你迟到了}"
    preview = build_script_preview(text, PROJECT, _SOFT)
    assert [(u.kind, u.speaker, u.text) for u in preview.utterances] == [
        ("dialogue", "张三", "我来了"),
        ("voiceover", None, "那年冬天格外冷"),
        ("dialogue", "李四", "你迟到了"),
    ]


def test_inline_dialogue_is_derived_alongside_the_description():
    """Derive dialogue after same-line visual description normally; speakers bind voices only, while described assets enter references normally."""
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
    """Bodies without dialogue syntax naturally yield empty utterances without warnings."""
    text = "中景，@[张三] 推门进 @[酒馆]。"
    preview = build_script_preview(text, PROJECT, _SOFT)
    assert preview.utterances == []
    assert preview.warnings == []
    assert [r.name for r in derive_references_from_text(text, PROJECT)[0]] == ["张三", "酒馆"]


# Speaker positions do not enter reference images.


def test_speaker_position_is_excluded_from_references():
    text = "@[酒馆] 内景，人声嘈杂。\n@[张三]：{我来了}"
    preview = build_script_preview(text, PROJECT, _SOFT)
    assert [r.name for r in derive_references_from_text(text, PROJECT)[0]] == ["酒馆"]
    # Entirely offscreen characters lack reference images but still have utterances.
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
    """Shared semantic exit for both derivation paths: script_plan tools and review writeback."""
    text = "@[酒馆] 内景。\n@[张三]：{我来了}\n@[张三] 抬眼。"
    assert extract_mentions(text) == ["酒馆", "张三"]
    assert extract_mentions("@[张三]：{我来了}") == []


def test_dialogue_only_line_derives_utterance_without_reference():
    """Dialogue-only lines yield consistent reference/utterance semantics: speakers do not become reference images."""
    text = "@[张三]：{我来了}"
    preview = build_script_preview(text, PROJECT, _SOFT)
    assert derive_references_from_text(text, PROJECT)[0] == []
    assert [(u.kind, u.speaker) for u in preview.utterances] == [("dialogue", "张三")]
    assert extract_mentions(text) == []


# Visible-degradation warnings.


def test_warn_unit_without_scene_when_project_has_scene_assets():
    """Without scene references, the model chooses locations freely; emit this notice only when projects have registered scenes."""
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
    """Trimming scene references by capability limits does not change this judgment; ask whether authors wrote a reference, not how many images were sent."""
    preview = build_script_preview(
        "@[张三] 推门进 @[酒馆]。",
        PROJECT,
        _SOFT,
        max_reference_images=1,  # Retain only @[张三]; trim the @[酒馆] image.
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
    """Malformed speakers must not silently become voiceover; warn authors that this part was not recognized as dialogue."""
    preview = build_script_preview("@[]：{我来了}。", PROJECT_WITHOUT_SCENES, _SOFT)
    assert keys(preview) == [WARN_BRACES_NOT_SPEECH]


def test_warn_unregistered_speaker():
    preview = build_script_preview("开场。\n@[王五]：{我来了}", PROJECT_WITHOUT_SCENES, _SOFT)
    assert keys(preview) == [WARN_UNREGISTERED_SPEAKER]
    assert preview.warnings[0]["params"] == {"name": "王五"}


#: 张三 with one registered derivative, without scenes to avoid an additional unreferenced-scene warning.
PROJECT_WITH_DERIVATIVE = {
    **PROJECT_WITHOUT_SCENES,
    "characters": {**PROJECT["characters"], "张三": {"derivatives": {"劲装": {}}}},
}


class TestDerivativeWarnings:
    """Editors only warn on ``@[角色/衍生]`` without blocking (docs/adr/0064 / 0072)."""

    def test_registered_derivative_is_silent(self):
        preview = build_script_preview("@[张三/劲装] 推门。@[张三/劲装]{我来了}", PROJECT_WITH_DERIVATIVE, _SOFT)

        assert keys(preview) == []

    def test_unregistered_derivative_in_a_mention_warns(self):
        preview = build_script_preview("@[张三/夜行衣] 推门。", PROJECT_WITH_DERIVATIVE, _SOFT)

        assert keys(preview) == [WARN_UNREGISTERED_MENTION]
        assert preview.warnings[0]["params"] == {"name": "张三/夜行衣"}

    def test_unregistered_derivative_in_the_speaker_slot_warns(self):
        """Speaker positions do not derive references; report unregistered speakers separately here."""
        preview = build_script_preview("开场。\n@[张三/夜行衣]{我来了}", PROJECT_WITH_DERIVATIVE, _SOFT)

        assert keys(preview) == [WARN_UNREGISTERED_MENTION]
        assert preview.warnings[0]["params"] == {"name": "张三/夜行衣"}

    def test_same_unregistered_derivative_written_in_both_slots_warns_once(self):
        """Outside-marker mentions and speakers are independently deduplicated sources; report the same name only once."""
        preview = build_script_preview("@[张三/夜行衣] 推门。@[张三/夜行衣]{我来了}", PROJECT_WITH_DERIVATIVE, _SOFT)

        assert keys(preview) == [WARN_UNREGISTERED_MENTION]
        assert preview.warnings[0]["params"] == {"name": "张三/夜行衣"}

    def test_unregistered_base_reports_only_the_speaker_warning(self):
        preview = build_script_preview("开场。\n@[无名氏/夜行衣]{我来了}", PROJECT_WITH_DERIVATIVE, _SOFT)

        assert keys(preview) == [WARN_UNREGISTERED_SPEAKER]
        assert preview.warnings[0]["params"] == {"name": "无名氏"}

    def test_derivative_speaker_binds_the_base_voice(self):
        preview = build_script_preview(
            "开场。\n@[张三/劲装]{我来了}",
            PROJECT_WITH_DERIVATIVE,
            VoiceRenderSettings(voice_consistency="native", max_reference_audio=3),
        )

        assert [u.speaker for u in preview.utterances] == ["张三"]
        assert keys(preview) == [WARN_SPEAKER_WITHOUT_AUDIO]


def test_warn_speaker_without_reference_audio_only_on_native():
    text = "开场。\n@[李四]：{你迟到了}"
    native = build_script_preview(
        text, PROJECT_WITHOUT_SCENES, VoiceRenderSettings(voice_consistency="native", max_reference_audio=3)
    )
    assert keys(native) == [WARN_SPEAKER_WITHOUT_AUDIO]
    soft = build_script_preview(text, PROJECT_WITHOUT_SCENES, VoiceRenderSettings(voice_consistency="soft"))
    assert keys(soft) == []


def test_warn_speaker_audio_unavailable_distinguished_from_unset():
    """With non-None ``audio_ready``, distinguish configured but unavailable audio (absent from audio_ready) from unconfigured fields:
    the former requires checking the referenced audio, while the latter requires adding audio in character settings."""
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
            # 张三 is configured, 李四 is not; empty audio_ready means execution resolved no usable clips.
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
    """All four NFC/NFD combining-name pairings bind identically, without silent encoding-based degradation.

    Emit neither unregistered nor unavailable-audio warnings: these do not block generation, so missing recognition
    yields an unbound voice in final video instead of a diagnosable error.
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

    # Execution builds audio_ready from asset-table keys through resolve_reference_audio_paths; keys may use either form.
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
    """With execution-supplied ``audio_ready``, nondict character entries (externally corrupted project.json) must not crash;
    audio_field_set simply finds no value and degrades to unconfigured, rather than ``.get`` raising AttributeError."""
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
    """Offscreen speakers (speaker positions derive no references) must match execution judgments for backends requiring
    per-image audio attachment (such as wan2.7-r2v); preview must not claim binding that execution later degrades."""
    text = "@[张三]：{我来了}"
    preview = build_script_preview(
        text,
        PROJECT_WITHOUT_SCENES,
        VoiceRenderSettings(voice_consistency="native", max_reference_audio=3, requires_reference_image=True),
    )
    assert keys(preview) == [WARN_SPEAKER_AUDIO_NEEDS_IMAGE]
    assert preview.warnings[0]["params"] == {"name": "张三"}


def test_warn_speaker_audio_needs_image_when_image_clipped_by_reference_limit():
    """Execution trims references to capability limits before rendering (图片N numbering exactly matches sent image count).
    Preview must use the same trimming boundary; otherwise trimmed characters still show bound audio until execution degrades it."""
    # 张三 must appear in description to derive an image; limit 1 keeps only @[酒馆], making its trimming the sole warning cause.
    text = "@[酒馆] 内景，@[张三] 推门。\n@[张三]：{我来了}"
    preview = build_script_preview(
        text,
        PROJECT,
        VoiceRenderSettings(voice_consistency="native", max_reference_audio=3, requires_reference_image=True),
        max_reference_images=1,  # Retain only @[酒馆]; trim 张三's image.
    )
    assert keys(preview) == [WARN_SPEAKER_AUDIO_NEEDS_IMAGE]
    assert preview.warnings[0]["params"] == {"name": "张三"}


def test_preview_does_not_clip_references_when_model_accepts_none():
    """At limit 0, execution does not trim references, leaving validation to reject them; preview follows that boundary without blaming audio degradation on trimming."""
    text = "@[酒馆] 内景，@[张三] 推门。\n@[张三]：{我来了}"
    preview = build_script_preview(
        text,
        PROJECT,
        VoiceRenderSettings(voice_consistency="native", max_reference_audio=3, requires_reference_image=True),
        max_reference_images=0,
    )
    assert keys(preview) == []


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
    assert preview.warnings[0]["params"] == {"provider": "", "model": "minimax-01"}


def test_silent_model_notice_skips_a_voiceover_only_script():
    """Unattributed narration is entirely omitted from prompts; dialogue-only-as-prompt-reference notices misdirect diagnosis for pure narration."""
    preview = build_script_preview(
        "开场。\n{那年冬天格外冷}",
        PROJECT_WITHOUT_SCENES,
        VoiceRenderSettings(voice_consistency="none", model_id="minimax-01"),
    )
    assert preview.warnings == []


def test_silent_model_notice_covers_an_unregistered_speaker():
    """Render unregistered speakers' dialogue unchanged, still informing users that it is only prompt reference."""
    preview = build_script_preview(
        "开场。\n@[路人]：{我也在}",
        PROJECT_WITHOUT_SCENES,
        VoiceRenderSettings(voice_consistency="none", model_id="minimax-01"),
    )
    assert WARN_SILENT_MODEL in keys(preview)


def test_silent_model_notice_not_emitted_without_any_utterance():
    preview = build_script_preview(
        "开场。", PROJECT_WITHOUT_SCENES, VoiceRenderSettings(voice_consistency="none", model_id="m")
    )
    assert preview.warnings == []


# Silent episode (requested_generate_audio=False).


def test_silent_episode_drops_audio_bindings_on_native_model():
    """Disabling audio stops even class A models binding reference audio; requests contain no clips."""
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
    """Silence suppresses per-character missing-reference-audio warnings; the cause is episode silence, not unconfigured characters."""
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
    """Dialogue derives normally; silent videos still send dialogue for provider lip-sync reference."""
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
    WARN_SILENT_MODEL: {"provider": "", "model": "minimax-01"},
    WARN_SILENT_EPISODE: {},
    WARN_SPEAKER_AUDIO_NEEDS_IMAGE: {"name": "李四"},
}


@pytest.mark.parametrize("locale", ["zh", "en", "vi"])
@pytest.mark.parametrize("key", WARNING_KEYS)
def test_warning_messages_render_in_all_locales(locale: str, key: str):
    assert key in MESSAGES[locale]
    text = _(key, locale=locale, **WARNING_PARAMS[key])
    assert text != key
    # Replace all placeholders; escaped example `{{台词}}` renders literal braces, not a residual placeholder.
    for param in WARNING_PARAMS[key]:
        assert f"{{{param}}}" not in text


def test_zh_braces_warning_shows_literal_syntax_example():
    text = _(WARN_BRACES_NOT_SPEECH, locale="zh", line=2)
    assert "@[角色]{台词}" in text
