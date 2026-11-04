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
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import httpx
import pytest

from lib.artifacts.artifact_manifest import ArtifactBasis, compose_video_artifact_basis
from lib.artifacts.video_artifact_facts import VideoArtifactCurrencyFacts
from lib.catalog.errors import ModelDiscontinuedError
from lib.catalog.query import factory_snapshot
from lib.config.service import ConfigService
from lib.custom_provider import make_endpoint_key, make_provider_id
from lib.custom_provider.path_overrides import endpoint_guard
from lib.db.models.task import Task
from lib.db.repositories.custom_endpoint_repo import CustomEndpointRepository
from lib.db.repositories.custom_provider_repo import CustomProviderRepository
from lib.generation.generation_queue import GenerationQueue
from lib.generation.generation_worker import GenerationWorker
from lib.project.project_manager import ProjectManager
from lib.script.reference_video.execution_checkpoint import (
    StagedProviderMedia,
    StoryboardSubmissionCheckpoint,
)
from server.services.tasks import generation_context
from server.services.tasks.generation_context import VideoLaneRequest, resolve_generation_context
from server.services.tasks.generation_tasks import execute_generation_task
from server.services.tasks.resume_executor import execute_resume_video_task
from tests.catalog_support import install_catalog, set_catalog_today, with_entry_changes
from tests.factories import custom_endpoint_definition
from tests.fakes import bind_safe_session_factory, bind_session_factory, use_video_thumbnail_extractor
from tests.http_capture import capture_http


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
async def chain_project(session_factory, tmp_path: Path, monkeypatch) -> Path:
    """整链共用环境：全局 session 工厂指向方言敏感测试库 + tmp 真 ProjectManager。

    环境阻碍逐项处理，业务链路不打桩：
    - resume 链路上两个全局 session 工厂（``resolve_generation_context`` 函数内
      导入的 ``lib.db.async_session_factory``、worker 自开 session 用的
      ``safe_session_factory``）都指到测试库；``safe_session_factory``
      单例内部经 ``lib.db.engine.async_session_factory`` 开 session，一并指过去。
    - 准入文件锁落 ``app_data_dir()``，隔离到 tmp。
    - ``get_project_manager`` 在链路各消费模块（resolver / generation_context /
      resume_executor / finalize helpers / worker 清理）逐点换成 tmp 下的真
      ProjectManager——resolver 等在模块顶部绑定了该名字，只 patch 定义处不生效。
    - 缩略图抽取走 ffmpeg 子进程，替换为 no-op 保持测试封闭。
    """
    bind_session_factory(monkeypatch, session_factory)
    bind_safe_session_factory(monkeypatch, session_factory)
    monkeypatch.setattr("lib.db.engine.async_session_factory", session_factory)
    monkeypatch.setenv("ARCREEL_DATA_DIR", str(tmp_path / "appdata"))

    pm = ProjectManager(tmp_path)
    for target in (
        "lib.project.project_manager.get_project_manager",
        "lib.config.resolver.get_project_manager",
        "lib.generation.generation_worker.get_project_manager",
        "lib.generation.video_resume.get_project_manager",
        "server.services.tasks.generation_context.get_project_manager",
        "server.services.tasks.resume_executor.get_project_manager",
        "server.services.tasks.generation_tasks.get_project_manager",
        "server.services.tasks.reference_video_tasks.get_project_manager",
    ):
        monkeypatch.setattr(target, lambda: pm)
    project_dir = tmp_path / "projects" / "demo"
    (project_dir / "scripts").mkdir(parents=True)
    (project_dir / "project.json").write_text(
        json.dumps(
            {
                "content_mode": "narration",
                "default_duration": 8,
                "aspect_ratio": "9:16",
                "episodes": [{"episode": 1, "script_file": "scripts/episode_1.json"}],
            }
        ),
        encoding="utf-8",
    )
    (project_dir / "scripts" / "episode_1.json").write_text(
        json.dumps(
            {
                "episode": 1,
                "content_mode": "narration",
                "segments": [{"segment_id": "E1S01", "novel_text": "n"}],
            }
        ),
        encoding="utf-8",
    )

    async def _no_thumbnail(*_args: Any, **_kwargs: Any) -> bool:
        return False

    use_video_thumbnail_extractor(monkeypatch, _no_thumbnail)
    return project_dir


async def _seed_custom_video_rows(
    session_factory,
    *,
    endpoint_count: int = 1,
    model_endpoint_index: int = 0,
    path_overrides: dict[str, str] | None = None,
    model_id: str = "video-x",
) -> dict:
    """落自定义供应商三件套：端点定义、供应商、挂端点键的视频模型行。

    ``endpoint_count`` > 1 时额外建端点行；``model_endpoint_index`` 决定模型行
    挂哪个端点键——guard 用例据此制造「模型行端点 ≠ checkpoint 冻结端点」。
    ``path_overrides`` 是模型行当前的路径覆盖；``model_id`` 与目录条目同名时按名字对应到该条目。
    """
    async with session_factory() as session:
        endpoint_repo = CustomEndpointRepository(session)
        endpoint_keys: list[str] = []
        for index in range(endpoint_count):
            endpoint = await endpoint_repo.create(
                definition=custom_endpoint_definition(),
                kind="declarative",
                schema_version="1.0.0",
                media_type="video",
                display_name=f"示例端点 {index}",
            )
            endpoint_keys.append(make_endpoint_key(endpoint.id))
        provider = await CustomProviderRepository(session).create_provider(
            display_name="中转站",
            discovery_format="openai",
            base_url="https://relay.test",
            api_key="sk-secret-key-1234",
            models=[
                {
                    "model_id": model_id,
                    "display_name": model_id,
                    "endpoint": endpoint_keys[model_endpoint_index],
                    "is_enabled": True,
                    "is_default": True,
                    "path_overrides": path_overrides,
                }
            ],
        )
        await session.commit()
        return {"provider_id": make_provider_id(provider.id), "endpoint_keys": endpoint_keys}


