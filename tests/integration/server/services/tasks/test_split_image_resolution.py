"""异模型文生图与图生图的分辨率读取：批量分镜估价与执行共享同一组项目配置。"""

from __future__ import annotations

import base64

import pytest

from lib.artifacts.artifact_activation import active_artifact_currency_resolver
from lib.backends.agnes_shared import AGNES_BASE_URL
from lib.backends.vidu_shared import VIDU_BASE_URL
from lib.config.resolver import ConfigResolver, ProviderModel
from lib.config.service import ConfigService
from lib.project.project_manager import ProjectManager
from server.services.admission.cost_estimation import estimate_image_batch_cost
from server.services.admission.storyboard_batch import plan_storyboard_image_batch
from server.services.tasks import generation_context, generation_tasks
from server.services.tasks.generation_context import ImageLaneRequest, resolve_generation_context
from server.services.tasks.image_edit_tasks import execute_image_edit_task
from tests.fakes import bind_session_factory, use_project_manager
from tests.http_capture import capture_http, only_request, request_json
from tests.integration.server.derivative_sheet_support import solid_png_bytes
from tests.integration.server.services.tasks.generation_tasks_support import (
    _FakePM,
    persist_active_fake_project,
    prepare_files,
    register_asset_sheet_claims,
)

_T2I_PROVIDER = "agnes"
_T2I_MODEL = "agnes-image-2.5-flash"
_I2I_PROVIDER = "vidu"
_I2I_MODEL = "viduq2"
_T2I_PAIR = f"{_T2I_PROVIDER}/{_T2I_MODEL}"
_I2I_PAIR = f"{_I2I_PROVIDER}/{_I2I_MODEL}"
_VIDU_RESULT_URL = "https://api.vidu.cn/mock/output.png"


def _agnes_image_response(png_bytes: bytes) -> dict:
    return {"created": 0, "data": [{"b64_json": base64.b64encode(png_bytes).decode()}]}


def _register_vidu_routes(router, task_id: str, png_bytes: bytes):
    create_route = router.post(f"{VIDU_BASE_URL}/reference2image").respond(
        200,
        json={"task_id": task_id, "state": "created", "credits": 10},
    )
    router.get(f"{VIDU_BASE_URL}/tasks/{task_id}/creations").respond(
        200,
        json={"id": task_id, "state": "success", "credits": 10, "creations": [{"url": _VIDU_RESULT_URL}]},
    )
    router.get(_VIDU_RESULT_URL).respond(200, content=png_bytes, headers={"content-type": "image/png"})
    return create_route


async def _seed_split_image_project(
    tmp_path,
    db_factory,
    monkeypatch,
    *,
    t2i_resolution: str | None,
    i2i_resolution: str | None,
):
    bind_session_factory(monkeypatch, db_factory)
    async with db_factory() as session:
        service = ConfigService(session)
        await service.set_provider_config(_T2I_PROVIDER, "api_key", "agnes-test-key")
        await service.set_provider_config(_I2I_PROVIDER, "api_key", "vidu-test-key")
        await session.commit()

    model_settings: dict[str, dict[str, str | None]] = {}
    if t2i_resolution is not None:
        model_settings[_T2I_PAIR] = {"resolution": t2i_resolution}
    if i2i_resolution is not None:
        model_settings[_I2I_PAIR] = {"resolution": i2i_resolution}

    project_path = prepare_files(tmp_path)
    (project_path / "characters" / "Alice.png").write_bytes(solid_png_bytes((200, 80, 80)))
    seed = _FakePM(project_path)
    seed.project.update(
        default_image_backend=_T2I_PAIR,
        image_provider_i2i=_I2I_PAIR,
        model_settings=model_settings,
    )
    first_shot = {
        **seed.script["segments"][0],
        "segment_id": "E1S01",
        "segment_break": False,
        "characters_in_segment": [],
        "scenes": [],
        "props": [],
        "generated_assets": {"storyboard_image": None, "video_clip": None, "status": "pending"},
    }
    second_shot = {
        **seed.script["segments"][0],
        "segment_id": "E1S02",
        "segment_break": True,
        "characters_in_segment": ["Alice"],
        "scenes": [],
        "props": [],
        "generated_assets": {"storyboard_image": None, "video_clip": None, "status": "pending"},
    }
    seed.script["segments"] = [first_shot, second_shot]
    persist_active_fake_project(seed)
    register_asset_sheet_claims(seed)
    pm = ProjectManager(tmp_path)
    use_project_manager(monkeypatch, pm)
    return pm, project_path


