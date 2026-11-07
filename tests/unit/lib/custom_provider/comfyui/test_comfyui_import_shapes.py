"""Import routing: kind identifies endpoint definitions; otherwise distinguish the two ComfyUI export formats."""

from __future__ import annotations

from typing import Any

import pytest

from arcreel_market_core.endpoint_definition import validate_definition
from lib.custom_provider.comfyui.import_shapes import ImportShape, route_import_payload, ui_workflow_refusal
from tests.factories import comfyui_api_workflow, comfyui_endpoint_definition, custom_endpoint_definition

#: ComfyUI Export canvas archive: nodes in the ``nodes`` array, separate edges in ``links``, no ``class_type``.
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
    """The definition container reports root type errors; do not relabel them as not a workflow."""
    shape, routed = route_import_payload(document, media_type="video")

    assert shape is ImportShape.ENDPOINT_DEFINITION
    assert routed is document


def test_a_definition_that_forgot_its_kind_stays_on_the_definition_path():
    """An object without any nodes is not a workflow: a definition missing kind should report missing kind, not treat each section
    as a node and return cascading errors under workflow.<节名>."""
    definition = custom_endpoint_definition()
    del definition["kind"]

    shape, routed = route_import_payload(definition, media_type="video")

    assert shape is ImportShape.ENDPOINT_DEFINITION
    assert [(issue.path, issue.code.value) for issue in validate_definition(routed).errors] == [("$", "missing_field")]


def test_a_workflow_with_one_malformed_node_is_still_a_workflow():
    """Workflow schemas report malformed nodes individually without rerouting the whole payload."""
    workflow = {**comfyui_api_workflow(), "10": {"inputs": {"text": "缺 class_type"}}}

    shape, _ = route_import_payload(workflow, media_type="video")

    assert shape is ImportShape.COMFYUI_API_WORKFLOW


def test_a_ui_format_workflow_is_singled_out():
    shape, routed = route_import_payload(UI_WORKFLOW, media_type="video")

    assert shape is ImportShape.COMFYUI_UI_WORKFLOW
    assert routed is UI_WORKFLOW


def test_a_node_named_nodes_does_not_look_like_the_ui_format():
    """API format maps node IDs to node objects; UI format has a ``nodes`` array, so the shapes cannot collide."""
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
    """An empty object means no items have been inferred yet; per-key empty lists mean explicitly unsupported."""
    _, routed = route_import_payload(comfyui_api_workflow(), media_type="video")

    assert routed["bindings"] == {}


def test_the_wrapped_definition_only_fails_on_the_two_required_bindings():
    """The wrapped result is valid except for empty bindings; its sole problem is not yet being bound."""
    _, routed = route_import_payload(comfyui_api_workflow(), media_type="video")

    codes = [(issue.path, issue.code.value) for issue in validate_definition(routed).errors]

    assert codes == [
        ("bindings.prompt", "comfyui_binding_required"),
        ("bindings.output", "comfyui_binding_required"),
    ]


def test_the_ui_format_refusal_names_its_own_code():
    diagnostics = ui_workflow_refusal()

    assert [(issue.path, issue.code.value) for issue in diagnostics.errors] == [("$", "comfyui_ui_format_workflow")]
