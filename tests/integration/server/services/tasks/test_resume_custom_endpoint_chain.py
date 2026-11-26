"""自定义端点（``ce-<id>``）视频任务重启续跑的整链回归。

覆盖「DB 里的 ce- 行 → 孤儿扫描 → execute_resume_video_task →
resolve_generation_context → 真 DeclarativeVideoBackend → HTTP」全链：
custom_endpoint / custom_provider / custom_provider_model 三行与 running 任务行
都真实落库，业务链路上的函数一律真跑，仅出站 HTTP 用 respx 打桩。判据落在
行为上：HTTP 计数（resume 不重复提交计费）、任务终态与付费产物字节。
"""

from __future__ import annotations

import asyncio
import json
from datetime import date
from pathlib import Path

import httpx
import pytest

from arcreel_market_core.video_backend_contract import VideoAudioMode
from lib.catalog import query as catalog
from lib.catalog.errors import ModelDiscontinuedError
from lib.config.service import ConfigService
from lib.custom_provider import make_provider_id
from lib.custom_provider.path_overrides import endpoint_guard
from lib.db.models.task import Task
from lib.db.repositories.custom_provider_repo import CustomProviderRepository
from lib.generation.generation_queue import GenerationQueue
from lib.generation.generation_worker import GenerationWorker
from server.services.tasks import generation_context
from server.services.tasks.generation_context import VideoLaneRequest, resolve_generation_context
from server.services.tasks.generation_tasks import execute_generation_task
from server.services.tasks.resume_executor import execute_resume_video_task
from tests.catalog_support import install_catalog, set_catalog_today, with_entry_changes
from tests.http_capture import capture_http
from tests.integration.server.services.tasks.video_recovery_support import (
    configure_chain_project,
)
from tests.integration.server.services.tasks.video_recovery_support import (
    seed_custom_video_rows as _seed_custom_video_rows,
)
from tests.integration.server.services.tasks.video_recovery_support import (
    seed_running_video_task as _seed_running_video_task,
)
from tests.integration.server.services.tasks.video_recovery_support import (
    storyboard_checkpoint_json as _storyboard_checkpoint_json,
)


@pytest.fixture(autouse=True)
def clean_backend_cache():
    """除清空缓存条目外，同时清空 per-key 锁。

    pytest-asyncio 按测试函数切换独立事件循环，跨测试复用同一缓存 key 时，
    前一个测试触发过竞争的 ``asyncio.Lock`` 会永久绑定在已关闭的旧循环上，
    后续测试再次竞争即抛 ``RuntimeError``。清空 locks 不影响被测生产行为。
    """
    generation_context.reset_backend_cache_for_tests()
    yield
    generation_context.reset_backend_cache_for_tests()


@pytest.fixture
def chain_project(session_factory, tmp_path: Path, monkeypatch) -> Path:
    return configure_chain_project(session_factory, tmp_path, monkeypatch)


async def _load_task_row(session_factory, task_id: str) -> Task:
    async with session_factory() as session:
        row = await session.get(Task, task_id)
        assert row is not None
        return row


async def _run_orphan_recovery(session_factory) -> None:
    """真 worker + 真 queue 驱动一轮启动期孤儿扫描，并等后台 dispatcher 跑完。"""
    worker = GenerationWorker(
        queue=GenerationQueue(session_factory=session_factory),
        executor=execute_generation_task,
        resume_executor=execute_resume_video_task,
    )
    await worker._recovery.handle_orphans()
    await asyncio.wait_for(worker._recovery.wait_dispatchers(), timeout=10)


