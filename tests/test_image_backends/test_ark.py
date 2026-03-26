"""ArkImageBackend 单元测试。"""
from __future__ import annotations

import base64
from dataclasses import dataclass
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from lib.image_backends.base import (
    ImageCapability,
    ImageGenerationRequest,
    ImageGenerationResult,
    ReferenceImage,
)
from lib.providers import PROVIDER_ARK


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

FAKE_B64 = base64.b64encode(b"fake-png-data").decode()


@dataclass
class _FakeImageData:
    b64_json: str = FAKE_B64
    url: str | None = None


@dataclass
class _FakeImagesResponse:
    data: list[_FakeImageData]


def _make_client_mock() -> MagicMock:
    """Return a mock Ark client whose images.generate returns a valid response."""
    client = MagicMock()
    client.images.generate.return_value = _FakeImagesResponse(data=[_FakeImageData()])
    return client


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestArkImageBackendInit:
    """构造函数测试。"""

    def test_missing_api_key_raises(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.delenv("ARK_API_KEY", raising=False)
        from lib.image_backends.ark import ArkImageBackend

        with pytest.raises(ValueError, match="Ark API Key"):
            ArkImageBackend(api_key=None)

    def test_api_key_from_env(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("ARK_API_KEY", "env-key")
        with patch("volcenginesdkarkruntime.Ark") as MockArk:
            from lib.image_backends.ark import ArkImageBackend

            backend = ArkImageBackend()
            MockArk.assert_called_once()
            assert backend.name == PROVIDER_ARK

    def test_api_key_from_param(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.delenv("ARK_API_KEY", raising=False)
        with patch("volcenginesdkarkruntime.Ark") as MockArk:
            from lib.image_backends.ark import ArkImageBackend

            ArkImageBackend(api_key="my-key")
            MockArk.assert_called_once_with(
                base_url="https://ark.cn-beijing.volces.com/api/v3",
                api_key="my-key",
            )


class TestArkImageBackendProperties:
    """属性测试。"""

    @pytest.fixture()
    def backend(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.delenv("ARK_API_KEY", raising=False)
        with patch("volcenginesdkarkruntime.Ark"):
            from lib.image_backends.ark import ArkImageBackend

            return ArkImageBackend(api_key="test-key")

    def test_name(self, backend):
        assert backend.name == PROVIDER_ARK

    def test_default_model(self, backend):
        assert backend.model == "doubao-seedream-5-0-lite-260128"

    def test_custom_model(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.delenv("ARK_API_KEY", raising=False)
        with patch("volcenginesdkarkruntime.Ark"):
            from lib.image_backends.ark import ArkImageBackend

            b = ArkImageBackend(api_key="k", model="custom-model")
            assert b.model == "custom-model"

    def test_capabilities(self, backend):
        assert backend.capabilities == {
            ImageCapability.TEXT_TO_IMAGE,
            ImageCapability.IMAGE_TO_IMAGE,
        }


class TestArkImageBackendGenerate:
    """generate() 方法测试。"""

    @pytest.fixture()
    def backend_and_client(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.delenv("ARK_API_KEY", raising=False)
        mock_client = _make_client_mock()
        with patch("volcenginesdkarkruntime.Ark", return_value=mock_client):
            from lib.image_backends.ark import ArkImageBackend

            backend = ArkImageBackend(api_key="test-key")
        return backend, mock_client

    async def test_t2i_generate(self, backend_and_client, tmp_path: Path):
        backend, client = backend_and_client
        output = tmp_path / "out.png"
        request = ImageGenerationRequest(prompt="a cat", output_path=output)

        result = await backend.generate(request)

        # SDK called correctly
        call_kwargs = client.images.generate.call_args
        assert call_kwargs.kwargs["model"] == "doubao-seedream-5-0-lite-260128"
        assert call_kwargs.kwargs["prompt"] == "a cat"
        assert call_kwargs.kwargs["response_format"] == "b64_json"
        assert "image" not in call_kwargs.kwargs

        # Result
        assert isinstance(result, ImageGenerationResult)
        assert result.provider == PROVIDER_ARK
        assert result.image_path == output
        assert output.exists()
        assert output.read_bytes() == base64.b64decode(FAKE_B64)

    async def test_t2i_with_seed(self, backend_and_client, tmp_path: Path):
        backend, client = backend_and_client
        output = tmp_path / "out.png"
        request = ImageGenerationRequest(prompt="a dog", output_path=output, seed=42)

        await backend.generate(request)

        call_kwargs = client.images.generate.call_args.kwargs
        assert call_kwargs["seed"] == 42

    async def test_i2i_single_ref(self, backend_and_client, tmp_path: Path):
        backend, client = backend_and_client

        # Prepare a reference image file
        ref_file = tmp_path / "ref.png"
        ref_file.write_bytes(b"ref-image-bytes")
        expected_data_uri = "data:image/png;base64," + base64.b64encode(b"ref-image-bytes").decode()

        output = tmp_path / "out.png"
        request = ImageGenerationRequest(
            prompt="enhance this",
            output_path=output,
            reference_images=[ReferenceImage(path=str(ref_file))],
        )

        await backend.generate(request)

        call_kwargs = client.images.generate.call_args.kwargs
        assert call_kwargs["image"] == expected_data_uri

    async def test_i2i_multiple_refs(self, backend_and_client, tmp_path: Path):
        backend, client = backend_and_client

        ref1 = tmp_path / "a.png"
        ref2 = tmp_path / "b.png"
        ref1.write_bytes(b"img-a")
        ref2.write_bytes(b"img-b")

        output = tmp_path / "out.png"
        request = ImageGenerationRequest(
            prompt="merge",
            output_path=output,
            reference_images=[
                ReferenceImage(path=str(ref1)),
                ReferenceImage(path=str(ref2)),
            ],
        )

        await backend.generate(request)

        call_kwargs = client.images.generate.call_args.kwargs
        assert call_kwargs["image"] == [
            "data:image/png;base64," + base64.b64encode(b"img-a").decode(),
            "data:image/png;base64," + base64.b64encode(b"img-b").decode(),
        ]

    async def test_output_dir_created(self, backend_and_client, tmp_path: Path):
        backend, _ = backend_and_client
        output = tmp_path / "sub" / "dir" / "out.png"
        request = ImageGenerationRequest(prompt="test", output_path=output)

        await backend.generate(request)

        assert output.exists()
