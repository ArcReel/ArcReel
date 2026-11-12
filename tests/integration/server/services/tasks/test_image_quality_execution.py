"""项目画质经真实任务、生成上下文与 SDK 到达 HTTP，并提交正式产物。"""

import asyncio
import base64
import json
from collections.abc import Callable

import httpx
import pytest
import respx
from sqlalchemy import update

from lib.catalog import official_sync
from lib.catalog import query as catalog
from lib.config.resolver import ConfigResolver
from lib.config.service import ConfigService
from lib.custom_provider import make_provider_id
from lib.db.models.custom_provider import CustomProvider, CustomProviderModel
from lib.project.project_manager import ProjectManager
from lib.script.grid.models import GridGeneration
from server.services.admission.cost_estimation import estimate_image_batch_cost
from server.services.tasks import generation_context, generation_tasks
from tests.catalog_support import install_catalog, with_dataset_version, with_entry_changes, with_entry_facts
from tests.fakes import bind_safe_session_factory, bind_session_factory, use_project_manager
from tests.http_capture import capture_http, only_request, request_json
from tests.integration.server.derivative_sheet_support import solid_png_bytes
from tests.integration.server.official_sync_support import (
    NEWER,
    serve_dataset,
    sync_as_app_starts_it,
)
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


# ---------------------------------------------------------------------------
# 官方同步之后：缓存的自定义图片后端随新数据重建（应用启动时注入的清缓存）
# ---------------------------------------------------------------------------

_QUALITIES = ("low", "medium", "high")
_RELAY = "https://relay.test/v1"
_OUTPUT = solid_png_bytes((20, 200, 90))


@pytest.fixture
async def relay_project(tmp_path, db_factory, monkeypatch):
    """一个绑定 openai/gpt-image-2 条目的自定义图片模型（OpenAI Images 端点），项目选它并存了画质 low。"""
    bind_session_factory(monkeypatch, db_factory)
    async with db_factory() as session:
        provider = CustomProvider(display_name="Relay", discovery_format="openai", base_url=_RELAY, api_key="k")
        session.add(provider)
        await session.flush()
        session.add(
            CustomProviderModel(
                provider_id=provider.id,
                model_id="relay-image",
                display_name="Relay Image",
                endpoint="openai-images",
                is_default=True,
                is_enabled=True,
                entry_binding="openai/gpt-image-2",
            )
        )
        await session.commit()
        provider_db_id = provider.id
    key = f"{make_provider_id(provider_db_id)}/relay-image"
    project_path = prepare_files(tmp_path)
    seed = _FakePM(project_path)
    seed.project.update(
        image_provider_t2i=key, image_provider_i2i=key, model_settings={key: {"resolution": "1K", "quality": "low"}}
    )
    seed.script["segments"] = [seed.script["segments"][0]]
    persist_active_fake_project(seed)
    use_project_manager(monkeypatch, ProjectManager(tmp_path))
    generation_context.reset_backend_cache_for_tests()
    official_sync.reset_for_tests()
    yield provider_db_id
    generation_context.reset_backend_cache_for_tests()
    official_sync.reset_for_tests()


async def _sent_image_request(base_url: str = _RELAY) -> httpx.Request:
    """跑一次真实的分镜图任务，返回发往 ``base_url`` 的那一个出图请求。"""
    with capture_http() as router:
        route = router.post(f"{base_url}/images/generations").respond(
            200, json={"data": [{"b64_json": base64.b64encode(_OUTPUT).decode()}]}
        )
        await generation_tasks.execute_storyboard_task(
            "demo", "E1S01", {"script_file": "episode_1.json", "prompt": "p"}
        )
    return only_request(route)


async def _estimated_total(tmp_path, db_factory) -> dict[str, float]:
    project = ProjectManager(tmp_path).load_project("demo")
    estimated = await estimate_image_batch_cost(
        project, ["t2i"], aspect_ratio="9:16", resolver=ConfigResolver(db_factory), session_factory=db_factory
    )
    return estimated.total


def _usd(tokens: int) -> float:
    # 测试目录数据里 gpt-image-2 的图片输出 token 单价是 $30 / 百万
    return tokens * 30 / 1_000_000


