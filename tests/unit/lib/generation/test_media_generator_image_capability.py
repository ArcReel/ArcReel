"""MediaGenerator gates before image backend calls; mismatches raise ImageCapabilityError."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from lib.backends.image_backends.base import ImageCapability, ImageCapabilityError


def _make_backend(caps: set[ImageCapability]) -> MagicMock:
    backend = MagicMock()
    backend.name = "fake"
    backend.model = "fake-1"
    backend.capabilities = caps
    backend.reference_image_limit = AsyncMock(return_value=None)
    backend.generate = AsyncMock()
    return backend


@pytest.mark.asyncio
async def test_t2i_call_with_i2i_only_backend_raises(tmp_path):
    from lib.generation.media_generator import MediaGenerator

    backend = _make_backend({ImageCapability.IMAGE_TO_IMAGE})
    g = MediaGenerator(
        project_path=tmp_path,
        image_backend=backend,
        image_provider_id="fake",
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
    from lib.generation.media_generator import MediaGenerator

    backend = _make_backend({ImageCapability.TEXT_TO_IMAGE})
    g = MediaGenerator(
        project_path=tmp_path,
        image_backend=backend,
        image_provider_id="fake",
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


@pytest.mark.asyncio
async def test_t2i_call_rejected_when_entry_only_accepts_i2i(tmp_path):
    """Channels implement both, but vidu/viduq1 entries accept image-to-image only; execution/task buckets agree and reject text-to-image."""
    from lib.backends.image_backends.base import BOTH_IMAGE_CAPABILITIES
    from lib.generation.media_generator import MediaGenerator

    backend = _make_backend(set(BOTH_IMAGE_CAPABILITIES))
    backend.model = "viduq1"
    g = MediaGenerator(project_path=tmp_path, image_backend=backend, image_provider_id="vidu")
    with pytest.raises(ImageCapabilityError) as excinfo:
        await g.generate_image_async(prompt="x", resource_type="characters", resource_id="A", reference_images=None)
    assert excinfo.value.code == "image_capability_missing_t2i"
    backend.generate.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("entry_limit", "refs", "code"), [(0, 1, "image_capability_missing_i2i"), (2, 3, "image_reference_images_exceeded")]
)
async def test_reference_images_beyond_the_effective_limit_are_rejected_before_submission(
    tmp_path, monkeypatch, entry_limit: int, refs: int, code: str
):
    """Execution and orchestration read identical effective limits: entry 0 rejects image-to-image; positive overflow never silently truncates in backends."""
    from lib.backends.image_backends.base import BOTH_IMAGE_CAPABILITIES
    from lib.catalog import query
    from lib.generation.media_generator import MediaGenerator
    from tests.catalog_support import install_catalog, with_entry_facts

    install_catalog(
        monkeypatch,
        with_entry_facts(query.factory_snapshot(), "grok", "grok-imagine-image", max_reference_images=entry_limit),
    )
    backend = _make_backend(set(BOTH_IMAGE_CAPABILITIES))
    backend.model = "grok-imagine-image"
    backend.reference_image_limit = AsyncMock(return_value=5)
    paths = []
    for index in range(refs):
        ref = tmp_path / f"ref{index}.png"
        ref.write_bytes(b"\x89PNG")
        paths.append(str(ref))
    g = MediaGenerator(project_path=tmp_path, image_backend=backend, image_provider_id="grok")
    with pytest.raises(ImageCapabilityError) as excinfo:
        await g.generate_image_async(prompt="x", resource_type="characters", resource_id="A", reference_images=paths)
    assert excinfo.value.code == code
    backend.generate.assert_not_called()
