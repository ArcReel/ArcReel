"""ComfyUI JSON Schema contract: valid itself, synchronized with catalogs, and sharing $defs with declarative definitions."""

from __future__ import annotations

from typing import Any

import pytest
from definition_factories import comfyui_endpoint_definition
from jsonschema import Draft202012Validator

from arcreel_market_core.comfyui.bindings import IMAGE_BINDING_KEYS, VIDEO_BINDING_KEYS
from arcreel_market_core.comfyui.validator import CURRENT_SCHEMA_VERSION, load_schema
from arcreel_market_core.endpoint_definition import COMFYUI_KIND
from arcreel_market_core.endpoint_definition import load_schema as load_declarative_schema

#: Components shared identically by both schemas: ``meta`` defines identity; ``auth`` defines credential syntax.
SHARED_DEFS = ("semver", "meta", "auth", "headerMap", "queryMap", "template")


def test_schema_is_a_valid_2020_12_schema():
    Draft202012Validator.check_schema(load_schema())


def test_schema_id_carries_the_current_version():
    assert load_schema()["$id"].endswith(f"/{CURRENT_SCHEMA_VERSION}.json")


def test_kind_const_matches_the_dispatch_name():
    """Schema kind and dispatch-map keys must be identical strings, or validated definitions cannot dispatch to implementations."""
    assert load_schema()["properties"]["kind"]["const"] == COMFYUI_KIND


@pytest.mark.parametrize("name", SHARED_DEFS)
def test_shared_defs_are_identical_to_the_declarative_schema(name: str):
    """Shared components are identical word for word; drift would validate the same meta differently across kinds."""
    assert load_schema()["$defs"][name] == load_declarative_schema()["$defs"][name]


def test_binding_keys_are_the_closed_set_in_the_schema():
    """Schema bindings keys equal the video semantic-key catalog; the image whitelist is its subset."""
    assert set(load_schema()["$defs"]["bindings"]["properties"]) == set(VIDEO_BINDING_KEYS)
    assert set(IMAGE_BINDING_KEYS) < set(VIDEO_BINDING_KEYS)


def test_the_factory_definition_passes_the_schema():
    assert list(_schema_errors(comfyui_endpoint_definition())) == []


@pytest.mark.parametrize(
    ("mutate", "reason"),
    [
        (lambda d: d.pop("workflow"), "workflow 是必填"),
        (lambda d: d.pop("bindings"), "bindings 是必填"),
        (lambda d: d.__setitem__("media_type", "audio"), "媒体类型只有图像与视频"),
        (lambda d: d.__setitem__("capabilities", {"first_frame": True}), "能力只从绑定推导，不进定义"),
        (lambda d: d.__setitem__("workflow", {"3": {"inputs": {}}}), "节点必须有 class_type"),
        (lambda d: d.__setitem__("workflow", {}), "空 workflow 提交不出任何东西"),
        (lambda d: d["bindings"].__setitem__("unknown_slot", []), "语义键集在 schema 里封闭"),
        (lambda d: d["bindings"]["output"].append({"node": "9", "class_type": "SaveVideo"}), "产物唯一"),
        (lambda d: d["bindings"]["output"][0].__setitem__("input", "images"), "产物是节点级的，没有 input"),
        (lambda d: d["bindings"]["prompt"][0].__setitem__("step", 8), "步长只用于宽高帧数"),
        (lambda d: d["bindings"]["prompt"][0].__setitem__("policy", "keep"), "种子策略只用于 seed"),
        (lambda d: d["bindings"]["width"][0].__setitem__("step", 0), "步长至少是 1"),
        (lambda d: d["bindings"]["seed"][0].__setitem__("policy", "always"), "种子策略只有两种"),
        (lambda d: d["bindings"]["fps"][0].__setitem__("direction", "write"), "帧率绑定只读"),
        (lambda d: d["bindings"]["prompt"][0].__setitem__("direction", "read"), "其余绑定只写"),
        (lambda d: d["bindings"]["prompt"][0].pop("class_type"), "目标要记下类型供重匹配"),
        (
            lambda d: d["bindings"]["reference_images"].append({"node": "6", "input": "text"}),
            "参考图目标缺类型",
        ),
    ],
)
def test_schema_rejects_a_malformed_definition(mutate: Any, reason: str):
    definition = comfyui_endpoint_definition()
    definition["bindings"].setdefault("reference_images", [])
    mutate(definition)

    assert list(_schema_errors(definition)), reason


def test_a_frames_binding_may_carry_a_hand_typed_fps():
    definition = comfyui_endpoint_definition()
    definition["bindings"]["frames"] = [
        {"node": "5", "input": "batch_size", "class_type": "EmptyLatentImage", "step": 4, "fps": 16}
    ]

    assert list(_schema_errors(definition)) == []


def test_a_reference_image_binding_may_record_its_consumer():
    definition = comfyui_endpoint_definition()
    definition["bindings"]["reference_images"] = [
        {
            "node": "4",
            "input": "ckpt_name",
            "class_type": "CheckpointLoaderSimple",
            "consumer": {"node": "3", "input": "model", "class_type": "KSampler"},
        }
    ]

    assert list(_schema_errors(definition)) == []


def _schema_errors(definition: dict[str, Any]) -> Any:
    return Draft202012Validator(load_schema()).iter_errors(definition)
