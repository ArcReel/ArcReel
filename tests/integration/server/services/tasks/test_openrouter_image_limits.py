"""OpenRouter 图片物理封顶（16 张）与远端动态上限端到端测试。

验证：
1. 服务级 16 张物理封顶在写入期拒绝超限覆盖、在读侧过滤存量超限覆盖，且设置页、预检与执行器口径一致；
2. 远端动态上限探测在数据库会话之外异步执行，且不重读本地模型目录；
3. 远端声明上限严格施加在用户覆盖之后，未声明或探测失败时保留既有回退规则。
"""

from __future__ import annotations

import base64
import io
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient
from PIL import Image

from lib.backends.image_errors import ImageCapabilityError
from lib.catalog import query as catalog
from lib.custom_provider import make_provider_id
from lib.db.repositories.custom_provider_repo import CustomProviderRepository
from lib.project.project_manager import ProjectManager
from server.services.admission.image_references import reference_image_limit
from server.services.tasks import generation_context
from server.services.tasks.generation_context import ImageLaneRequest, resolve_generation_context
from tests.catalog_support import install_catalog, with_entry_facts
from tests.fakes import bind_session_factory, use_project_manager
from tests.http_capture import capture_http, only_request, request_json

_OPENROUTER_BASE = "https://openrouter.test/api/v1"


def _png_bytes() -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (8, 8), "blue").save(buf, format="PNG")
    return buf.getvalue()


def _make_refs(tmp_path: Path, count: int) -> list[Path]:
    raw = _png_bytes()
    paths = []
    for idx in range(count):
        path = tmp_path / f"ref_{idx + 1}.png"
        path.write_bytes(raw)
        paths.append(path)
    return paths


def _init_project(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, provider_id: str, model_id: str
) -> tuple[Path, dict]:
    pm = ProjectManager(tmp_path)
    project_path = tmp_path / "demo"
    project_path.mkdir(parents=True, exist_ok=True)
    project = {
        "name": "demo",
        "aspect_ratio": "9:16",
        "image_provider_i2i": f"{provider_id}/{model_id}",
        "image_provider_t2i": f"{provider_id}/{model_id}",
    }
    (project_path / "project.json").write_text("{}", encoding="utf-8")
    use_project_manager(monkeypatch, pm)
    return project_path, project


async def _seed_openrouter_model(
    session_factory,
    *,
    model_id: str,
    overrides: dict[str, object] | None = None,
    entry_binding: str | None = None,
) -> str:
    async with session_factory() as session:
        repo = CustomProviderRepository(session)
        provider = await repo.create_provider(
            display_name="OpenRouter",
            discovery_format="openai",
            base_url=_OPENROUTER_BASE,
            api_key="sk-or",
            models=[
                {
                    "model_id": model_id,
                    "display_name": model_id,
                    "endpoint": "openrouter-images",
                    "is_default": True,
                    "is_enabled": True,
                    "capability_overrides": overrides,
                    "entry_binding": entry_binding,
                }
            ],
        )
        await session.commit()
        return make_provider_id(provider.id)


@pytest.fixture(autouse=True)
def _reset_cache():
    generation_context.reset_backend_cache_for_tests()
    yield
    generation_context.reset_backend_cache_for_tests()


async def test_openrouter_rejects_override_above_16_and_filters_stale_row(
    custom_providers_app, custom_providers_app_session_factory, tmp_path, monkeypatch
):
    with TestClient(custom_providers_app) as client:
        rejected = client.post(
            "/api/v1/custom-providers",
            json={
                "display_name": "OpenRouter",
                "discovery_format": "openai",
                "base_url": _OPENROUTER_BASE,
                "api_key": "sk-or",
                "models": [
                    {
                        "model_id": "vendor/or-img",
                        "display_name": "OR Img",
                        "endpoint": "openrouter-images",
                        "is_enabled": True,
                        "capability_overrides": {"max_reference_images": 20},
                    }
                ],
            },
        )
        assert rejected.status_code == 422

        provider_id = await _seed_openrouter_model(
            custom_providers_app_session_factory,
            model_id="vendor/stale-or",
            overrides={"max_reference_images": 20},
        )
        pid = int(provider_id.removeprefix("custom-"))
        models = client.get(f"/api/v1/custom-providers/{pid}").json()["models"]
        assert models[0]["capability_overrides"] is None

    bind_session_factory(monkeypatch, custom_providers_app_session_factory)
    project_path, project = _init_project(tmp_path, monkeypatch, provider_id, "vendor/stale-or")
    with capture_http() as router:
        router.get(f"{_OPENROUTER_BASE}/images/models").respond(
            200,
            json={
                "data": [
                    {
                        "id": "vendor/stale-or",
                        "supported_parameters": {"input_references": {"type": "range", "min": 0, "max": 24}},
                    }
                ]
            },
        )
        limit, _ = await reference_image_limit("demo", project, project_path)
        assert limit == 16


