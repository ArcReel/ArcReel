"""Tests for render_unit_prompt."""

from __future__ import annotations

import pytest

from lib.script.reference_video.voice_settings import VoiceRenderSettings
from lib.script.script_models import ReferenceResource
from server.services.tasks.reference_video_tasks import (
    _render_unit_prompt,
)


def test_render_unit_prompt_rejects_empty_text():
    """Keep a defensive execution blank check: prompts come from mutable scripts reread at execution. Even after structural validation moves to enqueue guards, block prompts cleared afterward or in legacy in-flight tasks, preventing appended trailers from making empty prompts appear billable-valid."""
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
    assert "High-fidelity product reproduction (highest priority" in rendered.prompt
