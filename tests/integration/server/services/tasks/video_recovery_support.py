from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from arcreel_market_core.video_backend_contract import VideoAudioMode
from lib.artifacts.artifact_manifest import ArtifactBasis, compose_video_artifact_basis
from lib.artifacts.video_artifact_facts import VideoArtifactCurrencyFacts
from lib.custom_provider import make_endpoint_key, make_provider_id
from lib.custom_provider.endpoint_resolution import derive_mirror_columns
from lib.db.models.task import Task
from lib.db.repositories.custom_endpoint_repo import CustomEndpointRepository
from lib.db.repositories.custom_provider_repo import CustomProviderRepository
from lib.project.project_manager import ProjectManager
from lib.script.reference_video.execution_checkpoint import (
    StagedProviderMedia,
    StoryboardSubmissionCheckpoint,
)
from tests.factories import custom_endpoint_definition
from tests.fakes import bind_safe_session_factory, bind_session_factory, use_video_thumbnail_extractor


def configure_chain_project(session_factory, tmp_path: Path, monkeypatch) -> Path:
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
        "lib.generation.generation_worker.get_project_manager",
        "lib.generation.restart_recovery.get_project_manager",
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


async def seed_custom_video_rows(
    session_factory,
    *,
    definition: dict | None = None,
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
        endpoint_definition = definition if definition is not None else custom_endpoint_definition()
        mirror = derive_mirror_columns(endpoint_definition)
        for _ in range(endpoint_count):
            endpoint = await endpoint_repo.create(
                definition=endpoint_definition,
                kind=mirror.kind,
                schema_version=mirror.schema_version,
                media_type=mirror.media_type,
                display_name=mirror.display_name,
            )
            endpoint_keys.append(make_endpoint_key(endpoint.id))
        provider = await CustomProviderRepository(session).create_provider(
            display_name="中转站",
            discovery_format="openai",
            base_url="https://relay.test",
            api_key="test-only",
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


def storyboard_checkpoint_json(
    task_id: str,
    *,
    provider_id: str,
    endpoint_guard: str | None,
    model_id: str = "video-x",
    audio_track: VideoAudioMode | None = None,
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
        audio_track=audio_track,
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


async def seed_running_video_task(
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
