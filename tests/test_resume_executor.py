"""Resume executor 单元测试。

关注点：
- resume_executor 直接调 generator.resume_video_async（→ backend.resume_video），
  而不是 generate_video_async（→ backend.generate），避免重复扣费。
- 跳过 storyboard / reference 本地文件存在性校验——provider 端 job 已经在跑。
- ResumeExpiredError 沿调用链上抛由 worker mark_failed 时识别。
"""

from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock

import pytest

from lib.video_backends.base import ResumeExpiredError

pytestmark = pytest.mark.unit


class _FakeProjectManager:
    def __init__(self, project_path: Path, project: dict[str, Any]) -> None:
        self.project_path = project_path
        self.project = project
        self.scene_assets: list[dict[str, Any]] = []

    def load_project(self, _project_name: str) -> dict[str, Any]:
        return self.project

    def get_project_path(self, _project_name: str) -> Path:
        return self.project_path

    def update_scene_asset(self, **kwargs: Any) -> None:
        self.scene_assets.append(kwargs)


class _FakeGenerator:
    """模拟 MediaGenerator：resume_video_async 可控、versions 提供历史查询。"""

    def __init__(self, *, raises: Exception | None = None) -> None:
        self.resume_calls: list[dict[str, Any]] = []
        self.raises = raises
        self.versions = self  # 让 generator.versions.get_versions 走自身

    async def resume_video_async(self, **kwargs: Any) -> tuple[Path, int, Any, str | None]:
        self.resume_calls.append(kwargs)
        if self.raises is not None:
            raise self.raises
        output_path = kwargs["output_path"] if "output_path" in kwargs else Path(tempfile.gettempdir()) / "video.mp4"
        return output_path, 3, None, "video-uri-xyz"

    def get_versions(self, _resource_type: str, _resource_id: str) -> dict[str, Any]:
        return {"versions": [{"created_at": "2026-05-26T00:00:00Z"}]}


@pytest.fixture
def fake_pm(tmp_path: Path) -> _FakeProjectManager:
    project_path = tmp_path / "projects" / "demo"
    (project_path / "videos").mkdir(parents=True, exist_ok=True)
    (project_path / "thumbnails").mkdir(parents=True, exist_ok=True)
    return _FakeProjectManager(
        project_path=project_path,
        project={"content_mode": "narration", "default_duration": 8, "aspect_ratio": "9:16"},
    )


@pytest.fixture
def video_task() -> dict[str, Any]:
    return {
        "task_id": "T-1",
        "task_type": "video",
        "media_type": "video",
        "project_name": "demo",
        "resource_id": "E1S01",
        "provider_id": "openai",
        "provider_job_id": "openai-job-1",
        "payload": {"script_file": "episode_1.json", "prompt": "p"},
    }


def _fake_video_context(fake_generator: _FakeGenerator, *, endpoint: str | None = None):
    """把 fake generator 包成 GenerationContext。

    video lane 必须声明：resume 除 generator 外还读 ``ctx.video.endpoint`` 做 endpoint 比对，
    与生产路径（``video=VideoLaneRequest()``）一致。
    """
    from lib.config.resolver import ProviderModel
    from server.services.generation_context import GenerationContext, VideoLaneResult

    return GenerationContext(
        generator=fake_generator,  # type: ignore[arg-type]
        video_lane=VideoLaneResult(
            provider_model=ProviderModel("openai", "sora-2"),
            backend_name="openai",
            backend_model="sora-2",
            resolution="720p",
            resolution_or_fallback="720p",
            supported_durations=(8,),
            max_duration=8,
            max_reference_images=None,
            endpoint=endpoint,
        ),
    )


