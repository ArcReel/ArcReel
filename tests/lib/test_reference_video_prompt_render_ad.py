"""ad 参考直出路径的三段论渲染（``render_ad_backend_prompt``）：与剧集路径共用第一段声音声明
与第三段约束包，差异只在输入形态——结构化镜头字段 + 参考条目 label，不经 ``parse_prompt``。
"""

from __future__ import annotations

import pytest

from lib.reference_video.prompt_render import render_ad_backend_prompt
from lib.reference_video.script_preview import (
    WARN_REFERENCE_AUDIO_OVERFLOW,
    WARN_SPEAKER_AUDIO_NEEDS_IMAGE,
    WARN_SPEAKER_WITHOUT_AUDIO,
    WARN_UNREGISTERED_SPEAKER,
)

pytestmark = pytest.mark.unit


def _project(**overrides):
    project = {
        "characters": {
            "小美": {"voice_style": "清亮少女音", "reference_audio": "characters/refs_audio/小美.wav"},
            "小明": {"voice_style": "沉稳男声"},
        },
        "scenes": {"客厅": {}},
        "props": {},
    }
    project.update(overrides)
    return project


def _shot(shot_id: str, duration: int = 3, dialogue: list[dict] | None = None, **overrides) -> dict:
    base = {
        "shot_id": shot_id,
        "duration_seconds": duration,
        "voiceover_text": "口播文案",
        "image_prompt": {"scene": f"{shot_id} 画面"},
        "video_prompt": {
            "action": f"{shot_id} 动作",
            "camera_motion": "Static",
            "ambiance_audio": "环境音",
            "dialogue": dialogue or [],
        },
    }
    base.update(overrides)
    return base


def _entry(name: str, label: str, kind: str = "asset", asset_type: str = "character") -> dict:
    entry = {"image": f"assets/{name}.png", "label": label, "name": name, "kind": kind}
    if kind == "asset":
        entry["asset_type"] = asset_type
    return entry


def test_subject_binding_uses_entry_labels_positionally():
    shots = [_shot("E1S1")]
    entries = [
        _entry("按摩仪", "产品「按摩仪」标准多角度参考图", kind="sheet"),
        _entry("小美", "角色「小美」设计图"),
    ]

    rendered = render_ad_backend_prompt(shots, entries, _project())

    assert "<产品「按摩仪」标准多角度参考图>@图片1" in rendered.prompt
    assert "<角色「小美」设计图>@图片2" in rendered.prompt


def test_dialogue_renders_as_subject_speaks_and_binds_audio_when_native():
    shots = [_shot("E1S1", dialogue=[{"speaker": "小美", "line": "太好用了"}])]
    entries = [_entry("小美", "角色「小美」设计图")]

    rendered = render_ad_backend_prompt(
        shots,
        entries,
        _project(),
        voice_consistency="native",
        max_reference_audio=2,
        audio_ready={"小美"},
    )

    assert "<小美>说 {太好用了}" in rendered.prompt
    assert "<小美>的台词音色参考 @音频1" in rendered.prompt
    assert rendered.audio_speakers == ["小美"]
    assert rendered.audio_speaker_reference_index == [0]


def test_voiceover_text_excluded_ambiance_kept_as_prose():
    shots = [_shot("E1S1")]

    rendered = render_ad_backend_prompt(shots, [], _project())

    assert "口播文案" not in rendered.prompt
    assert "环境音：环境音" in rendered.prompt


def test_legend_and_negative_tail_are_gone():
    shots = [_shot("E1S1", dialogue=[{"speaker": "小美", "line": "买它"}])]
    entries = [_entry("按摩仪", "产品「按摩仪」标准多角度参考图", kind="sheet")]

    rendered = render_ad_backend_prompt(shots, entries, _project())

    assert "[图" not in rendered.prompt
    assert "参考图对照" not in rendered.prompt
    assert "禁止出现：BGM、文字字幕、水印。" not in rendered.prompt


def test_third_segment_anchors_style_and_constraint_pack():
    shots = [_shot("E1S1")]

    rendered = render_ad_backend_prompt(shots, [], _project(), style="明亮写实")

    assert "整体视觉风格：明亮写实。" in rendered.prompt
    assert "保持无字幕" in rendered.prompt
    assert "不要生成水印" in rendered.prompt
    assert "禁止出现背景音乐。" in rendered.prompt


def test_twin_guard_only_when_two_or_more_character_images():
    shots = [_shot("E1S1")]
    single = render_ad_backend_prompt(shots, [_entry("小美", "角色「小美」设计图")], _project())
    assert "双胞胎" not in single.prompt

    both = render_ad_backend_prompt(
        shots,
        [_entry("小美", "角色「小美」设计图"), _entry("小明", "角色「小明」设计图")],
        _project(),
    )
    assert "双胞胎" in both.prompt


