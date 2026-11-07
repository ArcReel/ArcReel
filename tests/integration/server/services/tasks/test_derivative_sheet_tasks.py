"""Character-derivative generation: outgoing request shape, admission refusal, artifact coordinates and currency.

Assertions inspect the actual outgoing request intercepted by respx at transport: an image edit **using the base asset image as input**, with guards and negative restrictions rendered through the asset-image template."""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest

from lib.artifacts.artifact_activation import active_artifact_currency_resolver
from lib.artifacts.artifact_manifest import ArtifactStatus, ProjectArtifactManifestAdapter
from lib.backends.image_backends.dashscope import DashScopeImageBackend
from lib.generation.generation_queue import DispatchProviderChanged
from lib.infra.api_errors import BadRequestError
from lib.project.asset_derivatives import derivative_artifact_key, derivative_sheet_relative_path
from lib.project.resource_paths import CHARACTER_DERIVATIVE_RESOURCE_TYPE
from server.services.tasks.derivative_sheet_tasks import execute_character_derivative_task
from server.services.tasks.image_edit_tasks import execute_image_edit_task
from tests.fakes import use_project_manager
from tests.http_capture import capture_http, only_request, request_json
from tests.integration.server.derivative_sheet_support import (
    GENERATION_URL,
    OWNER_LEFT_RGB,
    OWNER_RIGHT_RGB,
    OWNER_SHEET_SIZE,
    RESULT_IMAGE_RGB,
    RESULT_URL,
    build_generator,
    close_to,
    decode_data_url_image,
    read_project_json,
    seed_derivative_project,
    solid_png_bytes,
    wire_derivative_generation,
    write_two_tone_png,
)

_SHEET_PATH = derivative_sheet_relative_path("阿岚", "战斗装")
_ARTIFACT_KEY = derivative_artifact_key("阿岚", "战斗装")


def _wire(monkeypatch, pm, generator) -> None:
    """Connect task project-manager/context to this case's own real project and generator."""
    wire_derivative_generation(monkeypatch, pm, generator)


def _backend() -> DashScopeImageBackend:
    return DashScopeImageBackend(api_key="sk", model="qwen-image-2.0")


def _routes(router, *, result_bytes: bytes):
    submit = router.post(GENERATION_URL).mock(
        return_value=httpx.Response(
            200, json={"output": {"choices": [{"message": {"content": [{"image": RESULT_URL}]}}]}}
        )
    )
    router.get(RESULT_URL).mock(return_value=httpx.Response(200, content=result_bytes))
    return submit


def _sent_images(submit) -> list[str]:
    content = request_json(only_request(submit))["input"]["messages"][0]["content"]
    return [item["image"] for item in content if "image" in item]


class TestDerivativeSheetGeneration:
    async def test_outbound_request_edits_the_ontology_sheet(self, tmp_path, monkeypatch):
        pm, project_path = seed_derivative_project(tmp_path)
        generator = build_generator(project_path, _backend())
        _wire(monkeypatch, pm, generator)

        with capture_http() as router:
            submit = _routes(router, result_bytes=solid_png_bytes(RESULT_IMAGE_RGB))
            await execute_character_derivative_task("demo", "阿岚/战斗装", {}, task_id="task-1")

        content = request_json(only_request(submit))["input"]["messages"][0]["content"]
        prompt = next(item["text"] for item in content if "text" in item)
        assert "Keep the original three-view layout" in prompt
        assert prompt.endswith("Avoid: watermarks, extra text, logos")
        assert "Style:" not in prompt
        images = _sent_images(submit)
        assert len(images) == 1
        sent = decode_data_url_image(images[0])
        # Outgoing reference is the base asset image itself: dimensions and both color halves match within lossy-compression tolerance.
        assert sent.size == OWNER_SHEET_SIZE
        assert close_to(sent.convert("RGB").getpixel((8, 8)), OWNER_LEFT_RGB)
        assert close_to(sent.convert("RGB").getpixel((88, 40)), OWNER_RIGHT_RGB)

    async def test_provider_changed_since_claim_is_raised_before_any_paid_call(self, tmp_path, monkeypatch):
        """If resolved i2i provider differs from the claimed slot, send no request; worker updates the record and requeues."""
        pm, project_path = seed_derivative_project(tmp_path)
        generator = build_generator(project_path, _backend())
        _wire(monkeypatch, pm, generator)

        with capture_http() as router:
            submit = _routes(router, result_bytes=solid_png_bytes(RESULT_IMAGE_RGB))
            with pytest.raises(DispatchProviderChanged) as caught:
                await execute_character_derivative_task(
                    "demo", "阿岚/战斗装", {}, task_id="task-1", claimed_provider_id="dashscope"
                )

        assert (caught.value.claimed_provider_id, caught.value.actual_provider_id) == ("dashscope", "openai")
        assert not submit.called

    async def test_result_lands_on_the_derivative_coordinates(self, tmp_path, monkeypatch):
        pm, project_path = seed_derivative_project(tmp_path)
        generator = build_generator(project_path, _backend())
        _wire(monkeypatch, pm, generator)
        result_bytes = solid_png_bytes(RESULT_IMAGE_RGB)

        with capture_http() as router:
            _routes(router, result_bytes=result_bytes)
            result = await execute_character_derivative_task("demo", "阿岚/战斗装", {}, task_id="task-1")

        assert result["file_path"] == _SHEET_PATH
        assert result["resource_type"] == CHARACTER_DERIVATIVE_RESOURCE_TYPE
        assert result["resource_id"] == "阿岚/战斗装"
        assert _SHEET_PATH == "characters/derivatives/阿岚/战斗装.png"
        assert (project_path / _SHEET_PATH).read_bytes() == result_bytes

        derivative = read_project_json(project_path)["characters"]["阿岚"]["derivatives"]["战斗装"]
        assert derivative["character_sheet"] == _SHEET_PATH

        entry = ProjectArtifactManifestAdapter(project_path).get_entry(_ARTIFACT_KEY)
        assert entry is not None
        assert entry.artifact_path == _SHEET_PATH

        versions = generator.versions.get_versions(CHARACTER_DERIVATIVE_RESOURCE_TYPE, "阿岚/战斗装")
        assert versions["current_version"] == 1
        assert len(versions["versions"]) == 1
        snapshot = Path(versions["versions"][0]["file"])
        assert snapshot.parts[:4] == ("versions", "characters", "derivatives", "阿岚")

    async def test_ontology_without_a_sheet_is_refused_before_any_paid_call(self, tmp_path, monkeypatch):
        pm, project_path = seed_derivative_project(tmp_path, with_owner_sheet=False)
        generator = build_generator(project_path, _backend())
        _wire(monkeypatch, pm, generator)

        with capture_http() as router:
            submit = _routes(router, result_bytes=solid_png_bytes(RESULT_IMAGE_RGB))
            with pytest.raises(BadRequestError) as excinfo:
                await execute_character_derivative_task("demo", "阿岚/战斗装", {}, task_id="task-1")

        assert excinfo.value.key == "derivative_owner_sheet_missing"
        assert submit.call_count == 0
        assert not (project_path / _SHEET_PATH).exists()