def _patch_resume_executor_deps(
    monkeypatch,
    fake_pm: _FakeProjectManager,
    fake_generator: _FakeGenerator,
    *,
    endpoint: str | None = None,
) -> None:
    """同时 patch resume_executor 的 pm/generator 来源——它从 generation_tasks 顶层 re-import。"""
    from server.services import resume_executor

    monkeypatch.setattr(resume_executor, "get_project_manager", lambda: fake_pm)
    monkeypatch.setattr(
        resume_executor,
        "resolve_generation_context",
        AsyncMock(return_value=_fake_video_context(fake_generator, endpoint=endpoint)),
    )
    # finalize helpers 内部也通过 generation_tasks/reference_video_tasks 的 get_project_manager
    monkeypatch.setattr("server.services.generation_tasks.get_project_manager", lambda: fake_pm)
    monkeypatch.setattr("server.services.reference_video_tasks.get_project_manager", lambda: fake_pm)

    # extract_video_thumbnail 真实实现走 ffprobe；mock 成 no-op 让 finalize 不依赖外部工具
    async def _fake_thumb(*_args, **_kwargs):
        return False

    monkeypatch.setattr("server.services.generation_tasks.extract_video_thumbnail", _fake_thumb)
    monkeypatch.setattr("server.services.reference_video_tasks.extract_video_thumbnail", _fake_thumb)


@pytest.mark.asyncio
async def test_execute_resume_video_calls_backend_resume_directly(monkeypatch, fake_pm, video_task):
    """resume_executor 调 generator.resume_video_async（间接走 backend.resume_video），而非 generate。"""
    from server.services.resume_executor import execute_resume_video_task

    fake_gen = _FakeGenerator()
    _patch_resume_executor_deps(monkeypatch, fake_pm, fake_gen)

    result = await execute_resume_video_task(video_task, job_id="openai-job-1")

    assert len(fake_gen.resume_calls) == 1
    call = fake_gen.resume_calls[0]
    assert call["job_id"] == "openai-job-1"
    assert call["resource_type"] == "videos"
    assert call["resource_id"] == "E1S01"
    assert call["task_id"] == "T-1"
    # 返回结果带 file_path / resource_type，供 worker mark_succeeded
    assert result["resource_type"] == "videos"
    assert result["file_path"] == "videos/scene_E1S01.mp4"


@pytest.mark.asyncio
async def test_execute_resume_skips_storyboard_check(monkeypatch, fake_pm, video_task):
    """resume 路径不读 storyboard 本地文件——即使 storyboard 不存在也能成功。"""
    from server.services.resume_executor import execute_resume_video_task

    # 故意确保 storyboard 不存在
    storyboard_dir = fake_pm.project_path / "storyboards"
    if storyboard_dir.exists():
        for f in storyboard_dir.glob("*.png"):
            f.unlink()

    fake_gen = _FakeGenerator()
    _patch_resume_executor_deps(monkeypatch, fake_pm, fake_gen)

    # 不应抛 "storyboard not found"
    result = await execute_resume_video_task(video_task, job_id="openai-job-1")
    assert result["file_path"] == "videos/scene_E1S01.mp4"


@pytest.mark.asyncio
async def test_execute_resume_writes_scene_asset(monkeypatch, fake_pm, video_task):
    """resume 成功后写 scene asset（video_clip + video_uri）。"""
    from server.services.resume_executor import execute_resume_video_task

    fake_gen = _FakeGenerator()
    _patch_resume_executor_deps(monkeypatch, fake_pm, fake_gen)

    await execute_resume_video_task(video_task, job_id="openai-job-1")

    asset_types = {a["asset_type"] for a in fake_pm.scene_assets}
    assert "video_clip" in asset_types
    assert "video_uri" in asset_types


@pytest.mark.asyncio
async def test_execute_resume_expired_propagates(monkeypatch, fake_pm, video_task):
    """backend.resume_video raise ResumeExpiredError → resume_executor 不吞，往上抛。"""
    from server.services.resume_executor import execute_resume_video_task

    fake_gen = _FakeGenerator(raises=ResumeExpiredError(job_id="openai-job-1", provider="openai"))
    _patch_resume_executor_deps(monkeypatch, fake_pm, fake_gen)

    with pytest.raises(ResumeExpiredError):
        await execute_resume_video_task(video_task, job_id="openai-job-1")