def _serve_manifest_failure(router: respx.MockRouter) -> None:
    router.get(official_sync.MANIFEST_URL).respond(status_code=500)


@pytest.mark.parametrize(
    ("before", "after", "sent", "tokens"),
    [
        # 同步给绑定条目新增画质取值：存值生效，按 low 估（1K → 138 token）
        ((), _QUALITIES, "low", 138),
        # 同步移除画质取值：忽略存值，按档位推出 medium（1K → 1234 token）
        (_QUALITIES, (), "medium", 1234),
    ],
    ids=["qualities-added", "qualities-removed"],
)
async def test_request_quality_follows_the_estimate_after_official_sync(
    relay_project, tmp_path, db_factory, monkeypatch, before, after, sent, tokens
):
    """缓存了自定义图片后端之后，官方同步改了绑定条目的画质取值：下一次请求的画质与当时的费用预估一致。"""
    snapshot = with_entry_facts(catalog.factory_snapshot(), "openai", "gpt-image-2", qualities=before)
    install_catalog(monkeypatch, snapshot)
    # 同步前执行一次，缓存按旧数据构造的后端
    assert request_json(await _sent_image_request())["quality"] == ("low" if before else "medium")

    upstream = with_dataset_version(with_entry_facts(snapshot, "openai", "gpt-image-2", qualities=after), NEWER)
    await sync_as_app_starts_it(
        monkeypatch,
        db_factory,
        serve_dataset(upstream),
        lambda: catalog.official_data().dataset_version == NEWER,
    )

    assert await _estimated_total(tmp_path, db_factory) == {"USD": pytest.approx(_usd(tokens))}
    assert request_json(await _sent_image_request())["quality"] == sent


@pytest.mark.parametrize("outcome", ["applied", "unchanged", "failed"])
async def test_backend_cache_is_kept_unless_the_sync_applies_new_data(relay_project, db_factory, monkeypatch, outcome):
    """只有装入了新数据才重建后端：用改过的供应商地址区分请求来自缓存的实例还是重建的实例。"""
    await _sent_image_request()
    moved = "https://relay-moved.test/v1"
    async with db_factory() as session:
        await session.execute(update(CustomProvider).where(CustomProvider.id == relay_project).values(base_url=moved))
        await session.commit()

    current = catalog.official_data()
    serve: Callable[[respx.MockRouter], None]
    if outcome == "failed":
        serve = _serve_manifest_failure
    else:
        serve = serve_dataset(with_dataset_version(current, NEWER) if outcome == "applied" else current)
    done = {
        "applied": lambda: catalog.official_data().dataset_version == NEWER,
        "unchanged": lambda: official_sync.sync_status().last_success_at is not None,
        "failed": lambda: official_sync.sync_status().last_error is not None,
    }[outcome]
    await sync_as_app_starts_it(monkeypatch, db_factory, serve, done)

    expected = moved if outcome == "applied" else _RELAY
    assert str((await _sent_image_request(expected)).url) == f"{expected}/images/generations"


