"""三段论渲染管线：A/B/C 三档注入差异、编号顺序契约与降级 warning。"""

from __future__ import annotations

import pytest

from lib.reference_video.prompt_render import (
    render_unit_prompt,
    resolve_reference_audio_paths,
)
from lib.reference_video.script_preview import (
    WARN_REFERENCE_AUDIO_OVERFLOW,
    WARN_SILENT_MODEL,
    WARN_SPEAKER_AUDIO_NEEDS_IMAGE,
    WARN_SPEAKER_WITHOUT_AUDIO,
    WARN_UNCLOSED_BRACE,
    WARN_UNREGISTERED_MENTION,
    WARN_UNREGISTERED_SPEAKER,
)
from lib.script_models import ReferenceResource

pytestmark = pytest.mark.unit


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


def _refs(*pairs):
    return [ReferenceResource(type=t, name=n) for t, n in pairs]


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
        voice_consistency="native",
        max_reference_audio=3,
        model_id="doubao-seedance-2-0",
        style="写实电影感",
    )
    # 音频顺序即请求字段顺序，也即 @音频N 编号
    assert rendered.audio_speakers == ["张三", "李四"]
    assert "<张三>的台词音色参考 @音频1，声音特征：低沉沙哑的男声。" in rendered.prompt
    assert "<李四>的台词音色参考 @音频2，声音特征：清亮少女音。" in rendered.prompt


def test_first_segment_binds_images_in_reference_order():
    rendered = render_unit_prompt(
        _TEXT,
        _project(),
        _refs(("scene", "酒馆"), ("character", "张三"), ("prop", "长剑")),
        voice_consistency="soft",
    )
    assert rendered.prompt.startswith("<酒馆>@图片1、<张三>@图片2、<长剑>@图片3。")


def test_speaker_position_never_produces_a_reference_image():
    """李四只在规范台词行的 speaker 位出现：无参考图绑定，但音色声明与台词渲染照常。"""
    rendered = render_unit_prompt(
        _TEXT,
        _project(),
        _refs(("scene", "酒馆"), ("character", "张三"), ("prop", "长剑")),
        voice_consistency="native",
        max_reference_audio=3,
    )
    assert "<李四>@图片" not in rendered.prompt
    assert "<李四>的台词音色参考 @音频2" in rendered.prompt
    assert "<李四>说 {你终于来了。}" in rendered.prompt


def test_soft_tier_declares_voice_style_without_audio_designation():
    rendered = render_unit_prompt(
        _TEXT,
        _project(),
        _refs(("character", "张三")),
        voice_consistency="soft",
        max_reference_audio=3,
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
        voice_consistency="none",
        model_id="minimax-01",
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
        voice_consistency="native",
        max_reference_audio=1,
    )
    assert rendered.audio_speakers == ["张三"]
    assert "<李四>的声音特征：清亮少女音。" in rendered.prompt
    assert "<李四>的台词音色参考" not in rendered.prompt
    assert {"key": WARN_REFERENCE_AUDIO_OVERFLOW, "params": {"limit": 1, "name": "李四"}} in rendered.warnings


def test_speaker_without_reference_audio_warns_and_keeps_voice_style():
    text = "镜头1：黑场。\n@[旁白人]：{很久很久以前。}"
    rendered = render_unit_prompt(
        text,
        _project(),
        [],
        voice_consistency="native",
        max_reference_audio=3,
    )
    assert rendered.audio_speakers == []
    assert "<旁白人>的声音特征：温和中年男声。" in rendered.prompt
    assert {"key": WARN_SPEAKER_WITHOUT_AUDIO, "params": {"name": "旁白人"}} in rendered.warnings


def test_voiceover_line_renders_as_offscreen_speech():
    rendered = render_unit_prompt("镜头1：空镜。\n{多年以后他仍记得这句话。}", _project(), [])
    assert "画外音说 {多年以后他仍记得这句话。}" in rendered.prompt


def test_unregistered_speaker_line_is_sent_verbatim_with_warning():
    rendered = render_unit_prompt("镜头1：黑场。\n@[路人]：{你好。}", _project(), [])
    assert "@[路人]：{你好。}" in rendered.prompt
    assert "说 {你好。}" not in rendered.prompt
    assert {"key": WARN_UNREGISTERED_SPEAKER, "params": {"name": "路人"}} in rendered.warnings


def test_clipped_reference_still_renders_as_subject():
    """被能力上限裁掉参考图的已登记名字仍是画面主体：渲染 <X>，不把编辑器语法发给模型。"""
    rendered = render_unit_prompt(_TEXT, _project(), _refs(("scene", "酒馆")))
    assert "@[张三]" not in rendered.prompt
    assert "@[长剑]" not in rendered.prompt
    assert "<张三> 推门而入，手按 <长剑>。" in rendered.prompt
    # 主体记号与图号解耦：只有随请求发出的那张图才有绑定行
    assert rendered.prompt.startswith("<酒馆>@图片1。")
    assert "<张三>@图片" not in rendered.prompt


def test_unregistered_mention_kept_verbatim_with_warning():
    rendered = render_unit_prompt("镜头1：@[未知资产] 出现。", _project(), [])
    assert "@[未知资产]" in rendered.prompt
    assert {"key": WARN_UNREGISTERED_MENTION, "params": {"name": "未知资产"}} in rendered.warnings


