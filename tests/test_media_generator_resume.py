"""MediaGenerator.resume_video_async 单元测试。

关注点：
- resume 路径不写 ApiCall（不调 start_call / finish_call）
- finalize_pending_by_call_id 按 api_call_id 精准翻 pending → success/failed
- 版本管理用 ensure_current_tracked（重启崩溃边界场景补登记 v1，已有版本时跳过）
- ResumeExpiredError 沿调用链上抛，pending 翻 failed
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from lib.media_generator import MediaGenerator
from lib.video_backends.base import ResumeExpiredError


class _FakeVideoResult:
    def __init__(self) -> None:
        self.video_uri = "video-uri-resume"
        self.usage_tokens = 0
        self.generate_audio = True


class _FakeVideoBackend:
    name = "fake-video"
    model = "video-model"

    def __init__(self, *, raises: Exception | None = None) -> None:
        self.calls: list[Any] = []
        self.raises = raises

    async def generate(self, request):
        raise AssertionError("generate 不应被 resume 路径调用")

    async def resume_video(self, job_id, request):
        self.calls.append((job_id, request))
        if self.raises is not None:
            raise self.raises
        request.output_path.parent.mkdir(parents=True, exist_ok=True)
        request.output_path.write_bytes(b"fake-resume-video")
        return _FakeVideoResult()


class _FakeVersions:
    """模拟 VersionManager 的 ensure_current_tracked / add_version / get_current_version。"""

    def __init__(self, *, initial_version: int = 0) -> None:
        self._version = initial_version
        self.ensure_calls: list[dict[str, Any]] = []
        self.add_calls: list[dict[str, Any]] = []

    def ensure_current_tracked(self, **kwargs):
        self.ensure_calls.append(kwargs)
        if self._version <= 0:
            self._version = 1
            self.add_calls.append(kwargs)
            return self._version
        return None

    def add_version(self, **kwargs):
        self.add_calls.append(kwargs)
        self._version += 1
        return self._version

    def get_current_version(self, _resource_type, _resource_id):
        return self._version


class _FakeUsage:
    def __init__(self) -> None:
        self.started: list[dict[str, Any]] = []
        self.finished: list[dict[str, Any]] = []
        self.finalized: list[dict[str, Any]] = []
        self._finalize_affected = 1

    async def start_call(self, **kwargs):
        self.started.append(kwargs)
        return 999

    async def finish_call(self, **kwargs):
        self.finished.append(kwargs)

    async def finalize_pending_by_call_id(self, **kwargs):
        self.finalized.append(kwargs)
        return self._finalize_affected


class _FakeConfigResolver:
    async def video_generate_audio(self, _project_name=None):
        return True


def _build_generator(tmp_path: Path, *, initial_version: int = 0) -> MediaGenerator:
    gen = object.__new__(MediaGenerator)
    gen.project_path = tmp_path / "projects" / "demo"
    gen.project_path.mkdir(parents=True, exist_ok=True)
    gen.project_name = "demo"
    gen._rate_limiter = None
    gen._image_backend = None
    gen._video_backend = _FakeVideoBackend()
    gen._user_id = "default"
    gen._config = _FakeConfigResolver()
    gen.versions = _FakeVersions(initial_version=initial_version)
    gen.usage_tracker = _FakeUsage()
    return gen


@pytest.mark.asyncio
async def test_resume_does_not_call_usage_tracker_start_or_finish(tmp_path):
    gen = _build_generator(tmp_path)

    await gen.resume_video_async(
        job_id="provider-job-1",
        resource_type="videos",
        resource_id="E1S01",
        task_id="T-1",
        api_call_id=42,
    )

    assert gen.usage_tracker.started == [], "resume 不应调 start_call"
    assert gen.usage_tracker.finished == [], "resume 不应调 finish_call"


@pytest.mark.asyncio
async def test_resume_success_flips_pending_apicall_by_call_id(tmp_path):
    gen = _build_generator(tmp_path)

    await gen.resume_video_async(
        job_id="provider-job-1",
        resource_type="videos",
        resource_id="E1S01",
        task_id="T-1",
        api_call_id=42,
    )

    assert len(gen.usage_tracker.finalized) == 1
    call = gen.usage_tracker.finalized[0]
    assert call["call_id"] == 42
    assert call["status"] == "success"
    # success 路径不显式传 cost_amount，让 repo 按 ApiCall 行字段 auto-calc 算实际 cost
    # （与 generate 路径 finish_call 等价记账），caller 端不再硬编码 0.0
    assert "cost_amount" not in call or call["cost_amount"] is None


@pytest.mark.asyncio
async def test_resume_idempotent_when_finalize_returns_zero(tmp_path, caplog):
    gen = _build_generator(tmp_path)
    gen.usage_tracker._finalize_affected = 0  # 模拟「已 success」幂等场景

    output_path, version, _, _ = await gen.resume_video_async(
        job_id="provider-job-1",
        resource_type="videos",
        resource_id="E1S01",
        task_id="T-1",
        api_call_id=42,
    )

    assert output_path.exists()
    assert version == 1
    # 0 rows 不应抛异常，应当 logger.info 记录
    assert len(gen.usage_tracker.finalized) == 1


@pytest.mark.asyncio
async def test_resume_expired_flips_pending_to_failed(tmp_path):
    gen = _build_generator(tmp_path)
    gen._video_backend = _FakeVideoBackend(raises=ResumeExpiredError(job_id="provider-job-1", provider="openai"))

    with pytest.raises(ResumeExpiredError):
        await gen.resume_video_async(
            job_id="provider-job-1",
            resource_type="videos",
            resource_id="E1S01",
            task_id="T-1",
            api_call_id=42,
        )

    assert len(gen.usage_tracker.finalized) == 1
    call = gen.usage_tracker.finalized[0]
    assert call["call_id"] == 42
    assert call["status"] == "failed"
    assert call["cost_amount"] == 0.0


@pytest.mark.asyncio
async def test_resume_other_exception_does_not_finalize(tmp_path):
    """非 ResumeExpiredError（如下载超时）不翻 pending，留给 worker 重试机制处理。"""
    gen = _build_generator(tmp_path)
    gen._video_backend = _FakeVideoBackend(raises=RuntimeError("network timeout"))

    with pytest.raises(RuntimeError):
        await gen.resume_video_async(
            job_id="provider-job-1",
            resource_type="videos",
            resource_id="E1S01",
            task_id="T-1",
            api_call_id=42,
        )

    assert gen.usage_tracker.finalized == [], "非 ResumeExpired 不应 finalize pending"


@pytest.mark.asyncio
async def test_resume_uses_ensure_current_tracked(tmp_path):
    """resume 用 ensure_current_tracked，不调 add_version。"""
    gen = _build_generator(tmp_path)

    await gen.resume_video_async(
        job_id="provider-job-1",
        resource_type="videos",
        resource_id="E1S01",
        task_id="T-1",
        api_call_id=42,
    )

    # ensure_current_tracked 至少被调一次（末尾必调；开头 output_path.exists() 时也会调）
    assert len(gen.versions.ensure_calls) >= 1


@pytest.mark.asyncio
async def test_resume_after_pre_version_crash_creates_v1(tmp_path):
    """submit→poll 中崩 → versions.json 空 → resume 后补登记 v1 避免 finalize IndexError。"""
    gen = _build_generator(tmp_path, initial_version=0)

    _, version, _, _ = await gen.resume_video_async(
        job_id="provider-job-1",
        resource_type="videos",
        resource_id="E1S01",
        task_id="T-1",
        api_call_id=42,
    )

    assert version == 1
    assert len(gen.versions.add_calls) >= 1


@pytest.mark.asyncio
async def test_resume_after_post_version_crash_does_not_bump(tmp_path):
    """poll 成功→mark_succeeded 前崩 → versions 已有 v1 → resume 不重复 bump。"""
    gen = _build_generator(tmp_path, initial_version=1)

    _, version, _, _ = await gen.resume_video_async(
        job_id="provider-job-1",
        resource_type="videos",
        resource_id="E1S01",
        task_id="T-1",
        api_call_id=42,
    )

    assert version == 1, "已有 v1 时 ensure 应跳过，不重复 bump"
    # ensure_current_tracked 仍被调（但内部 short-circuit），add_calls 不增
    assert len(gen.versions.add_calls) == 0


@pytest.mark.asyncio
async def test_resume_handles_float_string_duration(tmp_path):
    """duration_seconds 传浮点字符串（如 "10.0"）时应解析为 int(10)，
    不能被 try/except 静默吞成兜底值 8（int("10.0") 会 ValueError）。
    两次 ensure_current_tracked 与 VideoGenerationRequest 都应收到归一后的 int。"""
    gen = _build_generator(tmp_path, initial_version=1)  # 已有 v1：开头 ensure 也会触发

    # 预先放文件让开头 output_path.exists() 分支也走 ensure；用真实 _get_output_path
    pre_path = gen._get_output_path("videos", "E1S01")
    pre_path.parent.mkdir(parents=True, exist_ok=True)
    pre_path.write_bytes(b"pre-existing")

    await gen.resume_video_async(
        job_id="provider-job-1",
        resource_type="videos",
        resource_id="E1S01",
        duration_seconds="10.0",
        task_id="T-1",
        api_call_id=42,
    )

    # 两次 ensure_current_tracked 都应该收到 duration_seconds=10（int），不是 "10.0" 也不是 8
    assert len(gen.versions.ensure_calls) == 2
    for call in gen.versions.ensure_calls:
        assert call["duration_seconds"] == 10
        assert isinstance(call["duration_seconds"], int)

    # provider 请求里的 duration_seconds 也应是 int(10)
    backend = gen._video_backend
    assert len(backend.calls) == 1
    _, request = backend.calls[0]
    assert request.duration_seconds == 10


@pytest.mark.asyncio
async def test_resume_missing_api_call_id_warns_does_not_crash(tmp_path, caplog):
    """旧任务 task.payload 无 api_call_id → resume 仍成功，仅 warning。"""
    gen = _build_generator(tmp_path)

    output_path, version, _, _ = await gen.resume_video_async(
        job_id="provider-job-1",
        resource_type="videos",
        resource_id="E1S01",
        task_id="T-1",
        api_call_id=None,
    )

    assert output_path.exists()
    assert version == 1
    assert gen.usage_tracker.finalized == [], "无 api_call_id 时不应 finalize"
