"""ArkImageBackend 单元测试。"""

from __future__ import annotations

import base64
from dataclasses import dataclass
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

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

    def test_api_key_from_env_no_longer_supported(self, monkeypatch: pytest.MonkeyPatch):
        """spec §5.4：env fallback 已删除——即使 ARK_API_KEY 在环境中，缺失 api_key 仍 raise。"""
        monkeypatch.setenv("ARK_API_KEY", "env-key")
        from lib.image_backends.ark import ArkImageBackend

        with pytest.raises(ValueError, match="Ark API Key"):
            ArkImageBackend(api_key=None)

    def test_api_key_from_param(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.delenv("ARK_API_KEY", raising=False)
        with patch("lib.image_backends.ark.create_ark_client") as mock_create:
            from lib.image_backends.ark import ArkImageBackend

            ArkImageBackend(api_key="my-key")
            mock_create.assert_called_once_with(api_key="my-key")


class TestArkImageBackendProperties:
    """属性测试。"""

    @pytest.fixture()
    def backend(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.delenv("ARK_API_KEY", raising=False)
        with patch("lib.image_backends.ark.create_ark_client"):
            from lib.image_backends.ark import ArkImageBackend

            return ArkImageBackend(api_key="test-key")

    def test_name(self, backend):
        assert backend.name == PROVIDER_ARK

    def test_default_model(self, backend):
        assert backend.model == "doubao-seedream-5-0-lite-260128"

    def test_custom_model(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.delenv("ARK_API_KEY", raising=False)
        with patch("lib.image_backends.ark.create_ark_client"):
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
        with patch("lib.image_backends.ark.create_ark_client", return_value=mock_client):
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
        # 部分兼容网关即便吃 response_format 仍返回 url，所以请求端不再传该参数
        assert "response_format" not in call_kwargs.kwargs
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

    async def test_size_from_aspect_ratio(self, backend_and_client, tmp_path: Path):
        """aspect_ratio 必须映射成显式 size 传给 SDK，否则 Seedream 默认 2048x2048（1:1），
        导致项目设置失效。尺寸值按 Ark 官方推荐宽高像素表（2K 档，4.x/5.x 系列）。"""
        backend, client = backend_and_client

        cases = [
            ("9:16", "1600x2848"),
            ("16:9", "2848x1600"),
            ("1:1", "2048x2048"),
            ("4:3", "2304x1728"),
            ("3:4", "1728x2304"),
        ]
        for i, (ar, expected) in enumerate(cases):
            request = ImageGenerationRequest(prompt="x", output_path=tmp_path / f"{i}.png", aspect_ratio=ar)
            await backend.generate(request)
            assert client.images.generate.call_args.kwargs["size"] == expected, f"aspect_ratio={ar} 应映射到 {expected}"

    async def test_size_fallback_unknown_aspect_ratio(self, backend_and_client, tmp_path: Path):
        """未识别比例回退到 '2K' keyword（方式 1），由模型按 prompt 自适应，
        避免传错宽高被 API 拒（4.x/5.x 方式 2 总像素须 ≥ 3_686_400）。"""
        backend, client = backend_and_client
        request = ImageGenerationRequest(prompt="x", output_path=tmp_path / "u.png", aspect_ratio="weird")
        await backend.generate(request)
        assert client.images.generate.call_args.kwargs["size"] == "2K"

    async def test_size_for_seedream_3_uses_1k_table(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
        """3.0-t2i 模型族单边像素 ∈ [512, 2048]，必须用 1K 表而非 2K 表。"""
        monkeypatch.delenv("ARK_API_KEY", raising=False)
        mock_client = _make_client_mock()
        with patch("lib.image_backends.ark.create_ark_client", return_value=mock_client):
            from lib.image_backends.ark import ArkImageBackend

            backend = ArkImageBackend(api_key="test-key", model="doubao-seedream-3-0-t2i-250415")

        request = ImageGenerationRequest(prompt="x", output_path=tmp_path / "v.png", aspect_ratio="9:16")
        await backend.generate(request)
        assert mock_client.images.generate.call_args.kwargs["size"] == "720x1280"

    async def test_size_fallback_unknown_aspect_ratio_seedream_3(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
        """3.0-t2i 未识别比例必须回退到 '1K' 而非 '2K'（单边像素上限 2048）。"""
        monkeypatch.delenv("ARK_API_KEY", raising=False)
        mock_client = _make_client_mock()
        with patch("lib.image_backends.ark.create_ark_client", return_value=mock_client):
            from lib.image_backends.ark import ArkImageBackend

            backend = ArkImageBackend(api_key="test-key", model="doubao-seedream-3-0-t2i-250415")

        request = ImageGenerationRequest(prompt="x", output_path=tmp_path / "u3.png", aspect_ratio="weird")
        await backend.generate(request)
        assert mock_client.images.generate.call_args.kwargs["size"] == "1K"

    async def test_explicit_image_size_overrides_aspect_ratio(self, backend_and_client, tmp_path: Path):
        """caller 显式传入 image_size（如 grid 路径的 '2K'）必须保留，不被 aspect_ratio 推导覆盖。"""
        backend, client = backend_and_client
        request = ImageGenerationRequest(
            prompt="x", output_path=tmp_path / "g.png", aspect_ratio="9:16", image_size="2K"
        )
        await backend.generate(request)
        assert client.images.generate.call_args.kwargs["size"] == "2K"

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

    async def test_empty_data_raises(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
        """Ark 返回空 data 数组时，应抛出清晰的 RuntimeError 而非 IndexError。"""
        monkeypatch.delenv("ARK_API_KEY", raising=False)
        client = MagicMock()
        client.images.generate.return_value = _FakeImagesResponse(data=[])

        with patch("lib.image_backends.ark.create_ark_client", return_value=client):
            from lib.image_backends.ark import ArkImageBackend

            backend = ArkImageBackend(api_key="test-key")
            output = tmp_path / "out.png"
            request = ImageGenerationRequest(prompt="a cat", output_path=output)

            with pytest.raises(RuntimeError, match="data 为空"):
                await backend.generate(request)

    async def test_t2i_url_fallback(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
        """网关只返回 url 时，应走 httpx 下载分支。"""
        monkeypatch.delenv("ARK_API_KEY", raising=False)
        client = MagicMock()
        client.images.generate.return_value = _FakeImagesResponse(
            data=[_FakeImageData(b64_json=None, url="https://gateway/img.png")]
        )
        downloaded = b"downloaded-from-gateway"

        with patch("lib.image_backends.ark.create_ark_client", return_value=client):
            from lib.image_backends.ark import ArkImageBackend

            backend = ArkImageBackend(api_key="test-key")
            output = tmp_path / "out.png"
            request = ImageGenerationRequest(prompt="a cat", output_path=output)

            with patch("lib.image_backends.base.httpx.AsyncClient") as MockHttpClient:
                mock_http = AsyncMock()
                MockHttpClient.return_value.__aenter__ = AsyncMock(return_value=mock_http)
                MockHttpClient.return_value.__aexit__ = AsyncMock(return_value=False)
                mock_resp = MagicMock()
                mock_resp.content = downloaded
                mock_resp.raise_for_status = MagicMock()
                mock_http.get = AsyncMock(return_value=mock_resp)

                result = await backend.generate(request)

            mock_http.get.assert_awaited_once_with("https://gateway/img.png", timeout=60)

        assert result.image_path == output
        assert output.read_bytes() == downloaded
