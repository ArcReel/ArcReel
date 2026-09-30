"""测试输入构造器：最小可用的端点定义，用例就地改出反例。"""

from __future__ import annotations

from typing import Any


def custom_endpoint_definition(**overrides: Any) -> dict[str, Any]:
    """最小可用的声明式调用端点定义：单张首帧、提交 + 轮询、扁平取值，校验零错误零警告。

    用例就地改出反例或补上可选构造；``overrides`` 覆盖顶层键（如换 ``meta`` 造重复血统）。
    """
    definition: dict[str, Any] = {
        "kind": "declarative",
        "schema_version": "1.1.0",
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


def _comfyui_api_workflow() -> dict[str, Any]:
    """最小可用的 ComfyUI「Export (API)」导出物：文生视频一条链路，节点 id 与真实导出同为数字串。

    每类字段各有一例：字面值（``6.text``）、连线（``6.clip``）、可绑的数值（``5.width``、``3.seed``）、
    产物节点（``9``）。用例就地改出反例。
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
    """最小可用的 ComfyUI 端点定义：绑定齐备、校验零错误。

    ``overrides`` 覆盖顶层键；改 ``bindings`` 或 ``media_type`` 即可造出各类反例。
    ``media_type="image"`` 且未自带 ``bindings`` 时不含 ``fps`` 绑定：图像端点没有帧率这一维。
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
