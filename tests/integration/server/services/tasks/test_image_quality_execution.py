"""项目画质经真实任务、生成上下文与 SDK 到达 HTTP，并提交正式产物。"""

import asyncio
import base64
import contextlib
import hashlib
import json
from collections.abc import Callable

import httpx
import pytest
import respx
from sqlalchemy import update

from arcreel_catalog_core import CatalogData, dump_entry
from lib.catalog import official_sync
from lib.catalog import query as catalog
from lib.config.resolver import ConfigResolver
from lib.config.service import ConfigService
from lib.custom_provider import make_provider_id
from lib.db.models.custom_provider import CustomProvider, CustomProviderModel
from lib.project.project_manager import ProjectManager
from lib.script.grid.models import GridGeneration
from server import app as server_app
from server.services.admission.cost_estimation import estimate_image_batch_cost
from server.services.tasks import generation_context, generation_tasks
from tests.catalog_support import install_catalog, with_dataset_version, with_entry_facts
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


# ---------------------------------------------------------------------------
# 官方同步之后：缓存的自定义图片后端随新数据重建（应用启动时注入的清缓存）
# ---------------------------------------------------------------------------

_QUALITIES = ("low", "medium", "high")
_RELAY = "https://relay.test/v1"
_NEWER = "2099-01-01T00:00:00Z"
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


async def _sync_as_app_starts_it(
    monkeypatch, db_factory, serve: Callable[[respx.MockRouter], None], done: Callable[[], bool]
) -> None:
    """经应用启动时的入口跑一轮官方同步，``done`` 成立后停掉同步循环。

    同步每用完一个数据库会话就检查一次 ``done``。这一轮最后一个会话记下同步结果，之后装入新数据、清缓存都不再
    让出事件循环，所以 ``done`` 取装入后的目录或记下的同步状态即可。
    """
    closed = asyncio.Event()

    @contextlib.asynccontextmanager
    async def session_factory():
        async with db_factory() as session:
            yield session
        closed.set()

    async def _until_done() -> None:
        while not done():
            closed.clear()
            await closed.wait()

    # 根 conftest 把应用启动的同步换成了不发请求的空任务，这里换回真实入口
    monkeypatch.setattr(server_app, "start_official_sync", official_sync.start_official_sync)
    async with httpx.AsyncClient() as client:
        with capture_http() as router:
            serve(router)
            task = server_app.start_catalog_sync(session_factory, lambda: client)
            assert task is not None
            waiting = asyncio.create_task(_until_done())
            # 防挂起的上限：不用 asyncio.timeout 包住 await，超时取消会掩盖挂起
            await asyncio.wait({waiting}, timeout=5)
            waiting.cancel()
            await official_sync.stop_official_sync(task)
    assert waiting.done()
    assert not waiting.cancelled(), "同步这一轮没有跑完"


def _published_files(data: CatalogData) -> tuple[bytes, bytes]:
    """目录数据仓库发布的一对文件 ``(清单文件, 数据文件)``，由 ``data`` 整份写出。"""
    raw = {
        "dataset_version": data.dataset_version,
        "entries": [dump_entry(entry) for entry in data.entries],
        "builtin_providers": {
            provider_id: {
                "models": [
                    {
                        key: value
                        for key, value in {
                            "api_model_name": model.api_model_name,
                            "entry": model.entry,
                            "media_type": model.media_type,
                            "multiplier": model.multiplier,
                            "min_app_version": model.min_app_version,
                            "endpoint": model.endpoint,
                        }.items()
                        if value is not None
                    }
                    for model in listing.models
                ],
                "defaults": dict(listing.defaults),
            }
            for provider_id, listing in data.builtin_providers.items()
        },
    }
    content = json.dumps(raw).encode()
    manifest = {"dataset_version": data.dataset_version, "sha256": hashlib.sha256(content).hexdigest()}
    return json.dumps(manifest).encode(), content


def _serve_dataset(data: CatalogData) -> Callable[[respx.MockRouter], None]:
    manifest, content = _published_files(data)

    def serve(router: respx.MockRouter) -> None:
        router.get(official_sync.MANIFEST_URL).respond(content=manifest)
        router.get(official_sync.CATALOG_URL).respond(content=content)

    return serve


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

    upstream = with_dataset_version(with_entry_facts(snapshot, "openai", "gpt-image-2", qualities=after), _NEWER)
    await _sync_as_app_starts_it(
        monkeypatch,
        db_factory,
        _serve_dataset(upstream),
        lambda: catalog.official_data().dataset_version == _NEWER,
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
        serve = _serve_dataset(with_dataset_version(current, _NEWER) if outcome == "applied" else current)
    done = {
        "applied": lambda: catalog.official_data().dataset_version == _NEWER,
        "unchanged": lambda: official_sync.sync_status().last_success_at is not None,
        "failed": lambda: official_sync.sync_status().last_error is not None,
    }[outcome]
    await _sync_as_app_starts_it(monkeypatch, db_factory, serve, done)

    expected = moved if outcome == "applied" else _RELAY
    assert str((await _sent_image_request(expected)).url) == f"{expected}/images/generations"
