"""角色声音绑定方式的取值与归一。"""

import pytest

from lib.character_voice import DEFAULT_CHARACTER_VOICE_BINDING, character_voice_binding


def test_no_project_context_returns_none():
    """无项目上下文（模型目录查询等）返回 None，不渲染成用户显式选过某种绑定方式。"""
    assert character_voice_binding(None) is None


def test_declared_value_is_returned():
    assert character_voice_binding({"character_voice_binding": "reference_audio"}) == "reference_audio"
    assert character_voice_binding({"character_voice_binding": "prompt"}) == "prompt"


def test_missing_field_falls_back_to_default():
    assert character_voice_binding({}) == DEFAULT_CHARACTER_VOICE_BINDING


@pytest.mark.parametrize("dirty", [None, "", "REFERENCE_AUDIO", "audio", 1, True, ["prompt"], {"v": "prompt"}])
def test_unreadable_value_falls_back_to_default(dirty):
    """手编脏值按默认档解读：这一侧的降级只会少挂参考音频，比按参考音频档解读一个读不懂的值安全。"""
    assert character_voice_binding({"character_voice_binding": dirty}) == DEFAULT_CHARACTER_VOICE_BINDING