async def test_ce_orphan_resumes_end_to_end_without_resubmitting(chain_project: Path, session_factory):
    """happy path：ce- 行经 resolve 构出真 backend，续跑只 poll+download、不重新提交。

    任务行落了历史提交域名（``submitted_base_url``），而供应商行当前域名已不同：
    续跑必须按历史域名轮询（域名回放），提交后改 base_url 不能让付费任务失联。
    """
    rows = await _seed_custom_video_rows(session_factory)
    await _seed_running_video_task(
        session_factory,
        task_id="T-ce-resume",
        provider_id=rows["provider_id"],
        checkpoint_json=_storyboard_checkpoint_json(
            "T-ce-resume",
            provider_id=rows["provider_id"],
            endpoint_guard=rows["endpoint_keys"][0],
        ),
        submitted_base_url="https://old-relay.test",
    )

    with capture_http() as router:
        submit = router.post("https://relay.test/v1/video/create")
        poll_current = router.get("https://relay.test/v1/video/fetch/job-old")
        poll = router.get("https://old-relay.test/v1/video/fetch/job-old").mock(
            return_value=httpx.Response(
                200,
                json={"status": "completed", "video_url": "https://old-relay.test/files/job-old.mp4"},
            )
        )
        download = router.get("https://old-relay.test/files/job-old.mp4").mock(
            return_value=httpx.Response(200, content=b"resumed")
        )

        await _run_orphan_recovery(session_factory)

        initial = await _load_task_row(session_factory, "T-ce-resume")
        assert initial.status == "succeeded"
        async with session_factory() as session:
            running = await session.get(Task, "T-ce-resume")
            running.status = "running"
            running.result_json = None
            await session.commit()
        await _run_orphan_recovery(session_factory)
        restored = await _load_task_row(session_factory, "T-ce-resume")
        assert json.loads(restored.result_json) == json.loads(initial.result_json)

    assert submit.call_count == 0
    assert poll_current.call_count == 0
    assert poll.call_count == 1
    assert download.call_count == 1

    row = await _load_task_row(session_factory, "T-ce-resume")
    assert row.status == "succeeded"
    result = json.loads(row.result_json or "{}")
    assert result["resource_id"] == "E1S01"
    from lib.artifacts.version_manager import VersionManager

    history = VersionManager(chain_project).get_versions("videos", "E1S01")
    assert [version["task_id"] for version in history["versions"]] == ["T-ce-resume"]
    paid_files = sorted((chain_project / "versions" / "videos").glob("E1S01_v*.mp4"))
    assert [path.read_bytes() for path in paid_files] == [b"resumed"]


async def test_ce_orphan_fails_before_any_http_when_endpoint_rebound(chain_project: Path, session_factory):
    """endpoint guard：模型行改挂另一端点键后，续跑在发出任何 HTTP 请求前失败。"""
    rows = await _seed_custom_video_rows(session_factory, endpoint_count=2, model_endpoint_index=1)
    await _seed_running_video_task(
        session_factory,
        task_id="T-ce-rebound",
        provider_id=rows["provider_id"],
        checkpoint_json=_storyboard_checkpoint_json(
            "T-ce-rebound",
            provider_id=rows["provider_id"],
            endpoint_guard=rows["endpoint_keys"][0],
        ),
    )

    with capture_http() as router:
        submit = router.post("https://relay.test/v1/video/create")
        poll = router.get("https://relay.test/v1/video/fetch/job-old")

        await _run_orphan_recovery(session_factory)

    assert submit.call_count == 0
    assert poll.call_count == 0

    row = await _load_task_row(session_factory, "T-ce-rebound")
    assert row.status == "failed"
    assert row.error_message is not None
    assert "resume_endpoint_changed" in row.error_message
    assert not (chain_project / "versions" / "videos").exists()


async def test_ce_orphan_fails_without_resubmitting_when_path_overrides_changed(chain_project: Path, session_factory):
    """路径覆盖随协议冻结：提交后改了请求地址，续跑与改绑端点同样失败，且不重新提交。"""
    rows = await _seed_custom_video_rows(session_factory, path_overrides={"poll": "/v2/fetch/{{ task_id }}"})
    endpoint_key = rows["endpoint_keys"][0]
    await _seed_running_video_task(
        session_factory,
        task_id="T-ce-paths",
        provider_id=rows["provider_id"],
        checkpoint_json=_storyboard_checkpoint_json(
            "T-ce-paths",
            provider_id=rows["provider_id"],
            endpoint_guard=endpoint_guard(endpoint_key, {"poll": "/v1/video/fetch/{{ task_id }}"}),
        ),
    )

    with capture_http() as router:
        submit = router.post("https://relay.test/v1/video/create")
        poll_submitted = router.get("https://relay.test/v1/video/fetch/job-old")
        poll_current = router.get("https://relay.test/v2/fetch/job-old")

        await _run_orphan_recovery(session_factory)

    assert submit.call_count == 0
    assert poll_submitted.call_count == 0
    assert poll_current.call_count == 0
    row = await _load_task_row(session_factory, "T-ce-paths")
    assert row.status == "failed"
    assert row.error_message is not None
    assert "resume_endpoint_changed" in row.error_message


