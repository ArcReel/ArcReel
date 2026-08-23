"""Tests for render_unit_prompt."""

from __future__ import annotations

import pytest

from lib.reference_video.voice_settings import VoiceRenderSettings
from lib.script_models import ReferenceResource
from server.services.reference_video_tasks import (
    _render_unit_prompt,
)


def test_render_unit_prompt_rejects_empty_text():
    """执行层保留一道防御性空检查：提示词源是可变 script、执行期重读，结构校验上移到
    入队守卫点后仍需挡住「入队后被改空 / 在途遗留任务」漏过的空提示词，避免尾词追加后
    被当成有效 prompt 提交给付费 backend。"""
    with pytest.raises(ValueError, match="empty"):
        _render_unit_prompt(
            {"text": "   \n  "},
            {},
            VoiceRenderSettings(model_id="m", audio_ready=set()),
        )


def test_render_unit_prompt_binds_subjects_in_first_mention_order():
    unit = {"text": "镜头1：@张三 推门\n镜头2：对面的 @张三 抬眼，背景是 @酒馆"}
    project = {"characters": {"张三": {}}, "scenes": {"酒馆": {}}}
    rendered = _render_unit_prompt(
        unit,
        project,
        VoiceRenderSettings(model_id="m", audio_ready=set()),
    )
    assert "<张三>@图片1、<酒馆>@图片2。" in rendered.prompt
    assert "@张三" not in rendered.prompt
    assert "[图1]" not in rendered.prompt


def test_render_unit_prompt_binds_all_product_images_and_adds_fidelity_guard():
    rendered = _render_unit_prompt(
        {"text": "镜头1：@[商品甲] 出现在画面中央"},
        {"products": {"商品甲": {}}},
        VoiceRenderSettings(model_id="m", audio_ready=set()),
        request_references=[
            ReferenceResource(type="product", name="商品甲"),
            ReferenceResource(type="product", name="商品甲"),
        ],
    )

    assert "<商品甲>@图片1、<商品甲>@图片2。" in rendered.prompt
    assert "商品高保真还原（最高优先级" in rendered.prompt