@pytest.mark.asyncio
async def test_execute_resume_declares_video_lane_only(monkeypatch, fake_pm, video_task):
    """resume_executor 只声明 video lane、不声明 image lane —— 不构造 image backend，
    image 配置坏不影响接续（等价于旧 require_image_backend=False 的意图）。"""
    from server.services import resume_executor
    from server.services.generation_context import VideoLaneRequest
    from server.services.resume_executor import execute_resume_video_task

    fake_gen = _FakeGenerator()
    monkeypatch.setattr(resume_executor, "get_project_manager", lambda: fake_pm)
    monkeypatch.setattr("server.services.generation_tasks.get_project_manager", lambda: fake_pm)
    monkeypatch.setattr("server.services.reference_video_tasks.get_project_manager", lambda: fake_pm)

    async def _fake_thumb(*_args, **_kwargs):
        return False

    monkeypatch.setattr("server.services.generation_tasks.extract_video_thumbnail", _fake_thumb)
    monkeypatch.setattr("server.services.reference_video_tasks.extract_video_thumbnail", _fake_thumb)

    captured: dict[str, Any] = {}

    async def _capturing_resolve_context(*args: Any, **kwargs: Any) -> Any:
        captured["kwargs"] = kwargs
        return _fake_video_context(fake_gen)

    monkeypatch.setattr(resume_executor, "resolve_generation_context", _capturing_resolve_context)

    await execute_resume_video_task(video_task, job_id="openai-job-1")

    # 只声明 video lane（VideoLaneRequest），image/audio lane 未声明 → 不构造对应 backend
    assert isinstance(captured["kwargs"].get("video"), VideoLaneRequest)
    assert captured["kwargs"].get("image") is None
    assert captured["kwargs"].get("audio") is None


@pytest.mark.asyncio
async def test_execute_resume_emits_project_change_batch(monkeypatch, fake_pm, video_task):
    """resume 成功后同步触发 emit_generation_success_batch（推 SSE 给前端）。"""
    from server.services import resume_executor
    from server.services.resume_executor import execute_resume_video_task

    fake_gen = _FakeGenerator()
    _patch_resume_executor_deps(monkeypatch, fake_pm, fake_gen)

    calls: list[dict[str, Any]] = []

    def _capture(**kwargs: Any) -> None:
        calls.append(kwargs)

    monkeypatch.setattr(resume_executor, "emit_generation_success_batch", _capture)

    await execute_resume_video_task(video_task, job_id="openai-job-1")

    assert len(calls) == 1
    call = calls[0]
    assert call["task_type"] == "video"
    assert call["project_name"] == "demo"
    assert call["resource_id"] == "E1S01"


@pytest.mark.asyncio
async def test_execute_resume_failure_does_not_emit(monkeypatch, fake_pm, video_task):
    """resume 抛错时不应 emit batch（finalize 未跑成功）。"""
    from server.services import resume_executor
    from server.services.resume_executor import execute_resume_video_task

    fake_gen = _FakeGenerator(raises=RuntimeError("backend boom"))
    _patch_resume_executor_deps(monkeypatch, fake_pm, fake_gen)

    calls: list[dict[str, Any]] = []
    monkeypatch.setattr(resume_executor, "emit_generation_success_batch", lambda **kwargs: calls.append(kwargs))

    with pytest.raises(RuntimeError):
        await execute_resume_video_task(video_task, job_id="openai-job-1")

    assert calls == []


