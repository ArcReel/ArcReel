"""MediaGenerator.resume_video_async 单元测试。

关注点：
- resume 路径不写 ApiCall（不调 start_call / finish_call）
- finalize_pending_by_call_id 按 api_call_id 精准翻 pending → success/failed
- 版本管理用 add_version：resume 成功后总是 bump 新版本，让 versions.json 与磁盘文件一致
  （submit→poll 崩 → 登记 v1；已有 v_n 的覆盖式重新生成 → 登记 v_(n+1)）
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
        self.duration_seconds = 8


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


class _ProbeVideoBackend(_FakeVideoBackend):
    """resume_video 触发时记录当时 add_calls 的快照，用来断言 add_version 在下载之后才发生。"""

    def __init__(self, versions: _FakeVersions) -> None:
        super().__init__()
        self._versions = versions
        self.add_calls_at_resume: int | None = None

    async def resume_video(self, job_id, request):
        self.add_calls_at_resume = len(self._versions.add_calls)
        return await super().resume_video(job_id, request)


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
async def test_resume_calls_add_version_after_download(tmp_path):
    """resume 成功后必须 add_version 记录新版本，且 add_version 发生在 backend.resume_video 之后
    （否则会把残留/旧文件错登记到 versions 表）。同时不应在开头预登记 ensure_current_tracked。"""
    gen = _build_generator(tmp_path)
    probe = _ProbeVideoBackend(gen.versions)
    gen._video_backend = probe

    await gen.resume_video_async(
        job_id="provider-job-1",
        resource_type="videos",
        resource_id="E1S01",
        task_id="T-1",
        api_call_id=42,
    )

    # add_version 必须发生在下载之后：进入 resume_video 时 add_calls 还是 0
    assert probe.add_calls_at_resume == 0
    assert len(gen.versions.add_calls) == 1
    # 不再在开头/末尾调 ensure_current_tracked（避免错位登记残留文件）
    assert gen.versions.ensure_calls == []


@pytest.mark.asyncio
async def test_resume_after_pre_version_crash_creates_v1(tmp_path):
    """submit→poll 中崩 → versions.json 空 → resume 下载新视频后 add_version 登记 v1，
    避免下游 _finalize_video_task 在 versions[-1] 上 IndexError。"""
    gen = _build_generator(tmp_path, initial_version=0)

    _, version, _, _ = await gen.resume_video_async(
        job_id="provider-job-1",
        resource_type="videos",
        resource_id="E1S01",
        task_id="T-1",
        api_call_id=42,
    )

    assert version == 1
    assert len(gen.versions.add_calls) == 1


@pytest.mark.asyncio
async def test_resume_after_version_v1_crash_bumps_to_v2(tmp_path):
    """覆盖式重新生成：versions.json 已有 v1，generate 在 add_version 之前崩。
    resume 下载新视频覆盖 output_path 后必须 bump 到 v2，让 versions.json 与磁盘内容一致；
    否则 versions={v1} 仍指向旧记录而 output_path 已是 v2 内容，回滚/历史会失真。"""
    gen = _build_generator(tmp_path, initial_version=1)

    _, version, _, _ = await gen.resume_video_async(
        job_id="provider-job-1",
        resource_type="videos",
        resource_id="E1S01",
        task_id="T-1",
        api_call_id=42,
    )

    assert version == 2, "已有 v1 + 覆盖式重新生成 → 必须登记 v2"
    assert len(gen.versions.add_calls) == 1


@pytest.mark.asyncio
async def test_resume_handles_float_string_duration(tmp_path):
    """duration_seconds 传浮点字符串（如 "10.0"）时应解析为 int(10)，
    不能被 try/except 静默吞成兜底值 8（int("10.0") 会 ValueError）。
    add_version 与 VideoGenerationRequest 都应收到归一后的 int。"""
    gen = _build_generator(tmp_path, initial_version=1)

    await gen.resume_video_async(
        job_id="provider-job-1",
        resource_type="videos",
        resource_id="E1S01",
        duration_seconds="10.0",
        task_id="T-1",
        api_call_id=42,
    )

    # add_version 应该收到 duration_seconds=10（int），不是 "10.0" 也不是 8
    assert len(gen.versions.add_calls) == 1
    add_call = gen.versions.add_calls[0]
    assert add_call["duration_seconds"] == 10
    assert isinstance(add_call["duration_seconds"], int)

    # provider 请求里的 duration_seconds 也应是 int(10)
    backend = gen._video_backend
    assert len(backend.calls) == 1
    _, request = backend.calls[0]
    assert request.duration_seconds == 10
    assert isinstance(request.duration_seconds, int), "归一后类型必须是 int 而非 str/float"


@pytest.mark.asyncio
async def test_resume_passes_usage_tokens_to_finalize(tmp_path):
    """Ark 视频按 usage_tokens 计费，缺省为 0 会导致 cost 永远为 0；
    resume 路径必须把 backend.resume_video 返回的 usage_tokens 透传到 finalize_pending_by_call_id，
    与 generate 路径 finish_call(..., usage_tokens=...) 等价记账。"""

    class _ArkLikeResult:
        def __init__(self) -> None:
            self.video_uri = "video-uri-resume"
            self.usage_tokens = 12345  # 模拟 Ark 返回的 completion_tokens
            self.generate_audio = True
            self.duration_seconds = 8

    class _ArkLikeBackend:
        name = "ark"
        model = "doubao-seedance-1-0-pro"

        def __init__(self) -> None:
            self.calls: list[Any] = []

        async def generate(self, request):
            raise AssertionError("generate 不应被 resume 路径调用")

        async def resume_video(self, job_id, request):
            self.calls.append((job_id, request))
            request.output_path.parent.mkdir(parents=True, exist_ok=True)
            request.output_path.write_bytes(b"fake-resume-video")
            return _ArkLikeResult()

    gen = _build_generator(tmp_path)
    gen._video_backend = _ArkLikeBackend()

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
    assert call["usage_tokens"] == 12345, "usage_tokens 必须透传，否则 Ark cost 永远为 0"


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


class _FailingFinalizeUsage(_FakeUsage):
    """模拟 finalize_pending_by_call_id 自身失败的场景。"""

    def __init__(self, *, exc: Exception) -> None:
        super().__init__()
        self._exc = exc

    async def finalize_pending_by_call_id(self, **kwargs):
        self.finalized.append(kwargs)
        raise self._exc


@pytest.mark.asyncio
async def test_resume_success_propagates_finalize_exception(tmp_path):
    """finalize_pending_by_call_id(success) 异常必须冒泡，不能被吞掉。

    吞掉会让对应的 ApiCall 永远卡在 pending（success 分支无后续重试，
    expired 分支又是终态），usage 报表和补账会出现永久缺口。fail-fast
    交给 worker finally 的 mark_failed 兜底。"""
    gen = _build_generator(tmp_path)
    gen.usage_tracker = _FailingFinalizeUsage(exc=RuntimeError("db down"))

    with pytest.raises(RuntimeError, match="db down"):
        await gen.resume_video_async(
            job_id="provider-job-1",
            resource_type="videos",
            resource_id="E1S01",
            task_id="T-1",
            api_call_id=42,
        )

    # finalize 被调过一次（看到异常上抛前的入参）
    assert len(gen.usage_tracker.finalized) == 1


@pytest.mark.asyncio
async def test_resume_expired_propagates_finalize_exception(tmp_path):
    """ResumeExpiredError 分支同样不能吞 finalize 异常：让 worker finally 兜底标记
    失败，避免 ApiCall 永远卡 pending。"""
    gen = _build_generator(tmp_path)
    gen._video_backend = _FakeVideoBackend(raises=ResumeExpiredError(job_id="provider-job-1", provider="openai"))
    gen.usage_tracker = _FailingFinalizeUsage(exc=RuntimeError("db down"))

    # finalize 抛 RuntimeError 应该覆盖原本要抛的 ResumeExpiredError 上抛
    with pytest.raises(RuntimeError, match="db down"):
        await gen.resume_video_async(
            job_id="provider-job-1",
            resource_type="videos",
            resource_id="E1S01",
            task_id="T-1",
            api_call_id=42,
        )

    assert len(gen.usage_tracker.finalized) == 1


@pytest.mark.asyncio
async def test_resume_passes_generate_audio_to_finalize(tmp_path):
    """provider 在 submit 后可能降级/关闭音频；resume 必须把 backend 返回的
    generate_audio 透传到 finalize_pending_by_call_id，避免 cost 沿用请求值误计费
    （与 generate 路径 finish_call(generate_audio=result.generate_audio) 等价）。"""

    class _AudioDowngradeResult:
        def __init__(self) -> None:
            self.video_uri = "video-uri-resume"
            self.usage_tokens = 1234
            self.generate_audio = False  # provider 实际降级到无音频
            self.duration_seconds = 8

    class _AudioDowngradeBackend:
        name = "fake"
        model = "video-model"

        def __init__(self) -> None:
            self.calls: list[Any] = []

        async def generate(self, request):
            raise AssertionError("generate 不应被 resume 路径调用")

        async def resume_video(self, job_id, request):
            self.calls.append((job_id, request))
            request.output_path.parent.mkdir(parents=True, exist_ok=True)
            request.output_path.write_bytes(b"fake-resume-video")
            return _AudioDowngradeResult()

    gen = _build_generator(tmp_path)
    gen._video_backend = _AudioDowngradeBackend()

    await gen.resume_video_async(
        job_id="provider-job-1",
        resource_type="videos",
        resource_id="E1S01",
        task_id="T-1",
        api_call_id=42,
    )

    assert len(gen.usage_tracker.finalized) == 1
    call = gen.usage_tracker.finalized[0]
    assert call["generate_audio"] is False, "generate_audio 必须透传 backend 返回的实际值"


@pytest.mark.asyncio
async def test_resume_passes_billed_duration_to_finalize(tmp_path):
    """DashScope 的 resume 与 generate 走同一段 poll，result.duration_seconds 可能是
    含输入参考视频时长的实际计费时长；resume 必须把它透传到 finalize_pending_by_call_id，
    与 generate 路径 finish_call(billed_duration_seconds=...) 等价记账。"""

    class _BilledDurationResult:
        def __init__(self) -> None:
            self.video_uri = "video-uri-resume"
            self.usage_tokens = 0
            self.generate_audio = True
            self.duration_seconds = 15  # 请求 8 秒，provider 按 15 秒计费

    class _BilledDurationBackend:
        name = "dashscope"
        model = "wan2.7-r2v"

        def __init__(self) -> None:
            self.calls: list[Any] = []

        async def generate(self, request):
            raise AssertionError("generate 不应被 resume 路径调用")

        async def resume_video(self, job_id, request):
            self.calls.append((job_id, request))
            request.output_path.parent.mkdir(parents=True, exist_ok=True)
            request.output_path.write_bytes(b"fake-resume-video")
            return _BilledDurationResult()

    gen = _build_generator(tmp_path)
    gen._video_backend = _BilledDurationBackend()

    await gen.resume_video_async(
        job_id="provider-job-1",
        resource_type="videos",
        resource_id="E1S01",
        duration_seconds="8",
        task_id="T-1",
        api_call_id=42,
    )

    assert len(gen.usage_tracker.finalized) == 1
    call = gen.usage_tracker.finalized[0]
    assert call["billed_duration_seconds"] == 15, "实际计费时长必须透传，否则 resume 路径回落请求时长记账"
