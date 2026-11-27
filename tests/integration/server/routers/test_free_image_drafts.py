from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from PIL import Image

from lib.artifacts.version_manager import VersionManager
from lib.backends.image_backends.base import ImageCapability, ImageGenerationResult
from lib.generation.media_generator import MediaGenerator
from lib.project.asset_types import ASSET_SPECS
from lib.project.project_manager import ProjectManager
from server.auth import CurrentUserInfo, get_current_user
from server.error_handlers import register_error_handlers
from server.routers import free_image_drafts as router
from server.services import free_image_drafts as drafts
from server.services.tasks import generation_tasks


@pytest.fixture
def project(tmp_path, monkeypatch):
    pm = ProjectManager(tmp_path / "data")
    pm.create_project("demo")
    pm.create_project_metadata("demo", "Demo", "", "narration")
    pm.add_project_character("demo", "Alice", "Detective")
    pm.add_project_scene("demo", "Harbor", "Harbor")
    pm.add_prop("demo", "Lantern", "Lantern")
    for module in (router, drafts, generation_tasks):
        monkeypatch.setattr(module, "get_project_manager", lambda: pm)
    monkeypatch.setenv("ARCREEL_DATA_DIR", str(pm.data_root))
    return pm


def client():
    app = FastAPI()
    register_error_handlers(app)
    app.dependency_overrides[get_current_user] = lambda: CurrentUserInfo(id="default", sub="test", role="admin")
    app.include_router(router.router)
    return TestClient(app)


def test_empty_prompt_rejected_and_local_request_queues_t2i(project, monkeypatch):
    enqueue = AsyncMock(return_value={"task_id": "task"})
    monkeypatch.setattr(router, "get_generation_queue", lambda: SimpleNamespace(enqueue_task=enqueue))
    with client() as http:
        url = "/projects/demo/image-drafts/generate"
        assert http.post(url, json={"prompt": "   "}).status_code == 400
        response = http.post(
            url,
            json={
                "prompt": "  Orange cat ",
                "aspect_ratio": "3:4",
                "generation_options": {"backend": "openai/model", "resolution": "1K"},
            },
        )
    assert response.status_code == 200
    queued = enqueue.call_args.kwargs
    assert queued["media_type"] == "image"
    assert queued["task_type"] == "free_image"
    assert queued["resource_id"] == response.json()["draft_id"]
    assert queued["payload"] == {
        "prompt": "Orange cat",
        "aspect_ratio": "3:4",
        "generation_options": {"backend": "openai/model", "resolution": "1K"},
    }


@pytest.mark.asyncio
async def test_worker_commits_generated_image_and_settings(project, monkeypatch):
    import asyncio

    project_dir = await asyncio.to_thread(project.get_project_path, "demo")
    draft_id = "a" * 32

    async def generate(request):
        assert request.prompt == "Orange cat"
        assert request.aspect_ratio == "3:4"
        assert request.image_size == "1K"
        assert request.reference_images == []
        await asyncio.to_thread(Image.new("RGB", (3, 4)).save, request.output_path)
        return ImageGenerationResult(request.output_path, "openai", "model")

    backend = SimpleNamespace(
        name="openai",
        model="model",
        capabilities={ImageCapability.TEXT_TO_IMAGE},
        generate=generate,
    )
    generator = MediaGenerator(project_dir, image_backend=backend, image_provider_id="openai")
    resolve = AsyncMock(
        return_value=SimpleNamespace(
            image=SimpleNamespace(
                provider_model=SimpleNamespace(provider_id="openai"), backend_model="model", resolution="1K"
            ),
            generator=generator,
        )
    )
    monkeypatch.setattr(generation_tasks, "resolve_generation_context", resolve)
    result = await generation_tasks.execute_generation_task(
        {
            "task_type": "free_image",
            "task_id": "test-free-image-task",
            "project_name": "demo",
            "resource_id": draft_id,
            "payload": {
                "prompt": "Orange cat",
                "aspect_ratio": "3:4",
                "generation_options": {"backend": "openai/model"},
            },
        },
        claimed_provider_id="openai",
    )
    assert resolve.call_args.kwargs["image"].generation_type == "t2i"
    assert await asyncio.to_thread(drafts.list_drafts, "demo") == [result]
    assert result["model"] == "model"
    assert result["resolution"] == "1K"


@pytest.mark.parametrize(("asset_type", "name"), [("character", "Alice"), ("scene", "Harbor"), ("prop", "Lantern")])
def test_binding_keeps_asset_versions_and_draft_deletion_keeps_binding(project, asset_type, name):
    directory = project.get_project_path("demo") / "image_drafts"
    directory.mkdir()
    base = "/projects/demo/image-drafts"
    with client() as http:
        for draft_id, color in [("a" * 32, "red"), ("b" * 32, "blue")]:
            staged = directory / "stage.png"
            output = directory / f"{draft_id}.png"
            Image.new("RGB", (4, 4), color).save(staged)
            drafts.commit_draft(
                "demo",
                staged,
                output,
                resource_id=draft_id,
                prompt=color,
                model="model",
                provider="openai",
                aspect_ratio="1:1",
                resolution=None,
            )
            image = http.get(f"{base}/{draft_id}/image")
            assert image.content == output.read_bytes()
            response = http.post(f"{base}/{draft_id}/bind", json={"asset_type": asset_type, "asset_name": name})
            assert response.status_code == 200, response.text
        assert http.delete(f"{base}/{draft_id}").status_code == 204
        assert http.get(f"{base}/{draft_id}/image").status_code == 404
        assert [item["id"] for item in http.get(base).json()["items"]] == ["a" * 32]
    project_dir = project.get_project_path("demo")
    spec = ASSET_SPECS[asset_type]
    sheet = f"{spec.subdir}/{name}.png"
    assert project.load_project("demo")[spec.bucket_key][name][spec.sheet_field] == sheet
    assert VersionManager(project_dir).get_current_version(spec.bucket_key, name) == 2
    with Image.open(project_dir / sheet) as image:
        assert image.getpixel((0, 0)) == (0, 0, 255)
