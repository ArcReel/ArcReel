"""项目画质经真实任务、生成上下文与 SDK 到达 HTTP，并提交正式产物。"""

import base64
import json

import pytest

from lib.catalog import query as catalog
from lib.config.service import ConfigService
from lib.project.project_manager import ProjectManager
from lib.script.grid.models import GridGeneration
from server.services.tasks import generation_context, generation_tasks
from tests.catalog_support import install_catalog, with_entry_facts
from tests.fakes import bind_session_factory, use_project_manager
from tests.http_capture import capture_http, only_request, request_json
from tests.integration.server.derivative_sheet_support import solid_png_bytes
from tests.integration.server.services.tasks.generation_tasks_support import (
    _FakePM,
    persist_active_fake_project,
    prepare_files,
)


@pytest.mark.parametrize("grid", [False, True], ids=["storyboard", "grid"])
@pytest.mark.parametrize("quality", ["low", None])
async def test_project_quality_reaches_formal_image_request(tmp_path, db_factory, monkeypatch, grid, quality):
    bind_session_factory(monkeypatch, db_factory)
    async with db_factory() as session:
        await ConfigService(session).set_provider_config("openai", "api_key", "test-key")
        await session.commit()
    install_catalog(
        monkeypatch,
        with_entry_facts(catalog.factory_snapshot(), "openai", "gpt-image-2", qualities=("low", "medium", "high")),
    )
    project_path = prepare_files(tmp_path)
    # 复用剧本数据构造器落盘；执行使用真实 ProjectManager，不把数据构造器注入任务。
    seed = _FakePM(project_path)
    seed.project.update(
        image_provider_t2i="openai/gpt-image-2",
        model_settings={"openai/gpt-image-2": {"resolution": "1K", "quality": quality}},
    )
    seed.script["segments"] = [seed.script["segments"][0]]
    persist_active_fake_project(seed)
    use_project_manager(monkeypatch, ProjectManager(tmp_path))
    record = None
    if grid:
        record = GridGeneration.create(
            episode=1,
            script_file="episode_1.json",
            scene_ids=["E1S01"],
            rows=2,
            cols=2,
            grid_size="1K",
            provider="openai",
            model="gpt-image-2",
            video_aspect_ratio="9:16",
            prompt="grid",
        )
        (project_path / "grids").mkdir(exist_ok=True)
        (project_path / "grids" / f"{record.id}.json").write_text(json.dumps(record.to_dict()))

    output = solid_png_bytes((20, 200, 90))
    generation_context.reset_backend_cache_for_tests()
    try:
        with capture_http() as router:
            route = router.post("https://api.openai.com/v1/images/generations").respond(
                200, json={"data": [{"b64_json": base64.b64encode(output).decode()}]}
            )
            if record is not None:
                result = await generation_tasks.execute_grid_task("demo", record.id, {"script_file": "episode_1.json"})
            else:
                result = await generation_tasks.execute_storyboard_task(
                    "demo", "E1S01", {"script_file": "episode_1.json", "prompt": "p"}
                )
        body = request_json(only_request(route))
        assert body["quality"] == (quality or "medium")
        assert body["size"] == "1008x1792"
        # 分镜夹具已有一张旧图，正式提交先补录它再写第 2 版；宫格是首版。
        assert result["version"] == (1 if grid else 2)
        assert (project_path / result["file_path"]).read_bytes() == output
    finally:
        generation_context.reset_backend_cache_for_tests()
