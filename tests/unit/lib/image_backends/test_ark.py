"""ArkImageBackend 单元测试。"""

from __future__ import annotations

import base64
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from unittest.mock import patch

import httpx
import pytest

from lib.image_backends.base import (
    ImageCapability,
    ImageGenerationRequest,
    ImageGenerationResult,
    ReferenceImage,
)
from lib.providers import PROVIDER_ARK
from tests.http_capture import capture_http, only_request

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


class _RecordingImages:
    def __init__(self, response: _FakeImagesResponse) -> None:
        self.requests: list[dict[str, Any]] = []
        self.response = response

    def generate(self, **kwargs: Any) -> _FakeImagesResponse:
        self.requests.append(kwargs)
        return self.response


class _RecordingArkClient:
    """Ark SDK 客户端替身：记录 images.generate 的请求参数，回固定响应。

    尺寸映射、参考图编码这些契约都是「发出去的请求长什么样」，断言落在 ``requests``
    里的请求内容上，而不是替身的调用对象。
    """

    def __init__(self, response: _FakeImagesResponse | None = None) -> None:
        self.images = _RecordingImages(response or _FakeImagesResponse(data=[_FakeImageData()]))

    @property
    def requests(self) -> list[dict[str, Any]]:
        return self.images.requests


@contextmanager
def _recorded_ark_client(
    response: _FakeImagesResponse | None = None,
) -> Iterator[tuple[list[dict[str, Any]], _RecordingArkClient]]:
    """create_ark_client 的记录器：收下建客户端的参数，回一个记录型客户端。"""
    created: list[dict[str, Any]] = []
    client = _RecordingArkClient(response)

    def _create(**kwargs: Any) -> _RecordingArkClient:
        created.append(kwargs)
        return client

    with patch("lib.image_backends.ark.create_ark_client", _create):
        yield created, client


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
        with _recorded_ark_client() as (created, _client):
            from lib.image_backends.ark import ArkImageBackend

            ArkImageBackend(api_key="my-key")
        assert created == [{"api_key": "my-key", "base_url": None}]

    def test_custom_base_url_passed_through(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.delenv("ARK_API_KEY", raising=False)
        with _recorded_ark_client() as (created, _client):
            from lib.image_backends.ark import ArkImageBackend

            ArkImageBackend(api_key="k", base_url="https://ark.cn-beijing.volces.com/api/plan/v3")
        assert created == [{"api_key": "k", "base_url": "https://ark.cn-beijing.volces.com/api/plan/v3"}]


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
        with _recorded_ark_client() as (_created, client):
            from lib.image_backends.ark import ArkImageBackend

            backend = ArkImageBackend(api_key="test-key")
        return backend, client

    async def test_t2i_generate(self, backend_and_client, tmp_path: Path):
        backend, client = backend_and_client
        output = tmp_path / "out.png"
        request = ImageGenerationRequest(prompt="a cat", output_path=output)

        result = await backend.generate(request)

        # SDK called correctly
        call_kwargs = client.requests[-1]
        assert call_kwargs["model"] == "doubao-seedream-5-0-lite-260128"
        assert call_kwargs["prompt"] == "a cat"
        # 部分兼容网关即便吃 response_format 仍返回 url，所以请求端不再传该参数
        assert "response_format" not in call_kwargs
        assert "image" not in call_kwargs

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

        call_kwargs = client.requests[-1]
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
            assert client.requests[-1]["size"] == expected, f"aspect_ratio={ar} 应映射到 {expected}"

    async def test_size_fallback_unknown_aspect_ratio(self, backend_and_client, tmp_path: Path):
        """未识别比例回退到 '2K' keyword（方式 1），由模型按 prompt 自适应，
        避免传错宽高被 API 拒（4.x/5.x 方式 2 总像素须 ≥ 3_686_400）。"""
        backend, client = backend_and_client
        request = ImageGenerationRequest(prompt="x", output_path=tmp_path / "u.png", aspect_ratio="weird")
        await backend.generate(request)
        assert client.requests[-1]["size"] == "2K"

    async def test_size_for_seedream_3_uses_1k_table(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
        """3.0-t2i 模型族单边像素 ∈ [512, 2048]，必须用 1K 表而非 2K 表。"""
        monkeypatch.delenv("ARK_API_KEY", raising=False)
        with _recorded_ark_client() as (_created, client):
            from lib.image_backends.ark import ArkImageBackend

            backend = ArkImageBackend(api_key="test-key", model="doubao-seedream-3-0-t2i-250415")

        request = ImageGenerationRequest(prompt="x", output_path=tmp_path / "v.png", aspect_ratio="9:16")
        await backend.generate(request)
        assert client.requests[-1]["size"] == "720x1280"

    async def test_size_fallback_unknown_aspect_ratio_seedream_3(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
        """3.0-t2i 未识别比例必须回退到 '1K' 而非 '2K'（单边像素上限 2048）。"""
        monkeypatch.delenv("ARK_API_KEY", raising=False)
        with _recorded_ark_client() as (_created, client):
            from lib.image_backends.ark import ArkImageBackend

            backend = ArkImageBackend(api_key="test-key", model="doubao-seedream-3-0-t2i-250415")

        request = ImageGenerationRequest(prompt="x", output_path=tmp_path / "u3.png", aspect_ratio="weird")
        await backend.generate(request)
        assert client.requests[-1]["size"] == "1K"

    async def test_explicit_image_size_overrides_aspect_ratio(self, backend_and_client, tmp_path: Path):
        """caller 显式传入 image_size（如 grid 路径的 '2K'）必须保留，不被 aspect_ratio 推导覆盖。"""
        backend, client = backend_and_client
        request = ImageGenerationRequest(
            prompt="x", output_path=tmp_path / "g.png", aspect_ratio="9:16", image_size="2K"
        )
        await backend.generate(request)
        assert client.requests[-1]["size"] == "2K"

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

        call_kwargs = client.requests[-1]
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

        call_kwargs = client.requests[-1]
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
        with _recorded_ark_client(_FakeImagesResponse(data=[])) as (_created, _client):
            from lib.image_backends.ark import ArkImageBackend

            backend = ArkImageBackend(api_key="test-key")
            output = tmp_path / "out.png"
            request = ImageGenerationRequest(prompt="a cat", output_path=output)

            with pytest.raises(RuntimeError, match="data 为空"):
                await backend.generate(request)

    async def test_t2i_url_fallback(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
        """网关只返回 url 时，应走 httpx 下载分支。"""
        monkeypatch.delenv("ARK_API_KEY", raising=False)
        response = _FakeImagesResponse(data=[_FakeImageData(b64_json=None, url="https://gateway/img.png")])
        downloaded = b"downloaded-from-gateway"

        with _recorded_ark_client(response) as (_created, _client):
            from lib.image_backends.ark import ArkImageBackend

            backend = ArkImageBackend(api_key="test-key")
            output = tmp_path / "out.png"
            request = ImageGenerationRequest(prompt="a cat", output_path=output)

            with capture_http() as router:
                download = router.get("https://gateway/img.png").mock(
                    return_value=httpx.Response(200, content=downloaded)
                )
                result = await backend.generate(request)

            assert only_request(download).url.host == "gateway"

        assert result.image_path == output
        assert output.read_bytes() == downloaded
