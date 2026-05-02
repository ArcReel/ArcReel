"""MediaGenerator 在调用 image backend 前 gating；不匹配抛 ImageCapabilityError。"""

from unittest.mock import AsyncMock, MagicMock

import pytest

from lib.image_backends.base import ImageCapability, ImageCapabilityError


def _make_backend(caps: set[ImageCapability]) -> MagicMock:
    backend = MagicMock()
    backend.name = "fake"
    backend.model = "fake-1"
    backend.capabilities = caps
    backend.generate = AsyncMock()
    return backend


@pytest.mark.asyncio
async def test_t2i_call_with_i2i_only_backend_raises(tmp_path):
    from lib.media_generator import MediaGenerator

    backend = _make_backend({ImageCapability.IMAGE_TO_IMAGE})
    g = MediaGenerator(
        project_path=tmp_path,
        image_backend=backend,
    )
    with pytest.raises(ImageCapabilityError) as excinfo:
        await g.generate_image_async(
            prompt="x",
            resource_type="characters",
            resource_id="A",
            reference_images=None,
        )
    assert excinfo.value.code == "image_capability_missing_t2i"
    assert excinfo.value.params == {"provider": "fake", "model": "fake-1"}
    backend.generate.assert_not_called()


@pytest.mark.asyncio
async def test_i2i_call_with_t2i_only_backend_raises(tmp_path):
    from lib.media_generator import MediaGenerator

    backend = _make_backend({ImageCapability.TEXT_TO_IMAGE})
    g = MediaGenerator(
        project_path=tmp_path,
        image_backend=backend,
    )
    ref = tmp_path / "ref.png"
    ref.write_bytes(b"\x89PNG")
    with pytest.raises(ImageCapabilityError) as excinfo:
        await g.generate_image_async(
            prompt="x",
            resource_type="characters",
            resource_id="A",
            reference_images=[str(ref)],
        )
    assert excinfo.value.code == "image_capability_missing_i2i"
    assert excinfo.value.params == {"provider": "fake", "model": "fake-1"}
    backend.generate.assert_not_called()