async def test_openrouter_remote_probe_outside_db_without_catalog_reread_and_applies_after_override(
    db_factory, tmp_path, monkeypatch
):
    model_id = "vendor/or-four-refs"
    initial_snapshot = with_entry_facts(
        catalog.factory_snapshot(), "grok", "grok-imagine-image", max_reference_images=8
    )
    install_catalog(monkeypatch, initial_snapshot)
    bound_entry = catalog.find_entry("grok-imagine-image", media_type="image")
    assert bound_entry is not None
    provider_id = await _seed_openrouter_model(
        db_factory,
        model_id=model_id,
        overrides={"max_reference_images": 12},
        entry_binding=bound_entry.key.ref,
    )

    active_sessions = 0

    @asynccontextmanager
    async def tracked_session():
        nonlocal active_sessions
        active_sessions += 1
        try:
            async with db_factory() as session:
                yield session
        finally:
            active_sessions -= 1

    bind_session_factory(monkeypatch, tracked_session)
    project_path, project = _init_project(tmp_path, monkeypatch, provider_id, model_id)
    refs_five = _make_refs(tmp_path, 5)
    image_b64 = base64.b64encode(_png_bytes()).decode("ascii")

    probe_active_sessions: list[int] = []

    def _models_response(_request: httpx.Request) -> httpx.Response:
        probe_active_sessions.append(active_sessions)
        install_catalog(
            monkeypatch,
            with_entry_facts(catalog.factory_snapshot(), "grok", "grok-imagine-image", max_reference_images=1),
        )
        return httpx.Response(
            200,
            json={
                "data": [
                    {
                        "id": model_id,
                        "supported_parameters": {"input_references": {"type": "range", "min": 0, "max": 4}},
                    }
                ]
            },
        )

    with capture_http() as router:
        models_route = router.get(f"{_OPENROUTER_BASE}/images/models").mock(side_effect=_models_response)
        submit_route = router.post(f"{_OPENROUTER_BASE}/images").respond(200, json={"data": [{"b64_json": image_b64}]})

        ctx = await resolve_generation_context(
            "demo", None, project=project, project_path=project_path, image=ImageLaneRequest(generation_type="i2i")
        )
        assert probe_active_sessions == [0], "远端探测必须在数据库会话关闭之后执行"
        assert ctx.image.max_reference_images == 4, "远端探测与上限合成不得受会话关闭后本地目录变更影响"

        with pytest.raises(ImageCapabilityError) as overflow:
            await ctx.generator.generate_image_async(
                prompt="test overflow",
                resource_type="storyboards",
                resource_id="E1S01",
                reference_images=refs_five,
            )
        assert overflow.value.code == "image_reference_images_exceeded"
        assert overflow.value.params["limit"] == 4
        assert not submit_route.called

        await ctx.generator.generate_image_async(
            prompt="test within limit",
            resource_type="storyboards",
            resource_id="E1S01",
            reference_images=refs_five[:4],
        )
        assert len(request_json(only_request(submit_route))["input_references"]) == 4
        assert models_route.call_count == 1

        preflight_limit, _ = await reference_image_limit("demo", project, project_path)
        assert preflight_limit == 4


@pytest.mark.parametrize(
    ("overrides", "entry_limit", "remote_status", "remote_payload", "expected_limit"),
    [
        ({"max_reference_images": 12}, None, 500, {}, 12),
        (None, None, 500, {}, 16),
        (
            None,
            None,
            200,
            {"data": [{"id": "vendor/or-fallback", "supported_parameters": {"input_references": {"max": 0}}}]},
            0,
        ),
        (None, None, 200, {"data": []}, 16),
        (None, 8, 200, {"data": [{"id": "vendor/or-fallback", "supported_parameters": {}}]}, 0),
        (None, 8, 200, {"data": [{"id": "vendor/or-fallback"}]}, 8),
        (
            {"max_reference_images": 12},
            8,
            200,
            {
                "data": [
                    {
                        "id": "vendor/or-fallback",
                        "supported_parameters": {"input_references": {"type": "range", "min": 0}},
                    }
                ]
            },
            12,
        ),
    ],
)
async def test_openrouter_undeclared_remote_limit_keeps_fallback_rules(
    db_factory, tmp_path, monkeypatch, overrides, entry_limit, remote_status, remote_payload, expected_limit
):
    model_id = "vendor/or-fallback"
    entry_binding = None
    if entry_limit is not None:
        install_catalog(
            monkeypatch,
            with_entry_facts(
                catalog.factory_snapshot(), "grok", "grok-imagine-image", max_reference_images=entry_limit
            ),
        )
        entry = catalog.find_entry("grok-imagine-image", media_type="image")
        assert entry is not None
        entry_binding = entry.key.ref

    provider_id = await _seed_openrouter_model(
        db_factory, model_id=model_id, overrides=overrides, entry_binding=entry_binding
    )
    bind_session_factory(monkeypatch, db_factory)
    project_path, project = _init_project(tmp_path, monkeypatch, provider_id, model_id)

    with capture_http() as router:
        router.get(f"{_OPENROUTER_BASE}/images/models").respond(remote_status, json=remote_payload)
        limit, _ = await reference_image_limit("demo", project, project_path)
        assert limit == expected_limit
