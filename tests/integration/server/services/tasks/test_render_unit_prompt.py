"""Tests for render_unit_prompt."""

from __future__ import annotations

import pytest

from lib.script.reference_video.voice_settings import VoiceRenderSettings
from lib.script.script_models import ReferenceResource
from server.services.tasks.reference_video_tasks import (
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
    # 首现顺序即 <Subject N> 与 <Picture N> 编号：张三先出场 → Subject 1，酒馆后出场 → Subject 2
    assert "<Subject 1> is the character 张三, shown in <Picture 1>." in rendered.prompt
    assert "<Subject 2> is the scene 酒馆, shown in <Picture 2>." in rendered.prompt
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

    # 同一主体的多张商品参考图全部列进主体行（同一 <Subject> 下并列 <Picture 1>、<Picture 2>）
    assert "<Subject 1> is the product 商品甲, shown in <Picture 1>、<Picture 2>." in rendered.prompt
    assert "商品高保真还原（最高优先级" in rendered.prompt