@pytest.mark.asyncio
async def test_execute_resume_accepts_float_string_duration(monkeypatch, fake_pm):
    """payload.duration_seconds = \"8.0\"（浮点字符串）应被 int(float()) 兜底转 8，不应 ValueError。"""
    from server.services.resume_executor import execute_resume_video_task

    fake_gen = _FakeGenerator()
    _patch_resume_executor_deps(monkeypatch, fake_pm, fake_gen)

    task = {
        "task_id": "T-float",
        "task_type": "video",
        "media_type": "video",
        "project_name": "demo",
        "resource_id": "E1S01",
        "provider_id": "openai",
        "provider_job_id": "openai-job-1",
        "payload": {"script_file": "episode_1.json", "prompt": "p", "duration_seconds": "8.0"},
    }
    # 不应抛 ValueError
    result = await execute_resume_video_task(task, job_id="openai-job-1")
    assert result["resource_type"] == "videos"
    assert fake_gen.resume_calls[0]["duration_seconds"] == 8


@pytest.mark.asyncio
async def test_execute_resume_rejects_image_task(monkeypatch, fake_pm):
    """非 video / reference_video 任务（如 storyboard）不应被派发到 resume—— image 类无 resume 路径。"""
    from server.services.resume_executor import execute_resume_video_task

    fake_gen = _FakeGenerator()
    _patch_resume_executor_deps(monkeypatch, fake_pm, fake_gen)

    image_task = {
        "task_id": "T-img",
        "task_type": "storyboard",
        "media_type": "image",
        "project_name": "demo",
        "resource_id": "E1S01",
        "provider_id": "gemini-aistudio",
        "provider_job_id": "x",
        "payload": {"script_file": "episode_1.json"},
    }
    with pytest.raises(NotImplementedError):
        await execute_resume_video_task(image_task, job_id="x")


# ── endpoint 比对闸 ──────────────────────────────────────────────


@pytest.mark.asyncio
async def test_resume_fails_when_endpoint_changed(monkeypatch, fake_pm, video_task):
    """提交时的 endpoint 与模型行当下的 endpoint 不同 → 显式失败，绝不接续轮询。

    换 endpoint 等于换协议：拿新协议 backend 轮旧协议下创建的 job 会误读响应，把仍在跑
    仍在计费的远端 job 标成失败（docs/adr/0054）。
    """
    from lib.video_backends.base import ResumeEndpointChangedError
    from server.services.resume_executor import execute_resume_video_task

    fake_gen = _FakeGenerator()
    _patch_resume_executor_deps(monkeypatch, fake_pm, fake_gen, endpoint="minimax-video")

    task = {**video_task, "provider_id": "custom-7", "provider_endpoint": "openai-video"}
    with pytest.raises(ResumeEndpointChangedError) as exc_info:
        await execute_resume_video_task(task, job_id="custom-job-1")

    assert exc_info.value.submitted_endpoint == "openai-video"
    assert exc_info.value.current_endpoint == "minimax-video"
    # 核心回归点：没有拿新协议 backend 去轮旧 job
    assert fake_gen.resume_calls == []


@pytest.mark.asyncio
async def test_resume_proceeds_when_endpoint_unchanged(monkeypatch, fake_pm, video_task):
    """endpoint 未变更 → 续跑行为与现状一致。"""
    from server.services.resume_executor import execute_resume_video_task

    fake_gen = _FakeGenerator()
    _patch_resume_executor_deps(monkeypatch, fake_pm, fake_gen, endpoint="openai-video")

    task = {**video_task, "provider_id": "custom-7", "provider_endpoint": "openai-video"}
    await execute_resume_video_task(task, job_id="custom-job-1")

    assert len(fake_gen.resume_calls) == 1


@pytest.mark.asyncio
async def test_resume_proceeds_when_endpoint_not_persisted(monkeypatch, fake_pm, video_task):
    """存量任务未持久化 endpoint（列为 NULL）→ 无从比对，照常接续，不因新增列而回归。"""
    from server.services.resume_executor import execute_resume_video_task

    fake_gen = _FakeGenerator()
    _patch_resume_executor_deps(monkeypatch, fake_pm, fake_gen, endpoint="minimax-video")

    task = {**video_task, "provider_id": "custom-7", "provider_endpoint": None}
    await execute_resume_video_task(task, job_id="custom-job-1")

    assert len(fake_gen.resume_calls) == 1


