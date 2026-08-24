"""Runware image backend request-shape tests."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from lib.image_backends.base import ImageGenerationRequest, ReferenceImage
from lib.image_backends.runware import RunwareImageBackend

pytestmark = pytest.mark.unit


def _reference(tmp_path: Path, name: str) -> ReferenceImage:
    path = tmp_path / name
    path.write_bytes(b"image")
    return ReferenceImage(path=str(path), label=name)


async def test_generate_uploads_every_reference_in_order(tmp_path: Path) -> None:
    backend = RunwareImageBackend(api_key="rw-key", model="google:nano-banana@2-lite")
    backend._upload_reference_image = AsyncMock(side_effect=["uuid-storyboard", "uuid-character"])
    backend._submit = AsyncMock(return_value={"data": [{"imageURL": "https://example.test/out.png"}]})
    backend._persist_image = AsyncMock(return_value="https://example.test/out.png")
    references = [_reference(tmp_path, "storyboard.png"), _reference(tmp_path, "character.png")]

    await backend.generate(
        ImageGenerationRequest(
            prompt="Picture 1 is the Storyboard; Picture 2 is the character",
            output_path=tmp_path / "out.png",
            reference_images=references,
        )
    )

    assert backend._upload_reference_image.await_count == 2
    assert backend._submit.await_args.args[3] == ["uuid-storyboard", "uuid-character"]


async def test_submit_uses_runware_reference_images_contract() -> None:
    response = MagicMock()
    response.status_code = 200
    response.json.return_value = {"data": [{"imageURL": "https://example.test/out.png"}]}
    client = AsyncMock()
    client.post = AsyncMock(return_value=response)
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=None)

    with patch("lib.image_backends.runware.httpx.AsyncClient", return_value=client):
        backend = RunwareImageBackend(api_key="rw-key", model="google:nano-banana@2-lite")
        await backend._submit("prompt", 768, 1376, ["uuid-1", "uuid-2"], 42)

    body = client.post.await_args.kwargs["json"]
    assert body[0]["inputs"] == {"referenceImages": ["uuid-1", "uuid-2"]}
    assert "seedImage" not in body[0]
    assert "strength" not in body[0]