async def test_ce_orphan_resumes_through_unchanged_path_overrides(chain_project: Path, session_factory):
    """覆盖未改：续跑通过协议比对，并按覆盖后的轮询地址取件。"""
    overrides = {"poll": "/v2/fetch/{{ task_id }}"}
    rows = await _seed_custom_video_rows(session_factory, path_overrides=overrides)
    await _seed_running_video_task(
        session_factory,
        task_id="T-ce-paths-kept",
        provider_id=rows["provider_id"],
        checkpoint_json=_storyboard_checkpoint_json(
            "T-ce-paths-kept",
            provider_id=rows["provider_id"],
            endpoint_guard=endpoint_guard(rows["endpoint_keys"][0], overrides),
        ),
    )

    with capture_http() as router:
        submit = router.post("https://relay.test/v1/video/create")
        poll = router.get("https://relay.test/v2/fetch/job-old").mock(
            return_value=httpx.Response(
                200, json={"status": "completed", "video_url": "https://relay.test/files/job-old.mp4"}
            )
        )
        router.get("https://relay.test/files/job-old.mp4").mock(return_value=httpx.Response(200, content=b"resumed"))

        await _run_orphan_recovery(session_factory)

    assert submit.call_count == 0
    assert poll.call_count == 1
    assert (await _load_task_row(session_factory, "T-ce-paths-kept")).status == "succeeded"


RETIRED_MODEL = "agnes-video-v2.0"
SHUTDOWN = date(2026, 11, 24)


@pytest.fixture
def retired_model(monkeypatch) -> None:
    """``RETIRED_MODEL`` 的条目定在 ``SHUTDOWN`` 关停，今天就是关停日：任务在此之前已提交，重启后才续跑。"""
    install_catalog(
        monkeypatch,
        with_entry_changes(
            catalog.factory_snapshot(),
            "agnes",
            RETIRED_MODEL,
            discontinued=False,
            shutdown_date=SHUTDOWN,
            successor="agnes/agnes-video-2.5",
        ),
    )
    set_catalog_today(monkeypatch, SHUTDOWN)


async def _configure_agnes(session_factory) -> None:
    async with session_factory() as session:
        await ConfigService(session).set_provider_config("agnes", "api_key", "sk-agnes")
        await session.commit()


async def _assert_new_submission_rejected(provider_id: str) -> None:
    """同一模型在关停日期之后的新提交仍报下线：按当前配置解析（队列执行移除了入队时的身份）报错，
    载荷里锁定了同一身份、但没有声明是续跑的调用同样报错。"""
    identity = f"{provider_id}/{RETIRED_MODEL}"
    with pytest.raises(ModelDiscontinuedError) as excinfo:
        await resolve_generation_context(
            "demo",
            None,
            project={"video_provider_i2v": identity},
            video=VideoLaneRequest(generation_type="i2v", route="storyboard"),
        )
    assert excinfo.value.code == "model_discontinued"
    assert excinfo.value.params["shutdown_date"] == SHUTDOWN.isoformat()
    with pytest.raises(ModelDiscontinuedError):
        await resolve_generation_context(
            "demo", {"video_provider_i2v": identity}, project={}, video=VideoLaneRequest(generation_type="i2v")
        )


async def test_builtin_job_submitted_before_shutdown_still_resumes_after_it(
    chain_project: Path, session_factory, retired_model
):
    """内置供应商：关停前已提交、带厂商任务 ID 的任务，关停后重启续跑照常轮询取回结果，不重新提交；新提交仍报下线。"""
    await _configure_agnes(session_factory)
    await _seed_running_video_task(
        session_factory,
        task_id="T-agnes-retired",
        provider_id="agnes",
        checkpoint_json=_storyboard_checkpoint_json(
            "T-agnes-retired", provider_id="agnes", endpoint_guard=None, model_id=RETIRED_MODEL
        ),
    )

    with capture_http() as router:
        submit = router.post("https://apihub.agnes-ai.com/v1/videos")
        poll = router.get("https://apihub.agnes-ai.com/v1/videos/job-old").mock(
            return_value=httpx.Response(200, json={"status": "completed", "url": "https://cdn.agnes.test/job-old.mp4"})
        )
        download = router.get("https://cdn.agnes.test/job-old.mp4").mock(
            return_value=httpx.Response(200, content=b"resumed")
        )

        await _run_orphan_recovery(session_factory)

    assert submit.call_count == 0
    assert (poll.call_count, download.call_count) == (1, 1)
    row = await _load_task_row(session_factory, "T-agnes-retired")
    assert row.status == "succeeded", row.error_message
    paid_files = sorted((chain_project / "versions" / "videos").glob("E1S01_v*.mp4"))
    assert [path.read_bytes() for path in paid_files] == [b"resumed"]

    await _assert_new_submission_rejected("agnes")


