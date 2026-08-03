"""ComfyUI workflow import and request binding unit tests."""

from __future__ import annotations

import copy

import pytest

from lib.comfyui_workflow import (
    bind_scalar_inputs,
    bind_uploaded_image,
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
