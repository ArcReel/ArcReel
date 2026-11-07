"""ArkImageBackend unit tests."""

from __future__ import annotations

import base64
from collections.abc import Generator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from unittest.mock import patch

import httpx
import pytest

from lib.backends.image_backends.base import (
    ImageCapability,
    ImageGenerationRequest,
    ImageGenerationResult,
    ImageRequestFacts,
    ReferenceImage,
)
from lib.backends.providers import PROVIDER_ARK
from tests.http_capture import capture_http, only_request, request_json

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
    """Ark SDK substitute captures images.generate arguments and returns fixed responses.

    Size/reference encoding contracts concern actual requests; assert ``requests`` contents,
    never substitute callable objects.
    """

    def __init__(self, response: _FakeImagesResponse | None = None) -> None:
        self.images = _RecordingImages(response or _FakeImagesResponse(data=[_FakeImageData()]))

    @property
    def requests(self) -> list[dict[str, Any]]:
        return self.images.requests


@contextmanager
def _recorded_ark_client(
    response: _FakeImagesResponse | None = None,
) -> Generator[tuple[list[dict[str, Any]], _RecordingArkClient]]:
    """create_ark_client recorder captures constructor arguments and returns recording clients."""
    created: list[dict[str, Any]] = []
    client = _RecordingArkClient(response)

    def _create(**kwargs: Any) -> _RecordingArkClient:
        created.append(kwargs)
        return client

    with patch("lib.backends.image_backends.ark.create_ark_client", _create):
        yield created, client


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestArkImageBackendInit:
    """Constructor tests."""

    def test_missing_api_key_raises(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.delenv("ARK_API_KEY", raising=False)
        from lib.backends.image_backends.ark import ArkImageBackend

        with pytest.raises(ValueError, match="Ark API Key"):
            ArkImageBackend(api_key=None)

    def test_api_key_from_env_no_longer_supported(self, monkeypatch: pytest.MonkeyPatch):
        """Spec §5.4 removes environment fallback: missing api_key raises even with ARK_API_KEY set."""
        monkeypatch.setenv("ARK_API_KEY", "env-key")
        from lib.backends.image_backends.ark import ArkImageBackend

        with pytest.raises(ValueError, match="Ark API Key"):
            ArkImageBackend(api_key=None)

    def test_api_key_from_param(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.delenv("ARK_API_KEY", raising=False)
        with _recorded_ark_client() as (created, _client):
            from lib.backends.image_backends.ark import ArkImageBackend

            ArkImageBackend(api_key="my-key")
        assert created == [{"api_key": "my-key", "base_url": None, "transport_layer": None}]

    def test_custom_base_url_passed_through(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.delenv("ARK_API_KEY", raising=False)
        with _recorded_ark_client() as (created, _client):
            from lib.backends.image_backends.ark import ArkImageBackend

            ArkImageBackend(api_key="k", base_url="https://ark.cn-beijing.volces.com/api/plan/v3")
        assert created == [
            {"api_key": "k", "base_url": "https://ark.cn-beijing.volces.com/api/plan/v3", "transport_layer": None}
        ]


class TestArkImageBackendProperties:
    """Property tests."""

    @pytest.fixture
    def backend(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.delenv("ARK_API_KEY", raising=False)
        with patch("lib.backends.image_backends.ark.create_ark_client"):
            from lib.backends.image_backends.ark import ArkImageBackend

            return ArkImageBackend(api_key="test-key")

    def test_name(self, backend):
        assert backend.name == PROVIDER_ARK

    def test_default_model(self, backend):
        assert backend.model == "doubao-seedream-5-0-pro-260628"

    def test_custom_model(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.delenv("ARK_API_KEY", raising=False)
        with patch("lib.backends.image_backends.ark.create_ark_client"):
            from lib.backends.image_backends.ark import ArkImageBackend

            b = ArkImageBackend(api_key="k", model="custom-model")
            assert b.model == "custom-model"

    def test_capabilities(self, backend):
        assert backend.capabilities == {
            ImageCapability.TEXT_TO_IMAGE,
            ImageCapability.IMAGE_TO_IMAGE,
        }


class TestArkImageBackendReferenceLimitIsAModelFact:
    """Seedream generation-specific reference caps are catalog facts; channels neither infer names nor truncate, and meter sent counts."""

    @pytest.mark.parametrize("model", ["doubao-seedream-5-0-pro-260628", "doubao-seedream-4-5-251128"])
    async def test_channel_sends_every_reference_whatever_the_model_name(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, model: str
    ):
        from lib.backends.ark_shared import ARK_BASE_URL
        from lib.backends.image_backends.ark import ArkImageBackend

        monkeypatch.delenv("ARK_API_KEY", raising=False)
        backend = ArkImageBackend(api_key="k", model=model)
        refs = []
        for index in range(16):
            ref_file = tmp_path / f"ref{index}.png"
            ref_file.write_bytes(f"img-{index}".encode())
            refs.append(ReferenceImage(path=str(ref_file)))

        with capture_http() as router:
            route = router.post(f"{ARK_BASE_URL}/images/generations").mock(
                return_value=httpx.Response(200, json={"model": model, "created": 1, "data": [{"b64_json": FAKE_B64}]})
            )
            result = await backend.generate(
                ImageGenerationRequest(prompt="merge", output_path=tmp_path / "out.png", reference_images=refs)
            )

        assert await backend.reference_image_limit() is None
        assert len(request_json(only_request(route))["image"]) == 16
        assert result.usage.quantities["input_images"] == 16

    def test_estimate_reports_the_references_that_will_be_sent(self):
        from lib.backends.image_backends.ark import ArkImageBackend

        facts = ImageRequestFacts(model="doubao-seedream-5-0-pro-260628", aspect_ratio="16:9", reference_count=12)
        assert ArkImageBackend.estimate_usage(facts).quantities["input_images"] == 12


class TestArkImageBackendGenerate:
    """generate() tests."""

    @pytest.fixture
    def backend_and_client(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.delenv("ARK_API_KEY", raising=False)
        with _recorded_ark_client() as (_created, client):
            from lib.backends.image_backends.ark import ArkImageBackend

            backend = ArkImageBackend(api_key="test-key")
        return backend, client

    async def test_t2i_generate(self, backend_and_client, tmp_path: Path):
        backend, client = backend_and_client
        output = tmp_path / "out.png"
        request = ImageGenerationRequest(prompt="a cat", output_path=output)

        result = await backend.generate(request)

        # SDK called correctly
        call_kwargs = client.requests[-1]
        assert call_kwargs["model"] == "doubao-seedream-5-0-pro-260628"
        assert call_kwargs["prompt"] == "a cat"
        # Compatible gateways may accept response_format but still return URLs; omit that request parameter.
        assert "response_format" not in call_kwargs
        assert "image" not in call_kwargs

        # Result
        assert isinstance(result, ImageGenerationResult)
        assert result.provider == PROVIDER_ARK
        assert result.image_path == output
        assert output.exists()
        assert output.read_bytes() == base64.b64decode(FAKE_B64)
        # Report generated counts and actual request size resolution.
        assert result.usage is not None
        assert result.usage.quantities == {"output_images": 1, "input_images": 0}
        assert result.usage.dimensions == {"resolution": call_kwargs["size"]}

    async def test_t2i_with_seed(self, backend_and_client, tmp_path: Path):
        backend, client = backend_and_client
        output = tmp_path / "out.png"
        request = ImageGenerationRequest(prompt="a dog", output_path=output, seed=42)

        await backend.generate(request)

        call_kwargs = client.requests[-1]
        assert call_kwargs["seed"] == 42

    async def test_size_from_aspect_ratio(self, backend_and_client, tmp_path: Path):
        """Map aspect_ratio into explicit SDK sizes, or Seedream defaults 2048x2048 and ignores project ratios.
        Use Ark’s official recommended 2K pixel table for 4.x/5.x."""
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
        """Unknown ratios fall back to adaptive prompt-driven 2K keywords (method 1), avoiding API rejection
        for invalid dimensions (4.x/5.x method 2 requires total pixels ≥ 3_686_400)."""
        backend, client = backend_and_client
        request = ImageGenerationRequest(prompt="x", output_path=tmp_path / "u.png", aspect_ratio="weird")
        await backend.generate(request)
        assert client.requests[-1]["size"] == "2K"

    async def test_size_for_seedream_3_uses_1k_table(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
        """3.0-t2i edges require [512, 2048]; use 1K tables, never 2K."""
        monkeypatch.delenv("ARK_API_KEY", raising=False)
        with _recorded_ark_client() as (_created, client):
            from lib.backends.image_backends.ark import ArkSeedream3ImageBackend

            backend = ArkSeedream3ImageBackend(api_key="test-key", model="doubao-seedream-3-0-t2i-250415")

        request = ImageGenerationRequest(prompt="x", output_path=tmp_path / "v.png", aspect_ratio="9:16")
        await backend.generate(request)
        assert client.requests[-1]["size"] == "720x1280"

    async def test_size_fallback_unknown_aspect_ratio_seedream_3(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
        """3.0-t2i unknown ratios fall back to 1K, never 2K, respecting edge cap 2048."""
        monkeypatch.delenv("ARK_API_KEY", raising=False)
        with _recorded_ark_client() as (_created, client):
            from lib.backends.image_backends.ark import ArkSeedream3ImageBackend

            backend = ArkSeedream3ImageBackend(api_key="test-key", model="doubao-seedream-3-0-t2i-250415")

        request = ImageGenerationRequest(prompt="x", output_path=tmp_path / "u3.png", aspect_ratio="weird")
        await backend.generate(request)
        assert client.requests[-1]["size"] == "1K"

    async def test_explicit_image_size_overrides_aspect_ratio(self, backend_and_client, tmp_path: Path):
        """Preserve explicit image_size, e.g. grid 2K, rather than overriding from aspect_ratio."""
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
        """Empty Ark data arrays raise clear RuntimeError, not IndexError."""
        monkeypatch.delenv("ARK_API_KEY", raising=False)
        with _recorded_ark_client(_FakeImagesResponse(data=[])) as (_created, _client):
            from lib.backends.image_backends.ark import ArkImageBackend

            backend = ArkImageBackend(api_key="test-key")
            output = tmp_path / "out.png"
            request = ImageGenerationRequest(prompt="a cat", output_path=output)

            with pytest.raises(RuntimeError, match="data 为空"):
                await backend.generate(request)

    async def test_t2i_url_fallback(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
        """URL-only gateway results use httpx download branches."""
        monkeypatch.delenv("ARK_API_KEY", raising=False)
        response = _FakeImagesResponse(data=[_FakeImageData(b64_json=None, url="https://gateway/img.png")])
        downloaded = b"downloaded-from-gateway"

        with _recorded_ark_client(response) as (_created, _client):
            from lib.backends.image_backends.ark import ArkImageBackend

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
