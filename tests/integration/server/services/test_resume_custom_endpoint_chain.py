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
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
import pytest

from lib.artifact_manifest import ArtifactBasis, compose_video_artifact_basis
from lib.custom_provider import make_endpoint_key, make_provider_id
from lib.db.models.task import Task
from lib.db.repositories.custom_endpoint_repo import CustomEndpointRepository
from lib.db.repositories.custom_provider_repo import CustomProviderRepository
from lib.generation_queue import GenerationQueue
from lib.generation_worker import GenerationWorker
from lib.project_manager import ProjectManager
from lib.reference_video.execution_checkpoint import (
    NarrationExecutionFacts,
    StagedProviderMedia,
    StoryboardSubmissionCheckpoint,
)
from lib.video_artifact_facts import VideoArtifactCurrencyFacts
from server.services import generation_context
from tests.factories import custom_endpoint_definition
from tests.http_capture import capture_http


@pytest.fixture(autouse=True)
def clean_backend_cache():
    """除清空缓存条目外，同时清空 per-key 锁。

    pytest-asyncio 按测试函数切换独立事件循环，跨测试复用同一缓存 key 时，
    前一个测试触发过竞争的 ``asyncio.Lock`` 会永久绑定在已关闭的旧循环上，
    后续测试再次竞争即抛 ``RuntimeError``。清空 locks 不影响被测生产行为。
    """
    generation_context.invalidate_backend_cache()
    generation_context._backend_cache._locks.clear()
    yield
    generation_context.invalidate_backend_cache()
    generation_context._backend_cache._locks.clear()


@pytest.fixture
async def chain_project(session_factory, tmp_path: Path, monkeypatch) -> Path:
    """整链共用环境：全局 session 工厂指向方言敏感测试库 + tmp 真 ProjectManager。

    环境阻碍逐项处理，业务链路不打桩：
    - resume 链路上两个全局 session 工厂（``resolve_generation_context`` 函数内
      导入的 ``lib.db.async_session_factory``、worker 侧晚导入的
      ``lib.db.safe_session_factory``）都指到测试库；``safe_session_factory``
      单例内部经 ``lib.db.engine.async_session_factory`` 开 session，一并指过去。
    - 准入文件锁落 ``app_data_dir()``，隔离到 tmp。
    - ``get_project_manager`` 在链路各消费模块（resolver / generation_context /
      resume_executor / finalize helpers / worker 清理）逐点换成 tmp 下的真
      ProjectManager——resolver 等在模块顶部绑定了该名字，只 patch 定义处不生效。
    - 缩略图抽取走 ffprobe 子进程，替换为 no-op 保持测试封闭。
    """
    monkeypatch.setattr("lib.db.async_session_factory", session_factory)
    monkeypatch.setattr("lib.db.safe_session_factory", session_factory)
    monkeypatch.setattr("lib.db.engine.async_session_factory", session_factory)
    monkeypatch.setenv("ARCREEL_DATA_DIR", str(tmp_path / "appdata"))

    pm = ProjectManager(tmp_path / "projects")
    for target in (
        "lib.project_manager.get_project_manager",
        "lib.config.resolver.get_project_manager",
        "server.services.generation_context.get_project_manager",
        "server.services.resume_executor.get_project_manager",
        "server.services.generation_tasks.get_project_manager",
        "server.services.reference_video_tasks.get_project_manager",
    ):
        monkeypatch.setattr(target, lambda: pm)
    project_dir = tmp_path / "projects" / "demo"
    (project_dir / "scripts").mkdir(parents=True)
    (project_dir / "project.json").write_text(
        json.dumps({"content_mode": "narration", "default_duration": 8, "aspect_ratio": "9:16"}),
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

    monkeypatch.setattr("server.services.generation_tasks.extract_video_thumbnail", _no_thumbnail)
    monkeypatch.setattr("server.services.reference_video_tasks.extract_video_thumbnail", _no_thumbnail)
    return project_dir


async def _seed_custom_video_rows(session_factory, *, endpoint_count: int = 1, model_endpoint_index: int = 0) -> dict:
    """落自定义供应商三件套：端点定义、供应商、挂端点键的视频模型行。

    ``endpoint_count`` > 1 时额外建端点行；``model_endpoint_index`` 决定模型行
    挂哪个端点键——guard 用例据此制造「模型行端点 ≠ checkpoint 冻结端点」。
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
                    "model_id": "video-x",
                    "display_name": "video-x",
                    "endpoint": endpoint_keys[model_endpoint_index],
                    "is_enabled": True,
                    "is_default": True,
                }
            ],
        )
        await session.commit()
        return {"provider_id": make_provider_id(provider.id), "endpoint_keys": endpoint_keys}


def _storyboard_checkpoint_json(task_id: str, *, provider_id: str, endpoint_guard: str) -> str:
    """真实 ``StoryboardSubmissionCheckpoint.create(...).to_json()``：身份指向自定义供应商行。"""
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
        capability="i2v",
        provider_id=provider_id,
        provider_model_id="video-x",
        backend_model_id="video-x",
        endpoint_guard=endpoint_guard,
        api_call_id=7,
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
        # delivery=post_production：resume 不声明 audio lane，链路不触碰 TTS。
        narration=NarrationExecutionFacts(
            delivery="post_production",
            tts_status="not_applicable",
            artifact_path="",
            basis_digest=None,
            actual_duration_seconds=None,
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
    worker = GenerationWorker(queue=GenerationQueue(session_factory=session_factory))
    await worker._handle_orphan_tasks_on_start()
    dispatcher = worker._orphan_dispatcher_task
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