def test_unclosed_brace_line_sent_verbatim_with_warning():
    rendered = render_unit_prompt("镜头1：@[张三] 开口。\n@[张三]：{没有闭合", _project(), _refs(("character", "张三")))
    assert "{没有闭合" in rendered.prompt
    assert any(w["key"] == WARN_UNCLOSED_BRACE for w in rendered.warnings)


def test_legend_and_absolute_seconds_are_gone():
    rendered = render_unit_prompt(_TEXT, _project(), _refs(("character", "张三")), style="写实电影感")
    assert "[图" not in rendered.prompt
    assert "参考图对照" not in rendered.prompt
    assert "禁止出现：BGM、文字字幕、水印。" not in rendered.prompt
    assert "s)" not in rendered.prompt


def test_third_segment_anchors_style_and_constraint_packs():
    rendered = render_unit_prompt("镜头1：空镜。", _project(), [], style="写实电影感")
    assert "整体视觉风格：写实电影感。" in rendered.prompt
    assert "保持无字幕" in rendered.prompt
    assert "不要生成水印" in rendered.prompt
    assert "禁止出现背景音乐。" in rendered.prompt


def test_twin_guard_only_when_two_or_more_character_images():
    single = render_unit_prompt("镜头1：@[张三] 独行。", _project(), _refs(("character", "张三")))
    assert "双胞胎" not in single.prompt
    both = render_unit_prompt(
        "镜头1：@[张三] 与 @[李四] 对峙。",
        _project(),
        _refs(("character", "张三"), ("character", "李四")),
    )
    assert "双胞胎" in both.prompt


def test_legacy_script_without_dialogue_still_renders_three_segments():
    """存量文稿（无台词符号）走新管线：绑定 + 分镜 + 约束包齐备，语义不回退。"""
    rendered = render_unit_prompt(
        "镜头1：@[张三] 走进 @[酒馆]。\n镜头2：他坐下。",
        _project(),
        _refs(("character", "张三"), ("scene", "酒馆")),
        voice_consistency="native",
        max_reference_audio=3,
    )
    assert rendered.audio_speakers == []
    assert rendered.warnings == []
    assert "<张三>@图片1、<酒馆>@图片2。" in rendered.prompt
    assert "镜头1：\n<张三> 走进 <酒馆>。" in rendered.prompt
    assert "镜头2：\n他坐下。" in rendered.prompt


def test_audio_ready_overrides_field_presence(tmp_path):
    """字段指向已删文件时不绑定：编号与实际发出的音频段数严格等长。"""
    rendered = render_unit_prompt(
        _TEXT,
        _project(),
        _refs(("character", "张三")),
        voice_consistency="native",
        max_reference_audio=3,
        audio_ready={"李四"},
    )
    assert rendered.audio_speakers == ["李四"]
    assert "<李四>的台词音色参考 @音频1" in rendered.prompt
    assert {"key": WARN_SPEAKER_WITHOUT_AUDIO, "params": {"name": "张三"}} in rendered.warnings


def test_audio_speaker_reference_index_tracks_image_slot_by_name_not_position():
    """参考音频顺序（台词 speaker 首现）与参考图顺序（mention 首现）独立派生：references 里
    场景先于张三出现，但张三先开口——``audio_speaker_reference_index`` 须按名字取图 1（0-based）
    的下标，不能按位置假设第 1 段音频配第 1 张图。"""
    rendered = render_unit_prompt(
        _TEXT,
        _project(),
        _refs(("scene", "酒馆"), ("character", "张三")),
        voice_consistency="native",
        max_reference_audio=3,
    )
    assert rendered.audio_speakers == ["张三", "李四"]
    # references[0]=酒馆, references[1]=张三 → 张三的 0-based 下标是 1；李四未随请求发图
    assert rendered.audio_speaker_reference_index == [1, None]


def test_audio_requires_reference_image_downgrades_offscreen_speaker_with_warning():
    """backend 要求音频逐段挂图（如 wan2.7-r2v）时，纯画外 speaker（无参考图）不绑定音频，
    编号与 warning 都在渲染期同步产生，避免 @音频N 承诺一段实际不会发出的绑定。"""
    rendered = render_unit_prompt(
        _TEXT,
        _project(),
        _refs(("scene", "酒馆"), ("character", "张三")),
        voice_consistency="native",
        max_reference_audio=3,
        audio_requires_reference_image=True,
    )
    # 李四没有参考图（纯画外），即使有可用音频也不绑定
    assert rendered.audio_speakers == ["张三"]
    assert rendered.audio_speaker_reference_index == [1]
    assert "<李四>的台词音色参考" not in rendered.prompt
    assert {"key": WARN_SPEAKER_AUDIO_NEEDS_IMAGE, "params": {"name": "李四"}} in rendered.warnings


def test_audio_speaker_image_slot_ignores_same_named_scene_or_prop():
    """场景与角色同名时，音频不能对齐到同名场景的图——speaker 只能是角色，``references``
    里 type=scene 的「张三」不等于会说话的角色「张三」。"""
    project = _project(scenes={"张三": {}})
    text = "镜头1：@[张三] 的餐厅一角。\n@[张三]：{欢迎光临。}"
    rendered = render_unit_prompt(
        text,
        project,
        _refs(("scene", "张三")),
        voice_consistency="native",
        max_reference_audio=3,
        audio_requires_reference_image=True,
    )
    assert rendered.audio_speakers == []
    assert {"key": WARN_SPEAKER_AUDIO_NEEDS_IMAGE, "params": {"name": "张三"}} in rendered.warnings


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
