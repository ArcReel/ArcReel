"""ComfyUI workflow import and request binding unit tests."""

from __future__ import annotations

import copy

import pytest

from lib.comfyui_workflow import (
    bind_scalar_inputs,
    bind_uploaded_image,
    bind_uploaded_reference_images,
    detect_comfyui_endpoint_config,
    extract_video_output,
    workflow_profile_id,
)

pytestmark = pytest.mark.unit


def _workflow(prefix: str = "MiniMax_H3") -> dict:
    return {
        "10": {
            "class_type": "MiniMaxH3ImageToVideo",
            "inputs": {
                "prompt": "old prompt",
                "first_frame": ["20", 0],
                "aspect_ratio": "9:16",
            },
        },
        "20": {"class_type": "LoadImage", "inputs": {"image": "private/source.png"}},
        "30": {
            "class_type": "PrimitiveFloat",
            "inputs": {"value": 5.0},
            "_meta": {"title": "Float (duration)"},
        },
        "40": {"class_type": "RandomNoise", "inputs": {"noise_seed": 123}},
        "90": {"class_type": "SaveVideo", "inputs": {"filename_prefix": prefix, "video": ["10", 0]}},
    }


def _object_info() -> dict:
    return {
        "MiniMaxH3ImageToVideo": {
            "input": {
                "required": {"prompt": ["STRING"], "aspect_ratio": [["9:16", "16:9"]]},
                "optional": {"first_frame": ["IMAGE"], "last_frame": ["IMAGE"]},
            }
        }
    }


def test_detects_and_sanitizes_video_workflow() -> None:
    config = detect_comfyui_endpoint_config(_workflow(), object_info=_object_info())

    assert config["bindings"]["start_image"]["mode"] == "loader"
    assert config["bindings"]["end_image"]["mode"] == "inject_loader"
    assert config["bindings"]["duration"]["unit"] == "seconds"
    assert config["metadata"]["duration_default_seconds"] == 5
    assert config["workflow"]["10"]["inputs"]["prompt"] == ""
    assert config["workflow"]["20"]["inputs"]["image"] == ""
    assert config["workflow"]["90"]["inputs"]["filename_prefix"] == "arcreel/output"


def test_profile_identity_ignores_previous_output_prefix() -> None:
    first = detect_comfyui_endpoint_config(_workflow("old/a"), object_info=_object_info())
    second = detect_comfyui_endpoint_config(_workflow("old/b"), object_info=_object_info())

    assert workflow_profile_id(first) == workflow_profile_id(second)


def test_binds_arc_reel_values_and_injects_tail_frame() -> None:
    config = detect_comfyui_endpoint_config(_workflow(), object_info=_object_info())
    workflow = bind_scalar_inputs(
        config,
        prompt="camera pushes in",
        duration_seconds=8,
        aspect_ratio="16:9",
        seed=456,
        output_prefix="arcreel/proj/task",
    )
    bind_uploaded_image(
        workflow,
        config["bindings"]["end_image"],
        "arcreel/proj/end.png",
        loader_id="arcreel_end_image",
    )

    assert workflow["10"]["inputs"]["prompt"] == "camera pushes in"
    assert workflow["10"]["inputs"]["aspect_ratio"] == "16:9"
    assert workflow["30"]["inputs"]["value"] == 8.0
    assert workflow["40"]["inputs"]["noise_seed"] == 456
    assert workflow["90"]["inputs"]["filename_prefix"] == "arcreel/proj/task"
    assert workflow["10"]["inputs"]["last_frame"] == ["arcreel_end_image", 0]


def test_extracts_video_descriptor_from_save_video_output() -> None:
    record = {"outputs": {"90": {"gifs": [{"filename": "task.mp4", "subfolder": "arcreel/proj", "type": "output"}]}}}

    assert extract_video_output(record, "90") == {
        "filename": "task.mp4",
        "subfolder": "arcreel/proj",
        "type": "output",
    }


