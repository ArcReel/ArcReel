"""NewAPIVideoBackend 单元测试（mock httpx）。"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from lib.providers import PROVIDER_NEWAPI
from lib.video_backends.base import (
    VideoCapability,
    VideoGenerationRequest,
)


def _make_response(status_code: int, json_body: dict) -> MagicMock:
    resp = MagicMock()
    resp.status_code = status_code
    resp.json.return_value = json_body
    resp.raise_for_status = MagicMock()
    return resp


class TestNewAPIVideoBackend:
    def test_name_and_model(self):
        from lib.video_backends.newapi import NewAPIVideoBackend

        backend = NewAPIVideoBackend(api_key="sk-test", base_url="https://example.com/v1", model="kling-v1")
        assert backend.name == PROVIDER_NEWAPI
        assert backend.model == "kling-v1"

    def test_capabilities(self):
        from lib.video_backends.newapi import NewAPIVideoBackend

        backend = NewAPIVideoBackend(api_key="sk-test", base_url="https://x/v1", model="m")
        assert VideoCapability.TEXT_TO_VIDEO in backend.capabilities
        assert VideoCapability.IMAGE_TO_VIDEO in backend.capabilities
        assert backend.video_capabilities.reference_images is False
        assert backend.video_capabilities.max_reference_images == 0

    async def test_text_to_video_happy_path(self, tmp_path: Path):
        create_resp = _make_response(200, {"task_id": "task-42", "status": "queued"})
        poll_resp = _make_response(
            200,
            {
                "task_id": "task-42",
                "status": "completed",
                "url": "https://cdn.example.com/out.mp4",
                "format": "mp4",
                "metadata": {"duration": 5, "fps": 24, "width": 720, "height": 1280, "seed": 0},
            },
        )
        download_resp = MagicMock()
        download_resp.status_code = 200
        download_resp.content = b"mp4-bytes"
        download_resp.raise_for_status = MagicMock()

        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=create_resp)
        mock_client.get = AsyncMock(side_effect=[poll_resp, download_resp])
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)

        with (
            patch("httpx.AsyncClient", return_value=mock_client),
            patch("lib.video_backends.newapi._POLL_INTERVAL_SECONDS", 0.0),
        ):
            from lib.video_backends.newapi import NewAPIVideoBackend

            backend = NewAPIVideoBackend(api_key="sk-test", base_url="https://example.com/v1", model="kling-v1")
            request = VideoGenerationRequest(
                prompt="A cat running",
                output_path=tmp_path / "out.mp4",
                aspect_ratio="9:16",
                resolution="720p",
                duration_seconds=5,
            )
            result = await backend.generate(request)

        assert result.video_path == tmp_path / "out.mp4"
        assert result.video_path.read_bytes() == b"mp4-bytes"
        assert result.provider == PROVIDER_NEWAPI
        assert result.model == "kling-v1"
        assert result.duration_seconds == 5
        assert result.task_id == "task-42"

        post_call = mock_client.post.call_args
        assert post_call.args[0].endswith("/video/generations")
        assert post_call.kwargs["json"]["model"] == "kling-v1"
        assert post_call.kwargs["json"]["prompt"] == "A cat running"
        assert post_call.kwargs["json"]["width"] == 720
        assert post_call.kwargs["json"]["height"] == 1280
        assert post_call.kwargs["json"]["duration"] == 5
        assert post_call.kwargs["json"]["n"] == 1
        assert "image" not in post_call.kwargs["json"]
        assert post_call.kwargs["headers"]["Authorization"] == "Bearer sk-test"

    async def test_image_to_video_encodes_base64(self, tmp_path: Path):
        img_path = tmp_path / "start.png"
        img_path.write_bytes(b"\x89PNG\r\nfake")

        create_resp = _make_response(200, {"task_id": "t1", "status": "queued"})
        poll_resp = _make_response(
            200,
            {
                "task_id": "t1",
                "status": "completed",
                "url": "https://cdn/x.mp4",
                "metadata": {"duration": 5},
            },
        )
        download_resp = MagicMock()
        download_resp.status_code = 200
        download_resp.content = b"v"
        download_resp.raise_for_status = MagicMock()

        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=create_resp)
        mock_client.get = AsyncMock(side_effect=[poll_resp, download_resp])
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)

        with (
            patch("httpx.AsyncClient", return_value=mock_client),
            patch("lib.video_backends.newapi._POLL_INTERVAL_SECONDS", 0.0),
        ):
            from lib.video_backends.newapi import NewAPIVideoBackend

            backend = NewAPIVideoBackend(api_key="k", base_url="https://x/v1", model="kling-v1")
            await backend.generate(
                VideoGenerationRequest(
                    prompt="p",
                    output_path=tmp_path / "o.mp4",
                    start_image=img_path,
                    resolution="720p",
                    aspect_ratio="9:16",
                    duration_seconds=5,
                )
            )

        sent_image = mock_client.post.call_args.kwargs["json"]["image"]
        assert sent_image.startswith("data:image/png;base64,")
        assert "fake" not in sent_image  # 必须编码过

    async def test_failed_status_raises(self, tmp_path: Path):
        create_resp = _make_response(200, {"task_id": "t2", "status": "queued"})
        poll_resp = _make_response(
            200,
            {
                "task_id": "t2",
                "status": "failed",
                "error": {"code": 500, "message": "upstream down"},
            },
        )
        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=create_resp)
        mock_client.get = AsyncMock(return_value=poll_resp)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)

        with (
            patch("httpx.AsyncClient", return_value=mock_client),
            patch("lib.video_backends.newapi._POLL_INTERVAL_SECONDS", 0.0),
        ):
            from lib.video_backends.newapi import NewAPIVideoBackend

            backend = NewAPIVideoBackend(api_key="k", base_url="https://x/v1", model="m")
            with pytest.raises(RuntimeError, match="upstream down"):
                await backend.generate(
                    VideoGenerationRequest(
                        prompt="p",
                        output_path=tmp_path / "o.mp4",
                        resolution="720p",
                        aspect_ratio="9:16",
                        duration_seconds=5,
                    )
                )

    async def test_polls_through_in_progress(self, tmp_path: Path):
        create_resp = _make_response(200, {"task_id": "t3", "status": "queued"})
        in_progress = _make_response(200, {"task_id": "t3", "status": "in_progress"})
        completed = _make_response(
            200,
            {
                "task_id": "t3",
                "status": "completed",
                "url": "https://cdn/v.mp4",
                "metadata": {"duration": 5},
            },
        )
        download_resp = MagicMock()
        download_resp.status_code = 200
        download_resp.content = b"v"
        download_resp.raise_for_status = MagicMock()

        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=create_resp)
        mock_client.get = AsyncMock(side_effect=[in_progress, in_progress, completed, download_resp])
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)

        with (
            patch("httpx.AsyncClient", return_value=mock_client),
            patch("lib.video_backends.newapi._POLL_INTERVAL_SECONDS", 0.0),
        ):
            from lib.video_backends.newapi import NewAPIVideoBackend

            backend = NewAPIVideoBackend(api_key="k", base_url="https://x/v1", model="m")
            result = await backend.generate(
                VideoGenerationRequest(
                    prompt="p",
                    output_path=tmp_path / "o.mp4",
                    resolution="720p",
                    aspect_ratio="9:16",
                    duration_seconds=5,
                )
            )

        assert result.task_id == "t3"
        assert mock_client.get.call_count == 4