async def test_custom_job_submitted_before_shutdown_still_resumes_after_it(
    chain_project: Path, session_factory, retired_model
):
    """自定义供应商：模型对应的条目关停后，重启续跑关停前已提交的任务照常轮询取回结果，不重新提交；新提交仍报下线。"""
    rows = await _seed_custom_video_rows(session_factory, model_id=RETIRED_MODEL)
    await _seed_running_video_task(
        session_factory,
        task_id="T-ce-retired",
        provider_id=rows["provider_id"],
        checkpoint_json=_storyboard_checkpoint_json(
            "T-ce-retired",
            provider_id=rows["provider_id"],
            endpoint_guard=rows["endpoint_keys"][0],
            model_id=RETIRED_MODEL,
        ),
    )

    with capture_http() as router:
        submit = router.post("https://relay.test/v1/video/create")
        poll = router.get("https://relay.test/v1/video/fetch/job-old").mock(
            return_value=httpx.Response(
                200, json={"status": "completed", "video_url": "https://relay.test/files/job-old.mp4"}
            )
        )
        download = router.get("https://relay.test/files/job-old.mp4").mock(
            return_value=httpx.Response(200, content=b"resumed")
        )

        await _run_orphan_recovery(session_factory)

    assert submit.call_count == 0
    assert (poll.call_count, download.call_count) == (1, 1)
    row = await _load_task_row(session_factory, "T-ce-retired")
    assert row.status == "succeeded", row.error_message

    await _assert_new_submission_rejected(rows["provider_id"])


LEGACY_SEEDANCE_ENDPOINT = "ark-seedance"
SEEDANCE_MODEL = "doubao-seedance-2-0-260128"


async def _seed_relay_model(session_factory, *, endpoint: str, model_id: str) -> str:
    """落一个经内置调用端点 ``endpoint`` 调用 ``model_id`` 的自定义供应商，返回供应商 ID。"""
    async with session_factory() as session:
        provider = await CustomProviderRepository(session).create_provider(
            display_name="方舟中转",
            discovery_format="openai",
            base_url="https://ark-relay.test",
            api_key="sk-secret-key-1234",
            models=[
                {
                    "model_id": model_id,
                    "display_name": model_id,
                    "endpoint": endpoint,
                    "is_enabled": True,
                    "is_default": True,
                }
            ],
        )
        await session.commit()
        return make_provider_id(provider.id)


async def test_seedance_job_submitted_before_endpoint_split_resumes(chain_project: Path, session_factory, monkeypatch):
    """升级前经 ``ark-seedance`` 提交的任务：模型行已回填为协议等价的 ``ark-seedance-2``，续跑照常取回结果、不重新提交。"""
    provider_id = await _seed_relay_model(session_factory, endpoint="ark-seedance-2", model_id=SEEDANCE_MODEL)
    await _seed_running_video_task(
        session_factory,
        task_id="T-seedance-legacy",
        provider_id=provider_id,
        checkpoint_json=_storyboard_checkpoint_json(
            "T-seedance-legacy",
            provider_id=provider_id,
            endpoint_guard=LEGACY_SEEDANCE_ENDPOINT,
            model_id=SEEDANCE_MODEL,
        ),
    )

    def unexpected_preference(*args, **kwargs):
        raise AssertionError("resume must only consume the submitted checkpoint")

    project_file = chain_project / "project.json"
    project = json.loads(project_file.read_text())
    project_file.write_text(json.dumps({**project, "video_generate_audio": False, "default_duration": 17}))
    monkeypatch.setattr(generation_context, "snapshot_video_request_facts", unexpected_preference)
    for method in ("video_generate_audio", "video_generate_audio_for_project"):
        monkeypatch.setattr(f"lib.config.resolver.ConfigResolver.{method}", unexpected_preference)

    with capture_http() as router:
        submit = router.post("https://ark-relay.test/api/v3/contents/generations/tasks")
        poll = router.get("https://ark-relay.test/api/v3/contents/generations/tasks/job-old").mock(
            return_value=httpx.Response(
                200,
                json={
                    "id": "job-old",
                    "model": SEEDANCE_MODEL,
                    "status": "succeeded",
                    "content": {"video_url": "https://cdn.ark.test/job-old.mp4"},
                    "usage": {"completion_tokens": 10, "total_tokens": 10},
                    "created_at": 1,
                    "updated_at": 2,
                },
            )
        )
        download = router.get("https://cdn.ark.test/job-old.mp4").mock(
            return_value=httpx.Response(200, content=b"resumed")
        )

        await _run_orphan_recovery(session_factory)

    assert submit.call_count == 0
    assert (poll.call_count, download.call_count) == (1, 1)
    row = await _load_task_row(session_factory, "T-seedance-legacy")
    assert row.status == "succeeded", row.error_message
    paid_files = sorted((chain_project / "versions" / "videos").glob("E1S01_v*.mp4"))
    assert [path.read_bytes() for path in paid_files] == [b"resumed"]


