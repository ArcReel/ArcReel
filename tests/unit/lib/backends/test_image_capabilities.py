"""图片生效能力：模型条目与调用通道都满足的部分。"""

from __future__ import annotations

from pathlib import Path

import pytest

from lib.artifacts.visual_artifact_provenance import VisualReference
from lib.backends.generation_type_buckets import builtin_model_buckets, custom_model_buckets
from lib.backends.image_backends.agnes import AgnesImageBackend
from lib.backends.image_backends.base import BOTH_IMAGE_CAPABILITIES, ImageCapability
from lib.backends.image_backends.grok import GrokImageBackend
from lib.backends.image_capabilities import (
    backend_reference_image_limit,
    custom_image_model_capabilities,
    effective_image_capabilities,
    entry_reference_limit,
    image_reference_limit,
    intersect_image_capabilities,
)
from lib.backends.input_limits import InputLimit
from lib.catalog import query
from lib.prompts.reference_image_numbering import clamp_reference_images
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


# ---------------------------------------------------------------------------
# 参考图上限：条目与通道两侧求值，候选与编排层裁剪读同一个结果
# ---------------------------------------------------------------------------


def _sheets(count: int) -> list[VisualReference]:
    return [
        VisualReference(path=Path(f"角色{i}.png"), role="asset_sheet", logical_type="character", logical_id=f"角色{i}")
        for i in range(count)
    ]


def _clamped(limit: InputLimit[int], count: int) -> int:
    return clamp_reference_images(_sheets(count), limit.clamp_bound(), model="m").kept


def _grok(model: str = "grok-imagine-image") -> GrokImageBackend:
    return GrokImageBackend(api_key="k", model=model)


async def test_entry_below_the_channel_limit_wins_and_clamps(monkeypatch: pytest.MonkeyPatch):
    """Grok 通道一次收 5 张，条目记 3 张：生效 3 张，编排层裁到 3 张。"""
    install_catalog(
        monkeypatch, with_entry_facts(query.factory_snapshot(), "grok", "grok-imagine-image", max_reference_images=3)
    )
    limit = await backend_reference_image_limit(_grok(), provider_id="grok")
    assert limit == InputLimit.at_most(3)
    assert _clamped(limit, 6) == 3


async def test_unrecorded_entry_leaves_only_the_channel_limit(monkeypatch: pytest.MonkeyPatch):
    install_catalog(
        monkeypatch, with_entry_facts(query.factory_snapshot(), "grok", "grok-imagine-image", max_reference_images=None)
    )
    limit = await backend_reference_image_limit(_grok(), provider_id="grok")
    assert limit == InputLimit.at_most(5)
    assert _clamped(limit, 6) == 5


async def test_neither_side_declaring_a_limit_is_unrecorded_and_never_clamps(monkeypatch: pytest.MonkeyPatch):
    """Agnes 通道不限张数、条目也没收录：未收录，不裁剪，超出由上游拒绝。"""
    install_catalog(
        monkeypatch,
        with_entry_facts(query.factory_snapshot(), "agnes", "agnes-image-2.5-flash", max_reference_images=None),
    )
    limit = await backend_reference_image_limit(AgnesImageBackend(api_key="k"), provider_id="agnes")
    assert limit == InputLimit.unrecorded()
    assert _clamped(limit, 20) == 20


async def test_explicit_zero_accepts_no_reference_and_leaves_the_i2i_bucket(monkeypatch: pytest.MonkeyPatch):
    """条目显式记 0 张：不接受参考图，不进 i2i 桶；编排层不裁成 0 张，原样交给执行时的校验拒绝。"""
    data = with_entry_facts(query.factory_snapshot(), "grok", "grok-imagine-image", max_reference_images=0)
    install_catalog(monkeypatch, data)
    limit = await backend_reference_image_limit(_grok(), provider_id="grok")
    assert limit == InputLimit.at_most(0)
    assert not limit.accepts_input
    assert _clamped(limit, 4) == 4
    model_info = query.get_model("grok", "grok-imagine-image", media_type="image")
    assert model_info is not None
    assert builtin_model_buckets("grok", "grok-imagine-image", model_info) == {"t2i"}