class TestDerivativeSheetCurrency:
    """Derivative currency: changing base images makes derivatives stale; regenerating derivatives makes them current."""

    def _status(self, pm, project_path) -> ArtifactStatus:
        project = pm.load_project("demo")
        resolver = active_artifact_currency_resolver(project_path, project)
        return resolver.compare(_ARTIFACT_KEY, artifact_path=_SHEET_PATH).status

    async def _generate(self, pm, project_path, monkeypatch) -> None:
        generator = build_generator(project_path, _backend())
        _wire(monkeypatch, pm, generator)
        with capture_http() as router:
            _routes(router, result_bytes=solid_png_bytes(RESULT_IMAGE_RGB))
            await execute_character_derivative_task("demo", "阿岚/战斗装", {}, task_id="task-1")

    async def test_regenerating_the_ontology_sheet_stales_its_derivative(self, tmp_path, monkeypatch):
        pm, project_path = seed_derivative_project(tmp_path)
        await self._generate(pm, project_path, monkeypatch)
        assert self._status(pm, project_path) is ArtifactStatus.CURRENT

        # Replacing base-image content invalidates the derivative, which is an edit of that image.
        write_two_tone_png(project_path / "characters/阿岚.png", left=(10, 10, 10), right=(240, 240, 240))
        assert self._status(pm, project_path) is ArtifactStatus.STALE

        await self._generate(pm, project_path, monkeypatch)
        assert self._status(pm, project_path) is ArtifactStatus.CURRENT

    async def test_changing_the_derivative_description_stales_it(self, tmp_path, monkeypatch):
        pm, project_path = seed_derivative_project(tmp_path)
        await self._generate(pm, project_path, monkeypatch)

        def _retouch(entry):
            entry["derivatives"]["战斗装"]["description"] = "换上银色轻甲"

        pm.update_asset_entry("character", "demo", "阿岚", _retouch)
        assert self._status(pm, project_path) is ArtifactStatus.STALE


class TestDerivativeImageEdit:
    """Instructional derivative editing uses its own current image and persists at derivative coordinates."""

    async def test_edit_consumes_the_derivative_current_image_and_bumps_its_version(self, tmp_path, monkeypatch):
        pm, project_path = seed_derivative_project(tmp_path)
        generator = build_generator(project_path, _backend())
        _wire(monkeypatch, pm, generator)
        use_project_manager(monkeypatch, pm)

        generated = solid_png_bytes(RESULT_IMAGE_RGB)
        with capture_http() as router:
            _routes(router, result_bytes=generated)
            await execute_character_derivative_task("demo", "阿岚/战斗装", {}, task_id="task-1")

        edited = solid_png_bytes((240, 120, 20))
        with capture_http() as router:
            submit = _routes(router, result_bytes=edited)
            result = await execute_image_edit_task(
                "demo",
                "阿岚/战斗装",
                {"resource_type": "character_derivative", "prompt": "把披风改成红色"},
                task_id="task-2",
            )

        images = _sent_images(submit)
        assert len(images) == 1
        # Editing uses the derivative's own current solid-color image, not the base's two-color asset sheet.
        sent = decode_data_url_image(images[0]).convert("RGB")
        assert close_to(sent.getpixel((4, 4)), RESULT_IMAGE_RGB)
        assert close_to(sent.getpixel((28, 28)), RESULT_IMAGE_RGB)

        assert result["file_path"] == _SHEET_PATH
        assert result["version"] == 2
        assert (project_path / _SHEET_PATH).read_bytes() == edited
