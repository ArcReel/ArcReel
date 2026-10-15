"""图片生效能力：模型条目与调用通道都满足的部分。"""

from __future__ import annotations

import pytest

from lib.backends.image_backends.base import BOTH_IMAGE_CAPABILITIES, ImageCapability
from lib.backends.image_capabilities import (
    custom_image_model_capabilities,
    effective_image_capabilities,
    intersect_image_capabilities,
)
from lib.catalog import query
from tests.catalog_support import install_catalog, with_entry_facts

T2I = ImageCapability.TEXT_TO_IMAGE
I2I = ImageCapability.IMAGE_TO_IMAGE


@pytest.mark.parametrize(
    ("model_id", "expected"),
    [("viduq2", {T2I, I2I}), ("viduq1", {I2I})],
)
def test_builtin_model_takes_entry_facts_within_channel(model_id, expected):
    caps = effective_image_capabilities(BOTH_IMAGE_CAPABILITIES, model_id=model_id, builtin_provider="vidu")
    assert caps == expected


def test_builtin_entry_facts_follow_catalog(monkeypatch: pytest.MonkeyPatch):
    install_catalog(monkeypatch, with_entry_facts(query.factory_snapshot(), "vidu", "viduq2", text_to_image=False))
    caps = effective_image_capabilities(BOTH_IMAGE_CAPABILITIES, model_id="viduq2", builtin_provider="vidu")
    assert caps == {I2I}


@pytest.mark.parametrize(
    ("channel", "model_id", "expected"),
    [
        (BOTH_IMAGE_CAPABILITIES, "qwen-image-edit-plus", {I2I}),
        ({T2I}, "qwen-image-edit-plus", set()),
        ({T2I}, "relay-image-x", {T2I}),
    ],
)
def test_custom_model_matches_entry_by_name(channel, model_id, expected):
    caps = effective_image_capabilities(channel, model_id=model_id, builtin_provider=None)
    assert caps == expected


@pytest.mark.parametrize(
    ("model_id", "overrides", "expected"),
    [
        # 条目说只接受图生图，能力覆盖把文生图整维替换为开启
        ("qwen-image-edit-plus", {"text_to_image": True}, {T2I, I2I}),
        # 覆盖关掉一维，另一维仍取条目
        ("qwen-image-edit-plus", {"image_to_image": False}, set()),
        # 对应不上条目：只覆盖的那一维受约束，另一维不加约束
        ("relay-image-x", {"image_to_image": False}, {T2I}),
    ],
)
def test_custom_override_replaces_entry_fact_per_dimension(model_id, overrides, expected):
    model = custom_image_model_capabilities(model_id, overrides=overrides, endpoint_kind="python")
    assert intersect_image_capabilities(BOTH_IMAGE_CAPABILITIES, model) == expected


def test_comfyui_endpoint_ignores_entry_and_overrides():
    """ComfyUI 端点的能力只从节点绑定推导：名字对上条目也不取交集，存量覆盖不生效。"""
    model = custom_image_model_capabilities(
        "qwen-image-edit-plus", overrides={"image_to_image": False}, endpoint_kind="comfyui"
    )
    assert model is None
    assert intersect_image_capabilities({T2I}, model) == {T2I}