async def test_seedance_job_rejected_after_switching_protocol(chain_project: Path, session_factory):
    """升级前经 ``ark-seedance`` 提交的任务：模型行在途改挂了别的协议，续跑在发出任何请求前失败。"""
    provider_id = await _seed_relay_model(session_factory, endpoint="openai-video", model_id=SEEDANCE_MODEL)
    await _seed_running_video_task(
        session_factory,
        task_id="T-seedance-switched",
        provider_id=provider_id,
        checkpoint_json=_storyboard_checkpoint_json(
            "T-seedance-switched",
            provider_id=provider_id,
            endpoint_guard=LEGACY_SEEDANCE_ENDPOINT,
            model_id=SEEDANCE_MODEL,
        ),
    )

    with capture_http() as router:
        ark = router.route(host="ark-relay.test")

        await _run_orphan_recovery(session_factory)

    assert ark.call_count == 0
    row = await _load_task_row(session_factory, "T-seedance-switched")
    assert row.status == "failed"
    assert row.error_message is not None
    assert "resume_endpoint_changed" in row.error_message


@pytest.mark.parametrize(
    "endpoint",
    ["kling-video-pro-silent", "kling-video-pro-audio", "kling-video-audio", "kling-video-silent"],
)
async def test_kling_job_submitted_before_split_resumes(chain_project: Path, session_factory, endpoint: str):
    """旧可灵任务可经协议等价后继取回结果，零次重新提交。"""
    model = "kling-v2-6"
    provider_id = await _seed_relay_model(session_factory, endpoint=endpoint, model_id=model)
    await _seed_running_video_task(
        session_factory,
        task_id="T-kling-legacy",
        provider_id=provider_id,
        checkpoint_json=_storyboard_checkpoint_json(
            "T-kling-legacy", provider_id=provider_id, endpoint_guard="kling-video", model_id=model
        ),
    )
    with capture_http() as router:
        submit = router.post("https://ark-relay.test/v1/videos/text2video")
        poll = router.get("https://ark-relay.test/v1/videos/text2video/job-old").mock(
            return_value=httpx.Response(
                200,
                json={
                    "code": 0,
                    "data": {
                        "task_status": "succeed",
                        "task_result": {"videos": [{"url": "https://cdn.kling.test/old.mp4"}]},
                    },
                },
            )
        )
        download = router.get("https://cdn.kling.test/old.mp4").mock(
            return_value=httpx.Response(200, content=b"resumed-kling")
        )
        await _run_orphan_recovery(session_factory)

    assert submit.call_count == 0
    assert (poll.call_count, download.call_count) == (1, 1)
    row = await _load_task_row(session_factory, "T-kling-legacy")
    assert row.status == "succeeded", row.error_message
    paid_files = sorted((chain_project / "versions" / "videos").glob("E1S01_v*.mp4"))
    assert [path.read_bytes() for path in paid_files] == [b"resumed-kling"]