def _storyboard_checkpoint_json(
    task_id: str, *, provider_id: str, endpoint_guard: str | None, model_id: str = "video-x"
) -> str:
    """真实 ``StoryboardSubmissionCheckpoint.create(...).to_json()``：身份指向 ``provider_id`` 的 ``model_id``。"""
    visual = ArtifactBasis.build(
        "artifact-visual/video-storyboard",
        kind_version=1,
        inputs={
            "resource_id": "E1S01",
            "visual_prompt": {"action": "Run.", "camera_motion": "Static"},
            "canvas": {"aspect_ratio": "9:16"},
            "frames": [{"role": "storyboard", "sha256": "a" * 64}],
        },
    )
    speech = ArtifactBasis.build("artifact-speech/video", kind_version=1, inputs={"mode": "silent"})
    duration = ArtifactBasis.build(
        "artifact-speech/video-duration",
        kind_version=1,
        inputs={"request_duration_seconds": 8},
    )
    return StoryboardSubmissionCheckpoint.create(
        task_id=task_id,
        project_name="demo",
        script_file="scripts/episode_1.json",
        unit_id="E1S01",
        generation_type="i2v",
        provider_id=provider_id,
        provider_model_id=model_id,
        backend_model_id=model_id,
        endpoint_guard=endpoint_guard,
        prompt="frozen",
        duration_seconds=8,
        aspect_ratio="9:16",
        resolution="720p",
        generate_audio=True,
        service_tier="default",
        seed=None,
        visual_basis_digest="b" * 64,
        artifact_currency=VideoArtifactCurrencyFacts(
            episode=1,
            request_duration_seconds=8,
            visual_basis=visual,
            speech_basis=speech,
            duration_basis=duration,
            video_basis=compose_video_artifact_basis(visual=visual, speech=speech, duration=duration),
            voice_style_speakers=(),
            duration_tiers=(8,),
            reference_image_limit=None,
            parent_version=0,
        ),
        media=(
            StagedProviderMedia(
                index=0,
                role="start_image",
                logical_type="storyboard",
                logical_name="E1S01",
                kind="first_frame",
                source_locator="storyboards/scene_E1S01.png",
                staged_locator=f".arcreel/tasks/{task_id}/provider_media/000-start_image.png",
                sha256="c" * 64,
                size_bytes=1,
            ),
        ),
        reference_audio_targets=None,
    ).to_json()


async def _seed_running_video_task(
    session_factory,
    *,
    task_id: str,
    provider_id: str,
    checkpoint_json: str,
    submitted_base_url: str | None = None,
) -> None:
    """种一行已提交（有 job_id + checkpoint）的 running 视频任务，供孤儿扫描认领。"""
    now = datetime.now(UTC)
    async with session_factory() as session:
        session.add(
            Task(
                task_id=task_id,
                project_name="demo",
                task_type="video",
                media_type="video",
                resource_id="E1S01",
                script_file="scripts/episode_1.json",
                status="running",
                provider_id=provider_id,
                provider_job_id="job-old",
                submitted_base_url=submitted_base_url,
                execution_checkpoint_json=checkpoint_json,
                queued_at=now,
                started_at=now,
                updated_at=now,
            )
        )
        await session.commit()


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
    dispatcher = worker._recovery.orphan_dispatcher_task
    assert dispatcher is not None
    await asyncio.wait_for(dispatcher, timeout=10)


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

    # resume 不重复提交计费：submit 零请求，接续只按原 job 轮询并取回产物；
    # 轮询走历史提交域名，不落到供应商行的当前域名。
    assert submit.call_count == 0
    assert poll_current.call_count == 0
    assert poll.call_count == 1
    assert download.call_count == 1

    row = await _load_task_row(session_factory, "T-ce-resume")
    assert row.status == "succeeded"
    result = json.loads(row.result_json or "{}")
    assert result["resource_id"] == "E1S01"
    # 付费字节落入版本历史（当前项目态与提交冻结态不一致时按史存不选中，任务仍成功）。
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

    # 逐字比对失败在 backend 调用之前：poll/submit 都不该有流量。
    assert submit.call_count == 0
    assert poll.call_count == 0

    row = await _load_task_row(session_factory, "T-ce-rebound")
    assert row.status == "failed"
    assert row.error_message is not None
    assert "resume_endpoint_changed" in row.error_message
    # 无产物落盘。
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


#: 关停日期之后续跑的模型：内置 agnes 收录它，自定义供应商里同名的模型按名字对应到同一个条目。
RETIRED_MODEL = "agnes-video-v2.0"
SHUTDOWN = date(2026, 11, 24)


@pytest.fixture
def retired_model(monkeypatch) -> None:
    """``RETIRED_MODEL`` 的条目定在 ``SHUTDOWN`` 关停，今天就是关停日：任务在此之前已提交，重启后才续跑。"""
    install_catalog(
        monkeypatch,
        with_entry_changes(
            factory_snapshot(),
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

    # 续跑构造的 backend 已进实例缓存，新提交也不能经缓存绕过下线检查。
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


#: 端点拆分前的方舟 Seedance 端点名：升级前提交的任务把它冻结在检查点里。
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


async def test_seedance_job_submitted_before_endpoint_split_resumes(chain_project: Path, session_factory):
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
