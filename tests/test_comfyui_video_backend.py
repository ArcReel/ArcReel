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