@pytest.mark.asyncio
async def test_resume_proceeds_for_builtin_provider_without_endpoint(monkeypatch, fake_pm, video_task):
    """内置供应商无 endpoint 维度（两侧皆空）→ 闸不介入。"""
    from server.services.resume_executor import execute_resume_video_task

    fake_gen = _FakeGenerator()
    _patch_resume_executor_deps(monkeypatch, fake_pm, fake_gen, endpoint=None)

    await execute_resume_video_task(video_task, job_id="openai-job-1")

    assert len(fake_gen.resume_calls) == 1


# ── 提交域名回放 ──────────────────────────────────────────────


@pytest.mark.asyncio
async def test_resume_replays_submitted_base_url_for_builtin(monkeypatch, fake_pm, video_task):
    """内置供应商：持久化的提交域名透传给 backend，改配置后续跑仍轮原主机。"""
    from server.services.resume_executor import execute_resume_video_task

    fake_gen = _FakeGenerator()
    _patch_resume_executor_deps(monkeypatch, fake_pm, fake_gen, endpoint=None)

    task = {**video_task, "provider_id": "dashscope", "provider_endpoint": "https://maas-a.example.com/ws-1/api/v1"}
    await execute_resume_video_task(task, job_id="dashscope-job-1")

    assert fake_gen.resume_calls[0]["submitted_base_url"] == "https://maas-a.example.com/ws-1/api/v1"


@pytest.mark.asyncio
async def test_resume_replays_submitted_base_url_with_uppercase_scheme(monkeypatch, fake_pm, video_task):
    """域名形态判别不区分 scheme 大小写：用户填的 base_url 原样落库，大写 scheme 同样是域名。"""
    from server.services.resume_executor import execute_resume_video_task

    fake_gen = _FakeGenerator()
    _patch_resume_executor_deps(monkeypatch, fake_pm, fake_gen, endpoint=None)

    task = {**video_task, "provider_id": "dashscope", "provider_endpoint": "HTTPS://maas-a.example.com/ws-1/api/v1"}
    await execute_resume_video_task(task, job_id="dashscope-job-1")

    assert fake_gen.resume_calls[0]["submitted_base_url"] == "HTTPS://maas-a.example.com/ws-1/api/v1"


@pytest.mark.asyncio
async def test_resume_does_not_replay_endpoint_id_for_custom(monkeypatch, fake_pm, video_task):
    """自定义供应商：列里是 endpoint 标识、归比对闸消费，不当域名回放。"""
    from server.services.resume_executor import execute_resume_video_task

    fake_gen = _FakeGenerator()
    _patch_resume_executor_deps(monkeypatch, fake_pm, fake_gen, endpoint="openai-video")

    task = {**video_task, "provider_id": "custom-7", "provider_endpoint": "openai-video"}
    await execute_resume_video_task(task, job_id="custom-job-1")

    assert fake_gen.resume_calls[0]["submitted_base_url"] is None


@pytest.mark.asyncio
async def test_resume_replays_submitted_base_url_for_custom(monkeypatch, fake_pm, video_task):
    """自定义供应商：域名落在专列，在途改 base_url 后续跑仍按提交时的域名轮询。"""
    from server.services.resume_executor import execute_resume_video_task

    fake_gen = _FakeGenerator()
    _patch_resume_executor_deps(monkeypatch, fake_pm, fake_gen, endpoint="dashscope-async-video")

    task = {
        **video_task,
        "provider_id": "custom-7",
        "provider_endpoint": "dashscope-async-video",
        "submitted_base_url": "https://custom-a.example.com/api/v1",
    }
    await execute_resume_video_task(task, job_id="custom-job-1")

    assert fake_gen.resume_calls[0]["submitted_base_url"] == "https://custom-a.example.com/api/v1"