@pytest.mark.parametrize("outcome", ["added", "removed", "unchanged", "failed"])
async def test_official_sync_refreshes_worker_lanes_only_when_applied(file_db_factory, monkeypatch, outcome):
    from dataclasses import replace

    from lib.generation.generation_queue import GenerationQueue
    from lib.generation.generation_worker import GenerationWorker, SlotTable
    from tests.catalog_support import catalog_of
    from tests.integration.lib.generation.worker_support import stub_executors

    snapshot = catalog.factory_snapshot()
    # 使用完整条目和不同供应商清单模拟新增/移除 lane，不改变目录合成的保留条目规则。
    without_audio = replace(snapshot, builtin_providers=catalog_of([], {"grok": []}).builtin_providers)
    audio_listing = snapshot.builtin_providers["minimax"]
    with_audio = replace(snapshot, builtin_providers={"grok": audio_listing})
    initial = with_audio if outcome == "removed" else without_audio
    install_catalog(monkeypatch, initial)
    bind_safe_session_factory(monkeypatch, file_db_factory)
    async with file_db_factory() as session:
        await ConfigService(session).set_provider_config("grok", "audio_max_workers", "2")
        await session.commit()
    slots = SlotTable()
    queue = GenerationQueue(session_factory=file_db_factory)

    async def execute(_task, *, claimed_provider_id):
        assert claimed_provider_id == "grok"
        return {"audio": "generated"}

    worker = GenerationWorker(
        queue=queue,
        executor=execute,
        resume_executor=stub_executors.execute_resume,
        slots=slots,
        lanes=("audio",),
    )
    await worker.reload_limits()
    if outcome in {"unchanged", "failed"}:
        worker._capacity.replace({"grok": {"audio": 4}})
    before = worker._capacity.get("grok", "audio")
    running = asyncio.get_running_loop().create_future()
    slots.register("grok", "audio", "inflight", running)
    if outcome == "failed":
        serve = _serve_manifest_failure
    else:
        upstream = without_audio if outcome == "removed" else with_audio
        serve = serve_dataset(initial if outcome == "unchanged" else with_dataset_version(upstream, NEWER))
    expected = {"added": 2, "removed": 0, "unchanged": before, "failed": before}[outcome]
    done = {
        "added": lambda: worker._capacity.get("grok", "audio") == 2,
        "removed": lambda: worker._capacity.get("grok", "audio") == 0,
        "unchanged": lambda: official_sync.sync_status().last_success_at is not None,
        "failed": lambda: official_sync.sync_status().last_error is not None,
    }[outcome]
    try:
        await sync_as_app_starts_it(monkeypatch, file_db_factory, serve, done, worker=worker)
        assert worker._capacity.get("grok", "audio") == expected
        assert slots.occupied("grok", "audio") == 1
        running.set_result(None)
        assert slots.drain_finished() == [("inflight", running)]
        assert slots.occupied("grok", "audio") == 0
        if outcome == "added":
            task = await queue.enqueue_task(
                project_name="sync-audio",
                task_type="tts",
                media_type="audio",
                resource_id="voice",
                provider_id="grok",
            )
            assert await worker._claim_tasks()
            await asyncio.gather(*slots.all_active_tasks())
            await worker._drain_finished_tasks()
            finished = await queue.get_task(task["task_id"])
            assert finished is not None
            assert finished["status"] == "succeeded"
    finally:
        running.cancel()
        official_sync.reset_for_tests()


async def test_sync_rebuilds_cached_image_model_facts_and_builtin_video_entry(relay_project, db_factory, monkeypatch):
    from lib.backends.image_backends.base import ImageCapability
    from lib.backends.video_backends.name_family import catalog_entry_of

    async with db_factory() as session:
        await ConfigService(session).set_provider_config("grok", "api_key", "test-key")
        await session.commit()
    snapshot = with_entry_facts(
        catalog.factory_snapshot(),
        "openai",
        "gpt-image-2",
        text_to_image=True,
        image_to_image=True,
        max_reference_images=3,
    )
    install_catalog(monkeypatch, snapshot)
    resolver = ConfigResolver(db_factory)

    async def backends():
        image = await generation_context._get_or_create_image_backend(
            make_provider_id(relay_project), {"model": "relay-image"}, resolver
        )
        video, _ = await generation_context._get_or_create_video_backend(
            "grok", {"model": "grok-imagine-video"}, resolver
        )
        return image, video

    before_image, before_video = await backends()
    assert before_image.entry_reference_limit == 3
    assert ImageCapability.IMAGE_TO_IMAGE in before_image.effective_capabilities
    before_entry = catalog_entry_of(before_video)
    assert before_entry is not None
    upstream = with_dataset_version(
        with_entry_facts(snapshot, "openai", "gpt-image-2", image_to_image=False, max_reference_images=1),
        NEWER,
    )
    upstream = with_entry_changes(upstream, "grok", "grok-imagine-video", display_name="Synced video")
    await sync_as_app_starts_it(
        monkeypatch,
        db_factory,
        serve_dataset(upstream),
        lambda: catalog.official_data().dataset_version == NEWER,
    )
    after_image, after_video = await backends()
    assert after_image.entry_reference_limit == 1
    assert after_image.effective_capabilities == frozenset({ImageCapability.TEXT_TO_IMAGE})
    after_entry = catalog_entry_of(after_video)
    assert after_entry is not None
    assert after_entry.display_name == "Synced video"
    assert before_entry.display_name != after_entry.display_name