async def test_split_t2i_and_i2i_models_read_their_own_resolutions_in_estimation_and_execution(
    tmp_path, db_factory, monkeypatch
):
    pm, project_path = await _seed_split_image_project(
        tmp_path,
        db_factory,
        monkeypatch,
        t2i_resolution="2K",
        i2i_resolution="4K",
    )
    project = pm.load_project("demo")
    script = pm.load_script("demo", "episode_1.json")
    resolver = ConfigResolver(db_factory)

    generation_context.reset_backend_cache_for_tests()
    try:
        plan = plan_storyboard_image_batch(
            project=project,
            script=script,
            script_file="episode_1.json",
            episode=1,
            resolver=active_artifact_currency_resolver(project_path, project),
            project_path=project_path,
            image_limit=(None, ""),
        )
        assert plan.lanes == {"E1S01": "t2i", "E1S02": "i2i"}

        t2i_estimate = await estimate_image_batch_cost(
            project, [plan.lanes["E1S01"]], aspect_ratio="9:16", resolver=resolver, session_factory=db_factory
        )
        i2i_estimate = await estimate_image_batch_cost(
            project, [plan.lanes["E1S02"]], aspect_ratio="9:16", resolver=resolver, session_factory=db_factory
        )
        combined_estimate = await estimate_image_batch_cost(
            project,
            [plan.lanes["E1S01"], plan.lanes["E1S02"]],
            aspect_ratio="9:16",
            resolver=resolver,
            session_factory=db_factory,
        )
        assert t2i_estimate.total == {"USD": pytest.approx(0.018)}
        assert i2i_estimate.total == {"CNY": pytest.approx(0.3125)}
        assert combined_estimate.total == {"USD": pytest.approx(0.018), "CNY": pytest.approx(0.3125)}

        t2i_ctx = await resolve_generation_context(
            "demo", None, project=project, project_path=project_path, image=ImageLaneRequest(generation_type="t2i")
        )
        i2i_ctx = await resolve_generation_context(
            "demo", None, project=project, project_path=project_path, image=ImageLaneRequest(generation_type="i2i")
        )
        assert t2i_ctx.image.provider_model == ProviderModel(_T2I_PROVIDER, _T2I_MODEL)
        assert t2i_ctx.image.resolution == "2K"
        assert i2i_ctx.image.provider_model == ProviderModel(_I2I_PROVIDER, _I2I_MODEL)
        assert i2i_ctx.image.resolution == "4K"

        output = solid_png_bytes((20, 120, 220))
        with capture_http() as router:
            t2i_route = router.post(f"{AGNES_BASE_URL}/images/generations").respond(
                200, json=_agnes_image_response(output)
            )
            i2i_route = _register_vidu_routes(router, "vidu-shot-2", output)
            await generation_tasks.execute_storyboard_task(
                "demo", "E1S01", {"script_file": "episode_1.json", "prompt": "t2i shot"}
            )
            await generation_tasks.execute_storyboard_task(
                "demo", "E1S02", {"script_file": "episode_1.json", "prompt": "i2i shot"}
            )
        t2i_body = request_json(only_request(t2i_route))
        i2i_body = request_json(only_request(i2i_route))
        assert t2i_body["model"] == _T2I_MODEL
        assert t2i_body["size"] == "2K"
        assert i2i_body["model"] == _I2I_MODEL
        assert i2i_body["resolution"] == "4K"

        with capture_http() as router:
            edit_route = _register_vidu_routes(router, "vidu-edit-1", output)
            await execute_image_edit_task(
                "demo",
                "E1S01",
                {"resource_type": "storyboard", "script_file": "episode_1.json", "prompt": "brighten sky"},
            )
        edit_body = request_json(only_request(edit_route))
        assert edit_body["model"] == _I2I_MODEL
        assert edit_body["resolution"] == "4K"
    finally:
        generation_context.reset_backend_cache_for_tests()


async def test_unset_i2i_resolution_keeps_provider_default_in_estimation_and_execution(
    tmp_path, db_factory, monkeypatch
):
    pm, project_path = await _seed_split_image_project(
        tmp_path,
        db_factory,
        monkeypatch,
        t2i_resolution="3K",
        i2i_resolution=None,
    )
    project = pm.load_project("demo")
    resolver = ConfigResolver(db_factory)

    generation_context.reset_backend_cache_for_tests()
    try:
        t2i_estimate = await estimate_image_batch_cost(
            project, ["t2i"], aspect_ratio="9:16", resolver=resolver, session_factory=db_factory
        )
        i2i_estimate = await estimate_image_batch_cost(
            project, ["i2i"], aspect_ratio="9:16", resolver=resolver, session_factory=db_factory
        )
        assert t2i_estimate.total == {"USD": pytest.approx(0.021)}
        assert i2i_estimate.total == {"CNY": pytest.approx(0.1875)}

        i2i_ctx = await resolve_generation_context(
            "demo", None, project=project, project_path=project_path, image=ImageLaneRequest(generation_type="i2i")
        )
        assert i2i_ctx.image.provider_model == ProviderModel(_I2I_PROVIDER, _I2I_MODEL)
        assert i2i_ctx.image.resolution is None

        output = solid_png_bytes((100, 180, 60))
        with capture_http() as router:
            i2i_route = _register_vidu_routes(router, "vidu-default-2", output)
            await generation_tasks.execute_storyboard_task(
                "demo", "E1S02", {"script_file": "episode_1.json", "prompt": "i2i default tier"}
            )
        i2i_body = request_json(only_request(i2i_route))
        assert i2i_body["model"] == _I2I_MODEL
        assert "resolution" not in i2i_body
    finally:
        generation_context.reset_backend_cache_for_tests()