@pytest.mark.asyncio
async def test_resume_fails_when_custom_endpoint_changed_even_with_base_url(monkeypatch, fake_pm, video_task):
    """协议标识不一致仍显式失败——域名回放不为换协议的续跑开口子。"""
    from lib.video_backends.base import ResumeEndpointChangedError
    from server.services.resume_executor import execute_resume_video_task

    fake_gen = _FakeGenerator()
    _patch_resume_executor_deps(monkeypatch, fake_pm, fake_gen, endpoint="minimax-video")

    task = {
        **video_task,
        "provider_id": "custom-7",
        "provider_endpoint": "dashscope-async-video",
        "submitted_base_url": "https://custom-a.example.com/api/v1",
    }
    with pytest.raises(ResumeEndpointChangedError):
        await execute_resume_video_task(task, job_id="custom-job-1")

    assert fake_gen.resume_calls == []


@pytest.mark.asyncio
async def test_resume_ignores_endpoint_id_left_by_provider_switch(monkeypatch, fake_pm, video_task):
    """任务由自定义供应商提交、模型行在途被改成内置供应商：列里的标识不当域名用。

    拿 endpoint 标识拼 URL 只会把可归因的 404 换成更难归因的连接错误。
    """
    from server.services.resume_executor import execute_resume_video_task

    fake_gen = _FakeGenerator()
    _patch_resume_executor_deps(monkeypatch, fake_pm, fake_gen, endpoint=None)

    task = {**video_task, "provider_id": "dashscope", "provider_endpoint": "openai-video"}
    await execute_resume_video_task(task, job_id="dashscope-job-1")

    assert fake_gen.resume_calls[0]["submitted_base_url"] is None


@pytest.mark.asyncio
async def test_resume_ignores_custom_domain_left_by_provider_switch(monkeypatch, fake_pm, video_task):
    """同一跨类切换下专列里的自定义域名同样不回放：当下是内置供应商，只认 ``provider_endpoint``。

    拿另一个供应商的域名配内置凭据轮询，只会把可归因的 404 换成认证或连接错误。
    """
    from server.services.resume_executor import execute_resume_video_task

    fake_gen = _FakeGenerator()
    _patch_resume_executor_deps(monkeypatch, fake_pm, fake_gen, endpoint=None)

    task = {
        **video_task,
        "provider_id": "dashscope",
        "provider_endpoint": "dashscope-async-video",
        "submitted_base_url": "https://custom-a.example.com/api/v1",
    }
    await execute_resume_video_task(task, job_id="dashscope-job-1")

    assert fake_gen.resume_calls[0]["submitted_base_url"] is None


@pytest.mark.asyncio
async def test_resume_fails_when_builtin_task_switched_to_custom_provider(monkeypatch, fake_pm, video_task):
    """任务由内置供应商提交（列里是域名）、模型行在途被改成自定义供应商：比对闸拦下。

    域名与 endpoint 标识必然不等，闸门据此判定协议已被换掉。内置任务的该列有值后这条路径
    才可达，语义上也确实换了协议——宁可显式失败，也不拿新协议 backend 轮旧 job。
    """
    from lib.video_backends.base import ResumeEndpointChangedError
    from server.services.resume_executor import execute_resume_video_task

    fake_gen = _FakeGenerator()
    _patch_resume_executor_deps(monkeypatch, fake_pm, fake_gen, endpoint="dashscope-async-video")

    task = {**video_task, "provider_id": "dashscope", "provider_endpoint": "https://maas-a.example.com/ws-1/api/v1"}
    with pytest.raises(ResumeEndpointChangedError):
        await execute_resume_video_task(task, job_id="dashscope-job-1")

    assert fake_gen.resume_calls == []
