"""GeminiImageBackend 单元测试。"""

from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from PIL import Image as PILImage

from lib.backends.image_backends.base import ImageCapability, ImageGenerationRequest, ReferenceImage
from tests.unit.lib.backends.write_cancel_gate import WriteGate, cancel_during_write


@pytest.fixture
def fake_rate_limiter():
    rl = MagicMock()
    rl.acquire_async = AsyncMock()
    return rl


@pytest.fixture
def patch_genai():
    """Patch google.genai so GeminiImageBackend can be imported without real SDK."""
    mock_genai = MagicMock()
    mock_types = MagicMock()
    mock_genai.Client.return_value = MagicMock()
    with patch.dict(
        "sys.modules", {"google": MagicMock(), "google.genai": mock_genai, "google.genai.types": mock_types}
    ):
        import sys

        sys.modules["google"].genai = mock_genai
        mock_genai.types = mock_types
        yield mock_genai, mock_types


@pytest.fixture
def backend_aistudio(fake_rate_limiter, patch_genai):
    from lib.backends.image_backends.gemini import GeminiImageBackend

    return GeminiImageBackend(
        backend_type="aistudio",
        api_key="fake-key",
        rate_limiter=fake_rate_limiter,
    )


@pytest.fixture
def backend_vertex(fake_rate_limiter, patch_genai):
    """Create a Vertex backend, patching credential loading."""
    mock_sa = MagicMock()
    mock_sa.Credentials.from_service_account_file.return_value = MagicMock()
    mock_open = MagicMock()
    with (
        patch("lib.backends.image_backends.gemini.resolve_vertex_credentials_path") as mock_resolve,
        patch("lib.backends.image_backends.gemini.json_module") as mock_json,
        patch("builtins.open", mock_open),
        patch.dict("sys.modules", {"google.oauth2": MagicMock(), "google.oauth2.service_account": mock_sa}),
    ):
        mock_resolve.return_value = Path("/fake/creds.json")
        mock_json.load.return_value = {"project_id": "test-project"}
        from lib.backends.image_backends.gemini import GeminiImageBackend

        return GeminiImageBackend(
            backend_type="vertex",
            rate_limiter=fake_rate_limiter,
        )


class TestProperties:
    def test_name_aistudio(self, backend_aistudio):
        assert backend_aistudio.name == "gemini-aistudio"

    def test_name_vertex(self, backend_vertex):
        assert backend_vertex.name == "gemini-vertex"

    def test_model_default(self, backend_aistudio):
        assert "gemini" in backend_aistudio.model

    def test_capabilities_include_t2i_and_i2i(self, backend_aistudio):
        caps = backend_aistudio.capabilities
        assert ImageCapability.TEXT_TO_IMAGE in caps
        assert ImageCapability.IMAGE_TO_IMAGE in caps

    @pytest.mark.parametrize("model", ["gemini-3-pro-image-preview", "gemini-2.5-flash-image"])
    def test_channel_declares_no_reference_limit_by_model_name(self, fake_rate_limiter, patch_genai, model):
        # 各代 Gemini 图像模型的参考图上限是模型事实，记在条目上；通道本身不限张数。
        from lib.backends.image_backends.gemini import GeminiImageBackend

        backend = GeminiImageBackend(
            backend_type="aistudio", api_key="fake-key", rate_limiter=fake_rate_limiter, image_model=model
        )
        assert asyncio.run(backend.reference_image_limit()) is None