async def test_old_kling_job_rejects_a_different_protocol(chain_project: Path, session_factory):
    provider_id = await _seed_relay_model(session_factory, endpoint="openai-video", model_id="kling-v2-6")
    await _seed_running_video_task(
        session_factory,
        task_id="T-kling-switched",
        provider_id=provider_id,
        checkpoint_json=_storyboard_checkpoint_json(
            "T-kling-switched", provider_id=provider_id, endpoint_guard="kling-video", model_id="kling-v2-6"
        ),
    )
    with capture_http() as router:
        outbound = router.route(host="ark-relay.test")
        await _run_orphan_recovery(session_factory)
    assert outbound.call_count == 0
    row = await _load_task_row(session_factory, "T-kling-switched")
    assert row.status == "failed"
    assert "resume_endpoint_changed" in (row.error_message or "")


@pytest.mark.parametrize("endpoint", ["dashscope-wan3-video", "openai-video"])
async def test_dashscope_legacy_job_resumes_only_on_successor(chain_project: Path, session_factory, endpoint: str):
    provider_id = await _seed_relay_model(session_factory, endpoint=endpoint, model_id="wan3.0-video")
    await _seed_running_video_task(
        session_factory,
        task_id="T-dashscope-legacy",
        provider_id=provider_id,
        checkpoint_json=_storyboard_checkpoint_json(
            "T-dashscope-legacy",
            provider_id=provider_id,
            endpoint_guard="dashscope-async-video",
            model_id="wan3.0-video",
        ),
    )
    with capture_http() as router:
        submit = router.post("https://ark-relay.test/api/v1/services/aigc/video-generation/video-synthesis")
        poll = router.get("https://ark-relay.test/api/v1/tasks/job-old").respond(
            200,
            json={
                "output": {"task_status": "SUCCEEDED", "video_url": "https://cdn.test/legacy.mp4"},
                "usage": {"duration": 5},
            },
        )
        download = router.get("https://cdn.test/legacy.mp4").respond(200, content=b"resumed")
        await _run_orphan_recovery(session_factory)
    row = await _load_task_row(session_factory, "T-dashscope-legacy")
    assert submit.call_count == 0
    if endpoint == "dashscope-wan3-video":
        assert row.status == "succeeded", row.error_message
        assert (poll.call_count, download.call_count) == (1, 1)
        paid_files = sorted((chain_project / "versions" / "videos").glob("E1S01_v*.mp4"))
        assert [path.read_bytes() for path in paid_files] == [b"resumed"]
    else:
        assert row.status == "failed"
        assert row.error_message
        assert "resume_endpoint_changed" in row.error_message
        assert len(router.calls) == 0


@pytest.mark.parametrize(("submitted", "current"), [("always_off", "always_on"), ("always_on", "always_off")])
async def test_resume_settles_frozen_audio_after_override_change(chain_project, session_factory, submitted, current):
    from sqlalchemy import update

    from lib.db.models.api_call import ApiCall
    from lib.db.models.custom_provider import CustomProviderModel
    from lib.db.repositories.usage_repo import UsageRepository

    rows = await _seed_custom_video_rows(session_factory)
    task_id = "T-audio-resume"
    await _seed_running_video_task(
        session_factory,
        task_id=task_id,
        provider_id=rows["provider_id"],
        checkpoint_json=_storyboard_checkpoint_json(
            task_id,
            provider_id=rows["provider_id"],
            endpoint_guard=rows["endpoint_keys"][0],
            audio_track=VideoAudioMode(submitted),
        ),
    )
    async with session_factory() as session:
        await session.execute(update(CustomProviderModel).values(capability_overrides={"audio_track": current}))
        call_id = await UsageRepository(session).start_call(
            project_name="demo",
            call_type="video",
            model="video-x",
            provider=rows["provider_id"],
            task_id=task_id,
            generate_audio=True,
        )
        await session.commit()
    with capture_http() as http:
        submit = http.post("https://relay.test/v1/video/create")
        poll = http.get("https://relay.test/v1/video/fetch/job-old").respond(
            200, json={"status": "completed", "video_url": "https://relay.test/files/job.mp4"}
        )
        download = http.get("https://relay.test/files/job.mp4").respond(200, content=b"paid-result")
        await _run_orphan_recovery(session_factory)
    assert submit.call_count == 0
    assert poll.call_count == download.call_count == 1
    assert (await _load_task_row(session_factory, task_id)).status == "succeeded"
    async with session_factory() as session:
        call = await session.get(ApiCall, call_id)
        assert call is not None
        assert call.status == "success"
        assert call.generate_audio is (submitted == "always_on")
    assert [p.read_bytes() for p in (chain_project / "versions" / "videos").glob("E1S01_v*.mp4")] == [b"paid-result"]
