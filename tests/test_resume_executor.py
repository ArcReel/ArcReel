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


def _patch_resume_executor_deps(monkeypatch, fake_pm: _FakeProjectManager, fake_generator: _FakeGenerator) -> None:
    """同时 patch resume_executor 的 pm/generator 来源——它从 generation_tasks 顶层 re-import。"""
    from server.services import resume_executor

    monkeypatch.setattr(resume_executor, "get_project_manager", lambda: fake_pm)
    monkeypatch.setattr(resume_executor, "get_media_generator", AsyncMock(return_value=fake_generator))
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
async def test_execute_resume_passes_require_image_backend_false(monkeypatch, fake_pm, video_task):
    """resume_executor 应显式 require_image_backend=False —— image 配置坏不影响接续。"""
    from server.services import resume_executor
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

    async def _capturing_get_media_generator(*args: Any, **kwargs: Any) -> Any:
        captured["kwargs"] = kwargs
        return fake_gen

    monkeypatch.setattr(resume_executor, "get_media_generator", _capturing_get_media_generator)

    await execute_resume_video_task(video_task, job_id="openai-job-1")

    assert captured["kwargs"].get("require_image_backend") is False


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
