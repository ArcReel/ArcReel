"""BailianImageBackend 单元测试。"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from lib.image_backends.base import ImageGenerationRequest, ReferenceImage
from lib.providers import PROVIDER_BAILIAN


class _FakeAsyncClient:
    def __init__(self, response: MagicMock):
        self._response = response
        self.post = AsyncMock(return_value=response)
        self.get = AsyncMock(return_value=response)

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False


@pytest.fixture()
def backend():
    from lib.image_backends.bailian import BailianImageBackend

    return BailianImageBackend(api_key="test-key")


class TestBailianImageBackend:
    def test_properties(self, backend):
        assert backend.name == PROVIDER_BAILIAN
        assert backend.model == "wan2.7-image-pro"

    async def test_submit_task_uses_valid_t2i_size_for_portrait(self, backend, tmp_path: Path):
        response = MagicMock()
        response.json.return_value = {"output": {"task_id": "task-123"}}
        response.raise_for_status = MagicMock()
        fake_client = _FakeAsyncClient(response)

        request = ImageGenerationRequest(
            prompt="hero portrait",
            output_path=tmp_path / "out.png",
            aspect_ratio="9:16",
            image_size="1K",
        )

        with patch("lib.image_backends.bailian.httpx.AsyncClient", return_value=fake_client):
            task_id = await backend._submit_task(request)

        assert task_id == "task-123"
        payload = fake_client.post.await_args.kwargs["json"]
        assert payload["parameters"]["size"] == "864*1536"

    async def test_submit_task_uses_shortcut_size_for_i2i(self, backend, tmp_path: Path):
        response = MagicMock()
        response.json.return_value = {"output": {"task_id": "task-456"}}
        response.raise_for_status = MagicMock()
        fake_client = _FakeAsyncClient(response)

        ref = tmp_path / "ref.png"
        ref.write_bytes(b"fake-image")

        request = ImageGenerationRequest(
            prompt="hero portrait",
            output_path=tmp_path / "out.png",
            reference_images=[ReferenceImage(path=str(ref))],
            aspect_ratio="9:16",
            image_size="1K",
        )

        with (
            patch("lib.image_backends.bailian.httpx.AsyncClient", return_value=fake_client),
            patch("lib.image_backends.bailian.upload_file_and_get_url", new=AsyncMock(return_value="oss://bucket/ref.png")),
        ):
            task_id = await backend._submit_task(request)

        assert task_id == "task-456"
        payload = fake_client.post.await_args.kwargs["json"]
        assert payload["parameters"]["size"] == "1K"

    async def test_submit_task_logs_error_body_on_http_error(self, backend, tmp_path: Path):
        request = ImageGenerationRequest(prompt="hero portrait", output_path=tmp_path / "out.png")
        response = MagicMock()
        response.status_code = 400
        response.text = '{"code":"InvalidParameter"}'
        response.request = httpx.Request("POST", "https://dashscope.aliyuncs.com/api/v1/services/aigc/image-generation/generation")
        response.raise_for_status.side_effect = httpx.HTTPStatusError(
            "Client error '400 Bad Request'",
            request=response.request,
            response=response,
        )
        fake_client = _FakeAsyncClient(response)

        with patch("lib.image_backends.bailian.httpx.AsyncClient", return_value=fake_client):
            with pytest.raises(httpx.HTTPStatusError):
                await backend._submit_task(request)
