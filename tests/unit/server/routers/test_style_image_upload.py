import asyncio
from io import BytesIO
from types import SimpleNamespace

import pytest
from fastapi import HTTPException, UploadFile
from PIL import Image

from lib.i18n import _
from lib.project.project_manager import get_project_manager
from server.routers.files import upload_style_image
from tests.fakes import use_text_generator


def _png(color: str) -> bytes:
    buffer = BytesIO()
    Image.new("RGB", (2, 2), color).save(buffer, format="PNG")
    return buffer.getvalue()


@pytest.mark.parametrize("older_finishes_first", [False, True])
@pytest.mark.asyncio
async def test_superseded_style_analysis_cannot_replace_newer_upload(monkeypatch, older_finishes_first):
    manager = get_project_manager()
    directory = manager.create_project("demo")
    old, new = _png("red"), _png("blue")
    started = [asyncio.Event(), asyncio.Event()]
    resume = [asyncio.Event(), asyncio.Event()]
    inputs = {}

    async def generate(request, *, project_name):
        index = int(started[0].is_set())
        started[index].set()
        await resume[index].wait()
        inputs[index] = await asyncio.to_thread(request.images[0].path.read_bytes)
        return SimpleNamespace(text=("red", "blue")[index])

    async def create(*args, **kwargs):
        return SimpleNamespace(generate=generate)

    use_text_generator(monkeypatch, create)

    def upload(content):
        return upload_style_image("demo", _, UploadFile(BytesIO(content), filename="style.png"))

    first = asyncio.create_task(upload(old))
    await asyncio.wait_for(started[0].wait(), timeout=10)
    second = asyncio.create_task(upload(new))
    try:
        await asyncio.wait_for(started[1].wait(), timeout=10)
        if older_finishes_first:
            resume[0].set()
            await asyncio.gather(first, return_exceptions=True)
            assert "style_image" not in manager.load_project("demo")
        else:
            resume[1].set()
            await second
    finally:
        for event in resume:
            event.set()
        outcome, latest = await asyncio.gather(first, second, return_exceptions=True)

    assert isinstance(latest, dict)
    assert latest["style_description"] == "blue"
    saved = manager.load_project("demo")
    assert (directory / saved["style_image"]).read_bytes() == new
    assert saved["style_description"] == "blue"
    assert inputs == {0: old, 1: new}
    assert isinstance(outcome, HTTPException)
    assert outcome.status_code == 409