def test_custom_override_closing_i2i_makes_the_reference_path_unsupported():
    """已有的图生图能力覆盖关掉这条路径：对应条目记 14 张也不接受参考图，候选只剩文生图。"""
    assert custom_model_buckets(endpoint="gemini-image", model_id="gemini-3-pro-image") == {"t2i", "i2i"}
    assert custom_model_buckets(
        endpoint="gemini-image", model_id="gemini-3-pro-image", capability_overrides={"image_to_image": False}
    ) == {"t2i"}
    model_caps = custom_image_model_capabilities(
        "gemini-3-pro-image", overrides={"image_to_image": False}, endpoint_kind="python"
    )
    limit = image_reference_limit(
        capabilities=intersect_image_capabilities(BOTH_IMAGE_CAPABILITIES, model_caps),
        entry_limit=entry_reference_limit("gemini-3-pro-image", builtin_provider=None),
        channel_limit=None,
    )
    assert limit == InputLimit.unsupported()
    assert _clamped(limit, 16) == 16


# ---------------------------------------------------------------------------
# 设置页的生效能力与候选读同一个求值结果
# ---------------------------------------------------------------------------


def test_settings_show_i2i_unsupported_when_the_entry_records_zero_references(monkeypatch: pytest.MonkeyPatch):
    """条目显式记 0 张：设置页的图生图为不支持、参考图上限 0，与候选（只进 t2i）一致。"""
    from lib.custom_provider.capability_view import builtin_capability_view, custom_capability_view
    from lib.custom_provider.endpoints import get_endpoint_spec

    data = with_entry_facts(query.factory_snapshot(), "grok", "grok-imagine-image", max_reference_images=0)
    data = with_entry_facts(data, "gemini-aistudio", "gemini-3-pro-image", max_reference_images=0)
    install_catalog(monkeypatch, data)
    zero_references = {"text_to_image": True, "image_to_image": False, "max_reference_images": 0}

    official = query.get_model("grok", "grok-imagine-image", media_type="image")
    assert official is not None
    view = builtin_capability_view("grok", "grok-imagine-image", base_model=official, official=True, overrides={})
    assert view.effective == zero_references
    assert builtin_model_buckets("grok", "grok-imagine-image", official) == {"t2i"}

    # 自定义供应商按模型名对应同一个条目；图生图仍可修改，原值是求值后的不支持
    custom = custom_capability_view(
        model_id="gemini-3-pro-image",
        endpoint="gemini-image",
        spec=get_endpoint_spec("gemini-image"),
        raw_overrides=None,
        applied_overrides=None,
        entry_binding=None,
    )
    assert (custom.base, "image_to_image" in custom.editable) == (zero_references, True)
    assert custom_model_buckets(endpoint="gemini-image", model_id="gemini-3-pro-image") == {"t2i"}


def test_settings_show_both_image_dimensions_of_a_comfyui_endpoint_from_its_bindings():
    """ComfyUI 图片端点：文生图、图生图与参考图上限都按节点绑定展示，只读，与候选一致。"""
    from lib.custom_provider.capability_view import custom_capability_view
    from lib.custom_provider.endpoints import comfyui_endpoint_spec
    from tests.factories import comfyui_endpoint_definition

    t2i = comfyui_endpoint_definition(media_type="image")
    i2i = comfyui_endpoint_definition(media_type="image")
    i2i["bindings"]["reference_images"] = [{"node": "11", "input": "image", "class_type": "LoadImage"}]

    def view(definition: dict):
        spec = comfyui_endpoint_spec("ce-7", definition)
        return custom_capability_view(
            model_id="doubao-seedream-4-5-251128",  # 对得上条目也不读条目
            endpoint="ce-7",
            spec=spec,
            raw_overrides=None,
            applied_overrides=None,
            entry_binding=None,
        )

    text_only, reference_only = view(t2i), view(i2i)
    assert text_only.displayed == ("text_to_image", "image_to_image", "max_reference_images")
    assert (text_only.editable, text_only.effective) == (
        (),
        {"text_to_image": True, "image_to_image": False, "max_reference_images": 0},
    )
    assert reference_only.effective == {"text_to_image": False, "image_to_image": True, "max_reference_images": 1}
