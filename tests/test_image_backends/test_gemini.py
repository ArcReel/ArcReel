"""GeminiImageBackend 单元测试。"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from PIL import Image as PILImage

from lib.image_backends.base import ImageCapability, ImageGenerationRequest, ReferenceImage


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def fake_rate_limiter():
    rl = MagicMock()
    rl.acquire_async = AsyncMock()
    return rl


@pytest.fixture()
def _patch_genai():
    """Patch google.genai so GeminiImageBackend can be imported without real SDK."""
    mock_genai = MagicMock()
    mock_types = MagicMock()
    mock_genai.Client.return_value = MagicMock()
    with patch.dict("sys.modules", {"google": MagicMock(), "google.genai": mock_genai, "google.genai.types": mock_types}):
        # Make `from google import genai` return our mock
        import sys
        sys.modules["google"].genai = mock_genai
        mock_genai.types = mock_types
        yield mock_genai, mock_types


@pytest.fixture()
def backend_aistudio(fake_rate_limiter, _patch_genai):
    from lib.image_backends.gemini import GeminiImageBackend
    return GeminiImageBackend(
        backend_type="aistudio",
        api_key="fake-key",
        rate_limiter=fake_rate_limiter,
    )


@pytest.fixture()
def backend_vertex(fake_rate_limiter, _patch_genai):
    """Create a Vertex backend, patching credential loading."""
    mock_sa = MagicMock()
    mock_sa.Credentials.from_service_account_file.return_value = MagicMock()
    mock_open = MagicMock()
    with patch("lib.image_backends.gemini.resolve_vertex_credentials_path") as mock_resolve, \
         patch("lib.image_backends.gemini.json_module") as mock_json, \
         patch("builtins.open", mock_open), \
         patch.dict("sys.modules", {"google.oauth2": MagicMock(), "google.oauth2.service_account": mock_sa}):
        mock_resolve.return_value = Path("/fake/creds.json")
        mock_json.load.return_value = {"project_id": "test-project"}
        from lib.image_backends.gemini import GeminiImageBackend
        return GeminiImageBackend(
            backend_type="vertex",
            rate_limiter=fake_rate_limiter,
        )


# ---------------------------------------------------------------------------
# Tests: properties
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# Tests: generate
# ---------------------------------------------------------------------------

class TestGenerate:
    async def test_generate_calls_sdk(self, backend_aistudio, fake_rate_limiter, tmp_path):
        """generate() should call client.aio.models.generate_content and save the image."""
        output_file = tmp_path / "out.png"

        # Mock the response: a part with inline_data that returns a PIL image
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

        # Verify rate limiter was called
        fake_rate_limiter.acquire_async.assert_awaited_once()

        # Verify SDK was called
        backend_aistudio._client.aio.models.generate_content.assert_awaited_once()

        # Verify result
        assert result.image_path == output_file
        assert result.provider == "gemini"
        assert output_file.exists()

    async def test_generate_with_reference_images(self, backend_aistudio, tmp_path):
        """generate() should build contents with reference images."""
        output_file = tmp_path / "out.png"
        ref_img_path = tmp_path / "characters" / "角色A.png"
        ref_img_path.parent.mkdir(parents=True, exist_ok=True)
        # Create a small test image
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
            reference_images=[ReferenceImage(path=str(ref_img_path), label="角色A")],
        )
        await backend_aistudio.generate(request)

        # Verify contents were built: should contain label, image, and prompt
        call_kwargs = backend_aistudio._client.aio.models.generate_content.call_args
        contents = call_kwargs.kwargs.get("contents") or call_kwargs[1].get("contents")
        # At minimum: label string, PIL image, prompt string
        assert len(contents) >= 3
        assert contents[0] == "角色A"
        assert contents[-1] == "draw character"

    async def test_generate_raises_on_empty_response(self, backend_aistudio, tmp_path):
        """generate() should raise RuntimeError when no image is returned."""
        output_file = tmp_path / "out.png"

        mock_response = MagicMock()
        mock_response.parts = []
        backend_aistudio._client.aio.models.generate_content = AsyncMock(return_value=mock_response)

        request = ImageGenerationRequest(prompt="test", output_path=output_file)
        with pytest.raises(RuntimeError, match="未返回图片"):
            await backend_aistudio.generate(request)


# ---------------------------------------------------------------------------
# Tests: helper methods
# ---------------------------------------------------------------------------

class TestHelpers:
    def test_extract_name_from_path_normal(self, backend_aistudio):
        assert backend_aistudio._extract_name_from_path("/path/to/角色A.png") == "角色A"

    def test_extract_name_skips_scene_prefix(self, backend_aistudio):
        assert backend_aistudio._extract_name_from_path("/path/scene_001.png") is None

    def test_extract_name_skips_storyboard_prefix(self, backend_aistudio):
        assert backend_aistudio._extract_name_from_path("/path/storyboard_001.png") is None

    def test_extract_name_skips_output_prefix(self, backend_aistudio):
        assert backend_aistudio._extract_name_from_path("/path/output_001.png") is None

    def test_load_image_detached(self, tmp_path):
        """_load_image_detached should return a copy not holding the file handle."""
        img_path = tmp_path / "test.png"
        PILImage.new("RGB", (10, 10), "red").save(img_path)
        from lib.image_backends.gemini import GeminiImageBackend
        loaded = GeminiImageBackend._load_image_detached(img_path)
        assert isinstance(loaded, PILImage.Image)