def test_speaker_without_reference_audio_downgrades_and_warns():
    shots = [_shot("E1S1", dialogue=[{"speaker": "小明", "line": "确实好用"}])]
    entries = [_entry("小明", "角色「小明」设计图")]

    rendered = render_ad_backend_prompt(
        shots, entries, _project(), voice_consistency="native", max_reference_audio=2, audio_ready=set()
    )

    assert rendered.audio_speakers == []
    assert any(w["key"] == WARN_SPEAKER_WITHOUT_AUDIO for w in rendered.warnings)
    # 无音频绑定不影响台词行本身的渲染
    assert "<小明>说 {确实好用}" in rendered.prompt


def test_unregistered_speaker_still_renders_with_warning():
    shots = [_shot("E1S1", dialogue=[{"speaker": "路人甲", "line": "哇好厉害"}])]

    rendered = render_ad_backend_prompt(shots, [], _project())

    assert "<路人甲>说 {哇好厉害}" in rendered.prompt
    assert any(w["key"] == WARN_UNREGISTERED_SPEAKER and w["params"]["name"] == "路人甲" for w in rendered.warnings)


def test_reference_audio_overflow_truncates_and_warns():
    shots = [
        _shot("E1S1", dialogue=[{"speaker": "小美", "line": "第一句"}]),
        _shot("E1S2", dialogue=[{"speaker": "小明", "line": "第二句"}]),
    ]
    entries = [_entry("小美", "角色「小美」设计图"), _entry("小明", "角色「小明」设计图")]

    rendered = render_ad_backend_prompt(
        shots,
        entries,
        _project(),
        voice_consistency="native",
        max_reference_audio=1,
        audio_ready={"小美", "小明"},
    )

    assert rendered.audio_speakers == ["小美"]
    assert any(w["key"] == WARN_REFERENCE_AUDIO_OVERFLOW for w in rendered.warnings)


def test_audio_requires_reference_image_downgrades_offscreen_speaker():
    # "小明" 有台词与可用音频，但本次没有随请求发出的参考图（entries 不含它）——
    # backend 要求音频逐段挂图时，纯画外角色即便有音频也不绑定。
    shots = [_shot("E1S1", dialogue=[{"speaker": "小明", "line": "旁白式吆喝"}])]

    rendered = render_ad_backend_prompt(
        shots,
        [],
        _project(),
        voice_consistency="native",
        max_reference_audio=2,
        audio_ready={"小明"},
        audio_requires_reference_image=True,
    )

    assert rendered.audio_speakers == []
    assert any(w["key"] == WARN_SPEAKER_AUDIO_NEEDS_IMAGE for w in rendered.warnings)


def test_audio_speaker_image_slot_ignores_same_named_scene_or_prop():
    # "客厅" 同名注册为场景（project fixture），资产条目里若混入同名场景条目，不应被误判为
    # 角色有参考图（与剧集路径 character_image_no 的同款过滤同一理由）。
    shots = [_shot("E1S1", dialogue=[{"speaker": "小美", "line": "在客厅里"}])]
    entries = [
        _entry("客厅", "场景「客厅」设计图", asset_type="scene"),
        _entry("小美", "角色「小美」设计图"),
    ]

    rendered = render_ad_backend_prompt(
        shots,
        entries,
        _project(),
        voice_consistency="native",
        max_reference_audio=2,
        audio_ready={"小美"},
    )

    assert rendered.audio_speaker_reference_index == [1]


def test_audio_speaker_image_slot_ignores_scene_sharing_a_character_name():
    # 三类资产允许重名：同名的场景设计图不得占用角色的图号，否则 reference_audio_targets
    # 会把音频挂到场景图上。
    project = _project(scenes={"小美": {}})
    shots = [_shot("E1S1", dialogue=[{"speaker": "小美", "line": "太好用了"}])]
    entries = [
        _entry("小美", "场景「小美」设计图", asset_type="scene"),
        _entry("小美", "角色「小美」设计图"),
    ]

    rendered = render_ad_backend_prompt(
        shots,
        entries,
        project,
        voice_consistency="native",
        max_reference_audio=2,
        audio_ready={"小美"},
    )

    assert rendered.audio_speaker_reference_index == [1]


def test_twin_guard_ignores_non_character_reference_images():
    shots = [_shot("E1S1")]
    entries = [
        _entry("小美", "角色「小美」设计图"),
        _entry("客厅", "场景「客厅」设计图", asset_type="scene"),
    ]

    rendered = render_ad_backend_prompt(shots, entries, _project())

    assert "双胞胎" not in rendered.prompt


def test_all_blank_shots_raise_value_error():
    shots = [_shot("E1S1", image_prompt={"scene": ""}, video_prompt={"action": ""})]

    with pytest.raises(ValueError, match="no visual content"):
        render_ad_backend_prompt(shots, [], _project())