def test_binding_does_not_mutate_persisted_template() -> None:
    config = detect_comfyui_endpoint_config(_workflow(), object_info=_object_info())
    original = copy.deepcopy(config)

    bind_scalar_inputs(
        config,
        prompt="new",
        duration_seconds=5,
        aspect_ratio="9:16",
        seed=1,
        output_prefix="out",
    )

    assert config == original


def test_detects_and_binds_minimax_h3_autogrow_reference_images() -> None:
    workflow = {
        "136": {
            "class_type": "MiniMaxH3ReferenceToVideo",
            "inputs": {
                "prompt": "<张三>@图片1、<酒馆>@图片2。",
                "ref_images.ref_image_0": ["137", 0],
                "ref_images.ref_image_1": ["139", 0],
            },
        },
        "137": {"class_type": "LoadImage", "inputs": {"image": "private/person.png"}},
        "139": {"class_type": "LoadImage", "inputs": {"image": "private/scene.png"}},
        "90": {"class_type": "SaveVideo", "inputs": {"filename_prefix": "MiniMax_H3", "video": ["136", 0]}},
    }
    object_info = {
        "MiniMaxH3ReferenceToVideo": {
            "display_name": "MiniMax H3 Reference to Video",
            "input": {
                "required": {"prompt": ["STRING"]},
                "optional": {
                    "ref_images": [
                        "COMFY_AUTOGROW_V3",
                        {
                            "template": {
                                "input": {"required": {"ref_image": ["IMAGE"]}},
                                "prefix": "ref_image_",
                                "min": 0,
                                "max": 9,
                            }
                        },
                    ]
                },
            },
        }
    }

    config = detect_comfyui_endpoint_config(workflow, object_info=object_info)

    assert config["metadata"]["display_name"] == "MiniMax H3 Reference to Video"
    assert config["metadata"]["workflow_kind"] == "reference_to_video"
    assert config["bindings"]["reference_images"] == {
        "node_id": "136",
        "field_prefix": "ref_images.ref_image_",
        "mode": "autogrow",
        "max_items": 9,
    }
    assert config["workflow"]["137"]["inputs"]["image"] == ""
    assert config["workflow"]["139"]["inputs"]["image"] == ""

    bound = bind_scalar_inputs(
        config,
        prompt="<张三>@图片1、<酒馆>@图片2。",
        duration_seconds=5,
        aspect_ratio="16:9",
        seed=1,
        output_prefix="arcreel/proj/task",
    )
    bind_uploaded_reference_images(
        bound,
        config["bindings"]["reference_images"],
        ["arcreel/proj/person.png", "arcreel/proj/scene.png"],
    )

    assert bound["136"]["inputs"]["prompt"] == "<张三><Picture 1>、<酒馆><Picture 2>。"
    assert bound["136"]["inputs"]["ref_images.ref_image_0"] == ["arcreel_reference_image_1", 0]
    assert bound["136"]["inputs"]["ref_images.ref_image_1"] == ["arcreel_reference_image_2", 0]
    assert bound["arcreel_reference_image_1"]["inputs"]["image"] == "arcreel/proj/person.png"
    assert bound["arcreel_reference_image_2"]["inputs"]["image"] == "arcreel/proj/scene.png"
    assert "137" not in bound
    assert "139" not in bound


def test_uses_explicit_arcreel_node_title_as_workflow_name() -> None:
    workflow = _workflow()
    workflow["10"]["_meta"] = {"title": "ArcReel: 主角首尾帧工作流"}

    config = detect_comfyui_endpoint_config(workflow, object_info=_object_info())

    assert config["metadata"]["display_name"] == "主角首尾帧工作流"


def test_workflow_profile_id_ignores_node_titles() -> None:
    first = detect_comfyui_endpoint_config(_workflow(), object_info=_object_info())
    renamed_workflow = _workflow()
    renamed_workflow["10"]["_meta"] = {"title": "ArcReel: 自定义工作流名称"}
    renamed = detect_comfyui_endpoint_config(renamed_workflow, object_info=_object_info())

    assert workflow_profile_id(first) == workflow_profile_id(renamed)
