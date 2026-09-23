"""导入分流：带 kind 的是端点定义，其余按 ComfyUI 的两种导出格式区分。"""

from __future__ import annotations

from typing import Any

import pytest

from lib.custom_provider.comfyui.import_shapes import ImportShape, route_import_payload, ui_workflow_refusal
from lib.custom_provider.endpoint_definition import validate_definition
from tests.factories import comfyui_api_workflow, comfyui_endpoint_definition, custom_endpoint_definition

#: ComfyUI「Export」导出的画布存档：节点在 ``nodes`` 数组里，连线单列在 ``links``，没有 ``class_type``。
UI_WORKFLOW: dict[str, Any] = {
    "last_node_id": 9,
    "last_link_id": 12,
    "nodes": [{"id": 6, "type": "CLIPTextEncode", "widgets_values": ["一只猫"]}],
    "links": [[1, 4, 1, 6, 0, "CLIP"]],
    "version": 0.4,
}


@pytest.mark.parametrize(
    "document",
    [
        custom_endpoint_definition(),
        comfyui_endpoint_definition(),
        {"kind": "谁也不认得的 kind"},
    ],
)
def test_anything_carrying_a_kind_is_an_endpoint_definition(document: object):
    shape, routed = route_import_payload(document, media_type="video")

    assert shape is ImportShape.ENDPOINT_DEFINITION
    assert routed is document


@pytest.mark.parametrize("document", [[], "裸串", 7, None])
def test_a_non_object_payload_stays_on_the_definition_path(document: object):
    """根上的类型错误由定义的容器层报出，不该被改口说成「这不是 workflow」。"""
    shape, routed = route_import_payload(document, media_type="video")

    assert shape is ImportShape.ENDPOINT_DEFINITION
    assert routed is document


def test_a_definition_that_forgot_its_kind_stays_on_the_definition_path():
    """一个节点也没有的对象不是 workflow：漏写 kind 的定义该听见「缺 kind」，而不是它的每一节
    都被当成节点、报回一串定位到 workflow.<节名> 的次生错误。"""
    definition = custom_endpoint_definition()
    del definition["kind"]

    shape, routed = route_import_payload(definition, media_type="video")

    assert shape is ImportShape.ENDPOINT_DEFINITION
    assert [(issue.path, issue.code.value) for issue in validate_definition(routed).errors] == [("$", "missing_field")]


def test_a_workflow_with_one_malformed_node_is_still_a_workflow():
    """节点自身写坏了由 workflow 的 schema 逐个指出来，整份载荷不改走另一条路。"""
    workflow = {**comfyui_api_workflow(), "10": {"inputs": {"text": "缺 class_type"}}}

    shape, _ = route_import_payload(workflow, media_type="video")

    assert shape is ImportShape.COMFYUI_API_WORKFLOW


def test_a_ui_format_workflow_is_singled_out():
    shape, routed = route_import_payload(UI_WORKFLOW, media_type="video")

    assert shape is ImportShape.COMFYUI_UI_WORKFLOW
    assert routed is UI_WORKFLOW


def test_a_node_named_nodes_does_not_look_like_the_ui_format():
    """API 格式的键是节点 id、值是节点对象；UI 格式的 ``nodes`` 是数组，两者撞不上。"""
    workflow = {**comfyui_api_workflow(), "nodes": {"class_type": "SaveVideo"}, "links": {}}

    shape, _ = route_import_payload(workflow, media_type="video")

    assert shape is ImportShape.COMFYUI_API_WORKFLOW


@pytest.mark.parametrize("media_type", ["image", "video"])
def test_an_api_workflow_is_wrapped_into_a_definition(media_type: str):
    workflow = comfyui_api_workflow()

    shape, routed = route_import_payload(workflow, media_type=media_type)

    assert shape is ImportShape.COMFYUI_API_WORKFLOW
    assert routed["kind"] == "comfyui"
    assert routed["media_type"] == media_type
    assert routed["workflow"] == workflow


def test_the_wrapped_definition_carries_no_bindings_yet():
    """空对象是「每一项都还没推断过」，逐键的空列表则会被读成「显式不支持」。"""
    _, routed = route_import_payload(comfyui_api_workflow(), media_type="video")

    assert routed["bindings"] == {}


def test_the_wrapped_definition_only_fails_on_the_two_required_bindings():
    """包装结果除了绑定为空之外处处成立：它的唯一问题就是还没绑。"""
    _, routed = route_import_payload(comfyui_api_workflow(), media_type="video")

    codes = [(issue.path, issue.code.value) for issue in validate_definition(routed).errors]

    assert codes == [
        ("bindings.prompt", "comfyui_binding_required"),
        ("bindings.output", "comfyui_binding_required"),
    ]


def test_the_ui_format_refusal_names_its_own_code():
    diagnostics = ui_workflow_refusal()

    assert [(issue.path, issue.code.value) for issue in diagnostics.errors] == [("$", "comfyui_ui_format_workflow")]