class TestGenerate:
    async def test_generate_calls_sdk(self, backend_aistudio, fake_rate_limiter, tmp_path):
        """generate() should call client.aio.models.generate_content and save the image."""
        output_file = tmp_path / "out.png"

        mock_image = PILImage.new("RGB", (100, 100), "red")
        mock_part = MagicMock()
        mock_part.inline_data = b"fake"
        mock_part.as_image.return_value = mock_image

        mock_response = MagicMock()
        mock_response.parts = [mock_part]

        backend_aistudio._client.aio.models.generate_content = AsyncMock(return_value=mock_response)

        request = ImageGenerationRequest(
            prompt="a red ball",
            output_path=output_file,
        )
        result = await backend_aistudio.generate(request)

        fake_rate_limiter.acquire_async.assert_awaited_once()

        backend_aistudio._client.aio.models.generate_content.assert_awaited_once()

        assert result.image_path == output_file
        assert result.provider == "gemini"
        assert output_file.exists()
        # 没发 image_size：只报输出张数，不报分辨率（按价目表默认档计）
        assert result.usage is not None
        assert result.usage.quantities == {"output_images": 1}
        assert result.usage.dimensions == {}

    async def test_save_settles_through_cancellation(self, backend_aistudio, tmp_path):
        """供应商已返回图片：取消落在解码保存期间时，保存先结算，取消随之消失，后续转码与产物提交照常执行。"""
        gate = WriteGate()
        output_file = tmp_path / "out.png"

        class _BlockingImage:
            def save(self, path):
                gate.block()
                PILImage.new("RGB", (8, 8), "red").save(path)

        mock_part = MagicMock()
        mock_part.inline_data = b"fake"
        mock_part.as_image.return_value = _BlockingImage()
        mock_response = MagicMock()
        mock_response.parts = [mock_part]
        backend_aistudio._client.aio.models.generate_content = AsyncMock(return_value=mock_response)

        steps = await cancel_during_write(
            lambda: backend_aistudio.generate(ImageGenerationRequest(prompt="a red ball", output_path=output_file)),
            gate,
        )

        assert steps == ["follow-up"]
        assert PILImage.open(output_file).size == (8, 8)

    async def test_generate_with_reference_images(self, backend_aistudio, tmp_path):
        """参考图按顺序作为图片内容送入 SDK，prompt 置于末尾，中间不夹任何名称标签。"""
        output_file = tmp_path / "out.png"
        ref_img_path = tmp_path / "characters" / "角色A.png"
        ref_img_path.parent.mkdir(parents=True, exist_ok=True)
        PILImage.new("RGB", (10, 10), "blue").save(ref_img_path)

        mock_image = PILImage.new("RGB", (100, 100), "green")
        mock_part = MagicMock()
        mock_part.inline_data = b"fake"
        mock_part.as_image.return_value = mock_image

        mock_response = MagicMock()
        mock_response.parts = [mock_part]
        backend_aistudio._client.aio.models.generate_content = AsyncMock(return_value=mock_response)

        request = ImageGenerationRequest(
            prompt="draw character",
            output_path=output_file,
            reference_images=[ReferenceImage(path=str(ref_img_path))],
        )
        await backend_aistudio.generate(request)

        call_kwargs = backend_aistudio._client.aio.models.generate_content.call_args
        contents = call_kwargs.kwargs.get("contents") or call_kwargs[1].get("contents")
        assert len(contents) == 2
        backend_aistudio._types.Part.from_bytes.assert_called_once_with(
            data=ref_img_path.read_bytes(), mime_type="image/png"
        )
        assert contents[0] is backend_aistudio._types.Part.from_bytes.return_value
        assert contents[1] == "draw character"

    async def test_generate_sends_every_reference_before_the_prompt(self, backend_aistudio, tmp_path):
        """数量校验归编排层：通道把收到的参考图全部送入 SDK，prompt 仍在末尾。"""
        refs = []
        for index in range(16):
            ref_path = tmp_path / f"ref{index}.png"
            PILImage.new("RGB", (4, 4), "blue").save(ref_path)
            refs.append(ReferenceImage(path=str(ref_path)))
        mock_part = MagicMock()
        mock_part.inline_data = b"fake"
        mock_part.as_image.return_value = PILImage.new("RGB", (10, 10), "green")
        mock_response = MagicMock()
        mock_response.parts = [mock_part]
        backend_aistudio._client.aio.models.generate_content = AsyncMock(return_value=mock_response)

        await backend_aistudio.generate(
            ImageGenerationRequest(prompt="merge", output_path=tmp_path / "out.png", reference_images=refs)
        )

        contents = backend_aistudio._client.aio.models.generate_content.call_args.kwargs["contents"]
        assert len(contents) == 17
        assert contents[-1] == "merge"

    async def test_generate_raises_on_empty_response(self, backend_aistudio, tmp_path):
        """generate() should raise RuntimeError when no image is returned."""
        output_file = tmp_path / "out.png"

        mock_response = MagicMock()
        mock_response.parts = []
        backend_aistudio._client.aio.models.generate_content = AsyncMock(return_value=mock_response)

        request = ImageGenerationRequest(prompt="test", output_path=output_file)
        with pytest.raises(RuntimeError, match="未返回图片"):
            await backend_aistudio.generate(request)


async def test_corrupt_and_missing_references_are_reported_before_submission(tmp_path, backend_aistudio):
    from lib.backends.image_errors import ImageCapabilityError
    from tests.http_capture import capture_http

    backend = backend_aistudio
    corrupt = tmp_path / "broken.png"
    corrupt.write_bytes(b"bad")
    refs = [ReferenceImage(path=str(path)) for path in (corrupt, tmp_path / "gone.png")]
    with capture_http() as router, pytest.raises(ImageCapabilityError) as caught:
        await backend.generate(
            ImageGenerationRequest(prompt="p", reference_images=refs, output_path=tmp_path / "out.png")
        )
    assert caught.value.code == "image_reference_images_unreadable"
    assert caught.value.params["names"] == "broken.png, gone.png"
    assert not router.calls
    backend._client.aio.models.generate_content.assert_not_called()
