"""ComfyUI video backend transport and durable-download unit tests."""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest

from lib.comfyui_workflow import detect_comfyui_endpoint_config
from lib.video_backends.base import VideoGenerationRequest
from lib.video_backends.comfyui import ComfyUIVideoBackend

pytestmark = pytest.mark.unit


def _config() -> dict:
    workflow = {
        "10": {
            "class_type": "MiniMaxH3ImageToVideo",
            "inputs": {"prompt": "old", "first_frame": ["20", 0]},
        },
        "20": {"class_type": "LoadImage", "inputs": {"image": "old.png"}},
        "90": {"class_type": "SaveVideo", "inputs": {"filename_prefix": "test", "video": ["10", 0]}},
    }
    return detect_comfyui_endpoint_config(workflow)


def _reference_config() -> dict:
    workflow = {
        "10": {
            "class_type": "MiniMaxH3ReferenceToVideo",
            "inputs": {
                "prompt": "old",
                "ref_images.ref_image_0": ["20", 0],
            },
        },
        "20": {"class_type": "LoadImage", "inputs": {"image": "old.png"}},
        "90": {"class_type": "SaveVideo", "inputs": {"filename_prefix": "test", "video": ["10", 0]}},
    }
    object_info = {
        "MiniMaxH3ReferenceToVideo": {
            "input": {
                "required": {"prompt": ["STRING"]},
                "optional": {
                    "ref_images": [
                        "COMFY_AUTOGROW_V3",
                        {
                            "template": {
                                "input": {"required": {"ref_image": ["IMAGE"]}},
                                "prefix": "ref_image_",
                                "max": 9,
                            }
                        },
                    ]
                },
            }
        }
    }
    return detect_comfyui_endpoint_config(workflow, object_info=object_info)


async def test_generate_uploads_submits_polls_and_downloads(monkeypatch, tmp_path: Path) -> None:
    calls: list[tuple[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append((request.method, request.url.path))
        if request.url.path == "/upload/image":
            return httpx.Response(200, json={"name": "start.png", "subfolder": "arcreel/proj/task"})
        if request.url.path == "/prompt":
            return httpx.Response(200, json={"prompt_id": "prompt-1", "node_errors": {}})
        if request.url.path == "/history/prompt-1":
            return httpx.Response(
                200,
                json={
                    "prompt-1": {
                        "status": {"completed": True, "status_str": "success"},
                        "outputs": {
                            "90": {"gifs": [{"filename": "result.mp4", "subfolder": "jobs", "type": "output"}]}
                        },
                    }
                },
            )
        if request.url.path == "/view":
            return httpx.Response(200, content=b"video-bytes")
        return httpx.Response(404)

    real_client = httpx.AsyncClient

    def client_factory(*args, **kwargs):
        kwargs["transport"] = httpx.MockTransport(handler)
        return real_client(*args, **kwargs)

    monkeypatch.setattr("lib.video_backends.comfyui.httpx.AsyncClient", client_factory)
    source = tmp_path / "frame.png"
    source.write_bytes(b"png")
    target = tmp_path / "output" / "clip.mp4"
    backend = ComfyUIVideoBackend(
        base_url="http://comfy.local:8188",
        model="comfy-profile",
        endpoint_config=_config(),
    )

    result = await backend.generate(
        VideoGenerationRequest(
            prompt="move forward",
            output_path=target,
            start_image=source,
            project_name="proj",
        )
    )

    assert target.read_bytes() == b"video-bytes"
    assert result.video_path == target
    assert result.video_uri is None
    assert result.task_id == "prompt-1"
    assert calls == [
        ("POST", "/upload/image"),
        ("POST", "/prompt"),
        ("GET", "/history/prompt-1"),
        ("GET", "/view"),
    ]


async def test_generate_uploads_and_binds_multiple_reference_images(monkeypatch, tmp_path: Path) -> None:
    submitted_workflow: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal submitted_workflow
        if request.url.path == "/upload/image":
            filename = request.content.split(b'filename="', 1)[1].split(b'"', 1)[0].decode()
            return httpx.Response(200, json={"name": filename, "subfolder": "arcreel/proj/task"})
        if request.url.path == "/prompt":
            import json

            submitted_workflow = json.loads(request.content)["prompt"]
            return httpx.Response(200, json={"prompt_id": "prompt-refs", "node_errors": {}})
        if request.url.path == "/history/prompt-refs":
            return httpx.Response(
                200,
                json={
                    "prompt-refs": {
                        "status": {"completed": True, "status_str": "success"},
                        "outputs": {"90": {"gifs": [{"filename": "result.mp4", "subfolder": "jobs"}]}},
                    }
                },
            )
        if request.url.path == "/view":
            return httpx.Response(200, content=b"video")
        return httpx.Response(404)

    real_client = httpx.AsyncClient

    def client_factory(*args, **kwargs):
        kwargs["transport"] = httpx.MockTransport(handler)
        return real_client(*args, **kwargs)

    monkeypatch.setattr("lib.video_backends.comfyui.httpx.AsyncClient", client_factory)
    first = tmp_path / "person.png"
    second = tmp_path / "scene.png"
    first.write_bytes(b"person")
    second.write_bytes(b"scene")
    target = tmp_path / "result.mp4"
    backend = ComfyUIVideoBackend(
        base_url="http://comfy.local:8188",
        model="comfy-reference",
        endpoint_config=_reference_config(),
    )

    assert backend.video_capabilities.first_frame is False
    assert backend.video_capabilities.max_reference_images == 9
    await backend.generate(
        VideoGenerationRequest(
            prompt="<张三>@图片1、<酒馆>@图片2。",
            output_path=target,
            reference_images=[first, second],
            project_name="proj",
        )
    )

    assert submitted_workflow["10"]["inputs"]["prompt"] == "<张三><Picture 1>、<酒馆><Picture 2>。"
    assert submitted_workflow["10"]["inputs"]["ref_images.ref_image_0"][0] == "arcreel_reference_image_1"
    assert submitted_workflow["10"]["inputs"]["ref_images.ref_image_1"][0] == "arcreel_reference_image_2"
    assert submitted_workflow["arcreel_reference_image_1"]["inputs"]["image"].endswith("reference_1_person.png")
    assert submitted_workflow["arcreel_reference_image_2"]["inputs"]["image"].endswith("reference_2_scene.png")
    assert target.read_bytes() == b"video"
