"""提示词方言：模型识别与三段论渲染的逐方言断言。

方言只改「参考素材指代」和「台词句式」两类随模型变化的写法；除此之外三段论的其余部分
（主体记号、风控尾注、商品高保真声明）一律不变，故这里只断言会变的那几处。
"""

from __future__ import annotations

import pytest

from lib.script.reference_video.prompt_dialect import (
    GENERIC,
    MINIMAX_H3,
    SEEDANCE,
    WAN30,
    dialect_for,
)
from lib.script.reference_video.prompt_render import render_unit_prompt
from lib.script.reference_video.voice_settings import VoiceRenderSettings
from lib.script.script_models import ReferenceResource

_PROJECT = {"characters": {"张三": {}}, "scenes": {"酒馆": {}}}
_REFS = [
    ReferenceResource(type="character", name="张三"),
    ReferenceResource(type="scene", name="酒馆"),
]
_TEXT = "@[张三] 推开 @[酒馆] 的门，侧身跨过门槛。@[张三]{我来了。}"


def _render(dialect):
    return render_unit_prompt(
        _TEXT,
        _PROJECT,
        _REFS,
        VoiceRenderSettings(model_id="m", audio_ready=set()),
        dialect=dialect,
    ).prompt


@pytest.mark.parametrize(
    ("model", "expected"),
    [
        ("doubao-seedance-2-5-260628", SEEDANCE),
        ("doubao-seedance-2.5", SEEDANCE),
        ("wan3.0-video", WAN30),
        ("alibaba/wan3.0-video", WAN30),
        ("MiniMax-H3", MINIMAX_H3),
        ("minimax-h3", MINIMAX_H3),
        # legacy Hailuo 没有多参考能力、也不吃 @ImageN 记号，必须留在 generic
        ("MiniMax-Hailuo-2.3", GENERIC),
        ("MiniMax-Hailuo-02", GENERIC),
        ("S2V-01", GENERIC),
        ("agnes-video-2.5-flash", GENERIC),
        ("kling-video-o1", GENERIC),
        ("", GENERIC),
        (None, GENERIC),
    ],
)
def test_dialect_for_recognizes_model_family(model, expected):
    """认得出的按名字族取方言；认不得（含空值）一律回落 generic——猜错比不猜更贵。"""
    assert dialect_for(model) is expected


def test_generic_dialect_keeps_legacy_syntax():
    """方言化之前的写法逐字不变：既有项目的成片与预览不因本模块存在而改变。"""
    prompt = _render(GENERIC)
    assert "<张三>@图片1、<酒馆>@图片2。" in prompt
    assert "<张三>说 {我来了。}" in prompt
    assert "Avoid: BGM" in prompt


def test_seedance_dialect_uses_image_refs_and_quoted_speech():
    prompt = _render(SEEDANCE)
    assert "<张三>Image 1、<酒馆>Image 2。" in prompt
    assert '<张三>说："我来了。"' in prompt
    assert "@图片" not in prompt
    assert "Avoid: BGM" in prompt


def test_wan_dialect_uses_chinese_refs_and_negative_prefix():
    prompt = _render(WAN30)
    assert "<张三>图1、<酒馆>图2。" in prompt
    assert '<张三>说："我来了。"' in prompt
    assert "Negative: BGM" in prompt
    assert "Avoid:" not in prompt


def test_minimax_h3_dialect_uses_at_image_refs_and_quoted_speech():
    """H3 用 ``@ImageN``（官方 API 示例写 reference images 1，前端统一 @Image1）；台词走引号。"""
    prompt = _render(MINIMAX_H3)
    assert "<张三>@Image1、<酒馆>@Image2。" in prompt
    assert '<张三>说："我来了。"' in prompt
    assert "@图片" not in prompt
    # H3 没有 Negative 字段，负向约束仍作为正文尾注下发
    assert "Avoid: BGM" in prompt


def test_dialect_audio_reference_format():
    """音频号随方言走；图号与音频号是两条独立编号，格式必须各自成形。"""
    assert GENERIC.audio(2) == "@音频2"
    assert SEEDANCE.audio(2) == "Audio 2"
    assert WAN30.audio(2) == "音频2"
    assert MINIMAX_H3.audio(2) == "@Audio2"
