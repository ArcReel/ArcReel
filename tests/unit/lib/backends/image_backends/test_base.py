from dataclasses import fields
from pathlib import Path

import httpx
import pytest

from lib.backends.artifact_download_guard import (
    IMAGE_ARTIFACT_MAX_BYTES,
    ArtifactDestinationRejectedError,
    ArtifactTooLargeError,
)
from lib.backends.image_backends.base import (
    ImageCapability,
    ImageGenerationRequest,
    ImageGenerationResult,
    ReferenceImage,
    download_image_to_path,
)
from tests.http_capture import capture_http


def test_image_capability_is_str_enum():
    assert ImageCapability.TEXT_TO_IMAGE == "text_to_image"
    assert ImageCapability.IMAGE_TO_IMAGE == "image_to_image"


def test_reference_image_carries_only_the_path():
    ref = ReferenceImage(path="/tmp/test.png")
    assert ref.path == "/tmp/test.png"
    assert [f.name for f in fields(ref)] == ["path"]


def test_image_generation_request_defaults():
    req = ImageGenerationRequest(prompt="hello", output_path=Path("/tmp/out.png"))
    assert req.aspect_ratio == "9:16"
    assert req.image_size is None
    assert req.reference_images == []
    assert req.project_name is None
    assert req.seed is None


def test_image_generation_result():
    result = ImageGenerationResult(
        image_path=Path("/tmp/out.png"),
        provider="grok",
        model="grok-imagine-image",
    )
    assert result.image_uri is None
    assert result.seed is None
    assert result.usage_tokens is None


async def test_download_image_writes_the_response_body(tmp_path: Path):
    output = tmp_path / "out.png"
    with capture_http() as router:
        router.get("http://203.0.113.7/a.png").mock(return_value=httpx.Response(200, content=b"\x89PNG"))
        await download_image_to_path("http://203.0.113.7/a.png", output)
    assert output.read_bytes() == b"\x89PNG"


async def test_download_image_rejects_link_local_destination(tmp_path: Path):
    output = tmp_path / "out.png"
    with capture_http() as router:
        route = router.get("http://169.254.169.254/a.png").mock(return_value=httpx.Response(200))
        with pytest.raises(ArtifactDestinationRejectedError):
            await download_image_to_path("http://169.254.169.254/a.png", output)
    assert route.call_count == 0
    assert not output.exists()


async def test_download_image_aborts_when_declared_size_exceeds_the_image_limit(tmp_path: Path):
    output = tmp_path / "out.png"
    oversized = {"Content-Length": str(IMAGE_ARTIFACT_MAX_BYTES + 1)}
    with capture_http() as router:
        router.get("http://203.0.113.7/a.png").mock(return_value=httpx.Response(200, headers=oversized, content=b""))
        with pytest.raises(ArtifactTooLargeError):
            await download_image_to_path("http://203.0.113.7/a.png", output)
    assert not output.exists()
    assert not (tmp_path / f"{output.name}.part").exists()
