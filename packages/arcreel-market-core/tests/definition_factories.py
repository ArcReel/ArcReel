"""Test-input factories: minimally valid endpoint definitions, mutated in place by tests into counterexamples."""

from __future__ import annotations

from typing import Any


def custom_endpoint_definition(**overrides: Any) -> dict[str, Any]:
    """Minimally valid declarative endpoint: one first frame, submit + poll, flat extraction, zero validation errors or warnings.

    Tests mutate counterexamples in place or add optional constructs; ``overrides`` replaces top-level keys (such as ``meta`` for duplicate lineage).
    """
    definition: dict[str, Any] = {
        "kind": "declarative",
        "schema_version": "1.2.0",
        "meta": {"name": "示例端点", "author": "ArcReel", "version": "0.1.0"},
        "auth": {"headers": {"Authorization": "Bearer {{ api_key }}"}},
        "inputs": {"first_frame": {"source": "start_image", "encoding": "data_uri"}},
        "enum_maps": {"duration": {"5": 5, "10": 10}},
        "submit": {
            "method": "POST",
            "url": "{{ base_url }}/v1/video/create",
            "body": {
                "model": "{{ model }}",
                "prompt": "{{ prompt }}",
                "image": "{{ inputs.first_frame }}",
                "duration": "{{ duration }}",
            },
            "extract": {"task_id": ["$.task_id"], "error": ["$.error.message"]},
        },
        "poll": {
            "method": "GET",
            "url": "{{ base_url }}/v1/video/fetch/{{ task_id }}",
            "extract": {"status": ["$.status"], "video_url": ["$.video_url"], "error": ["$.error"]},
        },
        "status_map": {"pending": "queued", "processing": "running", "completed": "succeeded", "failed": "failed"},
        "capabilities": {"first_frame": True},
    }
    definition.update(overrides)
    return definition


def image_endpoint_definition(**overrides: Any) -> dict[str, Any]:
    """Minimally valid declarative image definition: text-to-image, submit + poll, image URL extraction, zero errors or warnings.

    Use the OpenAI-style-paths plus asynchronous-jobs provider family: submission returns ``data[0].task_id``, polling reads
    ``data.status``, and images use ``data.result.images[0].url[0]``. Tests mutate counterexamples in place.
    """
    definition: dict[str, Any] = {
        "kind": "declarative",
        "schema_version": "1.2.0",
        "media_type": "image",
        "meta": {"name": "示例图片端点", "author": "ArcReel", "version": "0.1.0"},
        "auth": {"headers": {"Authorization": "Bearer {{ api_key }}"}},
        "submit": {
            "method": "POST",
            "url": "{{ base_url }}/v1/images/generations",
            "body": {
                "model": "{{ model }}",
                "prompt": "{{ prompt }}",
                "size": "{{ width }}x{{ height }}",
                "seed": "{{ seed }}",
            },
            "extract": {"task_id": ["$.data[0].task_id"], "error": ["$.error.message"]},
        },
        "poll": {
            "method": "GET",
            "url": "{{ base_url }}/v1/tasks/{{ task_id }}",
            "extract": {
                "status": ["$.data.status"],
                "image_url": ["$.data.result.images[0].url[0]"],
                "error": ["$.data.error.message"],
            },
        },
        "status_map": {
            "pending": "queued",
            "processing": "running",
            "completed": "succeeded",
            "failed": "failed",
            "cancelled": "failed",
        },
        "capabilities": {"text_to_image": True},
    }
    definition.update(overrides)
    return definition


def _comfyui_api_workflow() -> dict[str, Any]:
    """Minimal ComfyUI Export (API): one text-to-video path, with numeric-string node IDs matching real exports.

    One example per field kind: literals (``6.text``), links (``6.clip``), bindable numbers (``5.width``, ``3.seed``),
    and artifact nodes (``9``). Tests mutate counterexamples in place.
    """
    return {
        "3": {
            "class_type": "KSampler",
            "inputs": {
                "seed": 123456,
                "steps": 20,
                "model": ["4", 0],
                "positive": ["6", 0],
                "negative": ["7", 0],
                "latent_image": ["5", 0],
            },
            "_meta": {"title": "KSampler"},
        },
        "4": {"class_type": "CheckpointLoaderSimple", "inputs": {"ckpt_name": "wan_2_2.safetensors"}},
        "5": {"class_type": "EmptyLatentImage", "inputs": {"width": 832, "height": 480, "batch_size": 1}},
        "6": {
            "class_type": "CLIPTextEncode",
            "inputs": {"text": "一只猫", "clip": ["4", 1]},
            "_meta": {"title": "正向"},
        },
        "7": {"class_type": "CLIPTextEncode", "inputs": {"text": "", "clip": ["4", 1]}, "_meta": {"title": "负向"}},
        "8": {"class_type": "VAEDecode", "inputs": {"samples": ["3", 0], "vae": ["4", 2]}},
        "9": {"class_type": "SaveVideo", "inputs": {"images": ["8", 0], "fps": 16}, "_meta": {"title": "存视频"}},
    }


def comfyui_endpoint_definition(**overrides: Any) -> dict[str, Any]:
    """Minimally valid ComfyUI endpoint: complete bindings and zero validation errors.

    ``overrides`` replaces top-level keys; mutate ``bindings`` or ``media_type`` for different counterexamples.
    With ``media_type="image"`` and no supplied ``bindings``, omit ``fps`` bindings: image endpoints have no frame-rate dimension.
    """
    definition: dict[str, Any] = {
        "kind": "comfyui",
        "schema_version": "1.0.0",
        "meta": {"name": "示例 ComfyUI 端点", "author": "ArcReel", "version": "0.1.0"},
        "media_type": "video",
        "workflow": _comfyui_api_workflow(),
        "bindings": {
            "prompt": [{"node": "6", "input": "text", "class_type": "CLIPTextEncode", "title": "正向"}],
            "negative_prompt": [{"node": "7", "input": "text", "class_type": "CLIPTextEncode", "title": "负向"}],
            "width": [{"node": "5", "input": "width", "class_type": "EmptyLatentImage", "step": 16}],
            "height": [{"node": "5", "input": "height", "class_type": "EmptyLatentImage", "step": 16}],
            "seed": [{"node": "3", "input": "seed", "class_type": "KSampler", "policy": "random"}],
            "fps": [{"node": "9", "input": "fps", "class_type": "SaveVideo", "direction": "read"}],
            "output": [{"node": "9", "class_type": "SaveVideo", "title": "存视频"}],
        },
    }
    definition.update(overrides)
    if definition["media_type"] == "image" and "bindings" not in overrides:
        del definition["bindings"]["fps"]
    return definition
