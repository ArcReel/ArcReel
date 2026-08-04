"""渲染层声音输入档值对象：能力 dict 取值与无声判据。"""

from __future__ import annotations

import pytest

from lib.reference_video.voice_settings import VoiceRenderSettings

pytestmark = pytest.mark.unit


def test_from_caps_reads_every_field_from_capability_dict():
    """能力 dict 的 key 名与字段名的对应只在 from_caps 一处：漏取一位会让某个调用点
    （解析预览路由 / SDK 拆分工具）给出与执行层不同的声音结论。"""
    settings = VoiceRenderSettings.from_caps(
        {
            "voice_consistency": "native",
            "requested_generate_audio": False,
            "max_reference_audio_count": 3,
            "model": "doubao-seedance-2-0",
            "reference_audio_per_image": True,
        },
        audio_ready={"张三"},
    )
    assert settings == VoiceRenderSettings(
        voice_consistency="native",
        requested_generate_audio=False,
        max_reference_audio=3,
        model_id="doubao-seedance-2-0",
        audio_ready={"张三"},
        requires_reference_image=True,
    )


def test_from_caps_degrades_to_soft_on_unresolvable_capabilities():
    """能力解析失败时调用方传空 dict：落到 soft / 无参考音频，只少发几条提示，不阻断渲染。"""
    settings = VoiceRenderSettings.from_caps({})
    assert settings == VoiceRenderSettings()
    assert settings.voice_consistency == "soft"
    assert settings.requested_generate_audio is True
    assert settings.audio_ready is None


@pytest.mark.parametrize(
    ("voice_consistency", "requested_generate_audio", "expected"),
    [
        ("none", True, True),  # 模型不产音
        ("native", False, True),  # 本集关闭音频
        ("soft", False, True),  # 软约束档同样受本集开关支配
        ("none", False, True),  # 两条路径叠加
        ("native", True, False),
        ("soft", True, False),
    ],
)
def test_is_silent_covers_both_silent_paths(voice_consistency: str, requested_generate_audio: bool, expected: bool):
    settings = VoiceRenderSettings(
        voice_consistency=voice_consistency,
        requested_generate_audio=requested_generate_audio,
    )
    assert settings.is_silent is expected
