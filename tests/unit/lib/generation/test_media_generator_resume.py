"""MediaGenerator.resume_video_async unit tests.

Focus:
- Resume creates no pending call rows/accounting brackets;
- Resolve unsettled calls by api_calls.task_id; ledger.resume_success/resume_failed precisely settle pending rows;
- Successful resume saves paid versions: formal staging callbacks atomically choose current,
  while nonformal requests append/select directly;
- ResumeExpiredError propagates and marks pending failed.
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import pytest

from arcreel_market_core.video_backend_contract import ResumeExpiredError
from lib.generation.media_generator import MediaGenerator
from tests.fakes import FakeConfigResolver, select_formal_video, write_backend_output, write_generation_targets


class _FakeVideoResult:
    def __init__(
        self,
        *,
        seed: int | None = None,
        provenance: dict[str, Any] | None = None,
        warnings: tuple[dict[str, Any], ...] = (),
    ) -> None:
        self.video_uri = "video-uri-resume"
        self.usage_tokens = 0
        self.generate_audio = True
        self.duration_seconds = 8
        self.seed = seed
        self.provenance = provenance
        self.warnings = warnings


class _FakeVideoBackend:
    name = "fake-video"
    model = "video-model"

    def __init__(self, *, raises: Exception | None = None, result: _FakeVideoResult | None = None) -> None:
        self.calls: list[Any] = []
        self.raises = raises
        self._result = result

    async def generate(self, request):
        raise AssertionError("generate 不应被 resume 路径调用")

    async def resume_video(self, job_id, request):
        self.calls.append((job_id, request))
        if self.raises is not None:
            raise self.raises
        await write_backend_output(request.output_path, b"fake-resume-video")
        return self._result if self._result is not None else _FakeVideoResult()


class _FakeVersions:
    """VersionManager substitute for ensure_current_tracked/add_version/get_current_version."""

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
    """Snapshot add_calls when resume_video starts, asserting add_version occurs only after downloads."""

    def __init__(self, versions: _FakeVersions) -> None:
        super().__init__()
        self._versions = versions
        self.add_calls_at_resume: int | None = None

    async def resume_video(self, job_id, request):
        self.add_calls_at_resume = len(self._versions.add_calls)
        return await super().resume_video(job_id, request)


class _FakeLedgerCall:
    def __init__(self, call_id: int) -> None:
        self.call_id = call_id
        self.declared = False
        self.result: Any = None
        self.usage: Any = None

    def success(self, result: Any, *, usage: Any = None) -> None:
        self.declared = True
        self.result = result
        self.usage = usage


class _FakeLedger:
    """Ledger substitute: resume uses resume_success/resume_failed only, never accounting brackets.

    ``started`` captures mistakenly opened brackets and stays empty; ``resumed`` captures resume
    settlement arguments, including backend results, as the primary seam. ``lookups`` records
    task-based call lookups.
    """

    def __init__(self, *, pending_call_id: int | None = 42) -> None:
        self.started: list[dict[str, Any]] = []
        self.resumed: list[dict[str, Any]] = []
        self.lookups: list[str] = []
        self._pending_call_id = pending_call_id
        self._n = 0
        self._resume_affected = 1

    async def pending_call_id_for_task(self, task_id: str) -> int | None:
        self.lookups.append(task_id)
        return self._pending_call_id

    @asynccontextmanager
    async def record(self, **kwargs):
        self._n += 1
        self.started.append(kwargs)
        call = _FakeLedgerCall(self._n)
        try:
            yield call
        except asyncio.CancelledError:
            raise
        except Exception:
            raise

    async def resume_success(
        self, *, call_id: int, result: Any, usage: Any = None, service_tier: str = "default"
    ) -> int:
        self.resumed.append(
            {"status": "success", "call_id": call_id, "result": result, "usage": usage, "service_tier": service_tier}
        )
        return self._resume_affected

    async def resume_failed(self, *, call_id: int, failure: BaseException | str | None = None) -> int:
        self.resumed.append({"status": "failed", "call_id": call_id, "failure": failure})
        return self._resume_affected


def _build_generator(tmp_path: Path, *, initial_version: int = 0) -> MediaGenerator:
    gen = object.__new__(MediaGenerator)
    gen.project_path = tmp_path / "projects" / "demo"
    gen.project_path.mkdir(parents=True, exist_ok=True)
    write_generation_targets(gen.project_path, segments=("E1S01",), video_units=("E1U1",))
    gen.project_name = "demo"
    gen._rate_limiter = None
    gen._image_backend = None
    gen._video_backend = _FakeVideoBackend()
    gen._user_id = "default"
    gen._config = FakeConfigResolver()
    gen.versions = _FakeVersions(initial_version=initial_version)
    gen.ledger = _FakeLedger()
    return gen


@pytest.mark.asyncio
async def test_resume_does_not_open_record_bracket(tmp_path):
    gen = _build_generator(tmp_path)

    await gen.resume_video_async(
        job_id="provider-job-1",
        resource_type="videos",
        resource_id="E1S01",
        task_id="T-1",
    )

    # Resume creates no pending rows/brackets; settle existing calls through resume_success/resume_failed only.
    assert gen.ledger.started == [], "resume 不应开记账括号"


@pytest.mark.asyncio
async def test_resume_success_flips_pending_apicall_by_call_id(tmp_path):
    gen = _build_generator(tmp_path)

    await gen.resume_video_async(
        job_id="provider-job-1",
        resource_type="videos",
        resource_id="E1S01",
        task_id="T-1",
    )

    assert len(gen.ledger.resumed) == 1
    call = gen.ledger.resumed[0]
    assert call["call_id"] == 42
    assert call["status"] == "success"
    # Successful settlement omits cost_amount, letting repositories auto-calculate actual cost from ApiCall fields
    # like generate accounting; cost_amount never enters resume_success arguments.
    assert "cost_amount" not in call


@pytest.mark.asyncio
async def test_resume_idempotent_when_finalize_returns_zero(tmp_path, caplog):
    gen = _build_generator(tmp_path)
    gen.ledger._resume_affected = 0  # Simulate already-successful idempotency.

    output_path, version, _, _ = await gen.resume_video_async(
        job_id="provider-job-1",
        resource_type="videos",
        resource_id="E1S01",
        task_id="T-1",
    )

    assert output_path.exists()
    assert version == 1
    # Zero rows never raises; logger.info records it.
    assert len(gen.ledger.resumed) == 1


@pytest.mark.asyncio
async def test_resume_expired_flips_pending_to_failed(tmp_path):
    gen = _build_generator(tmp_path)
    expired = ResumeExpiredError(job_id="provider-job-1", provider="openai")
    gen._video_backend = _FakeVideoBackend(raises=expired)

    with pytest.raises(ResumeExpiredError):
        await gen.resume_video_async(
            job_id="provider-job-1",
            resource_type="videos",
            resource_id="E1S01",
            task_id="T-1",
        )

    assert len(gen.ledger.resumed) == 1
    call = gen.ledger.resumed[0]
    assert call["call_id"] == 42
    assert call["status"] == "failed"
    # Pass expiry exceptions into settlement; ledgers encode original failures/machine codes internally, so pin only passing them.
    assert call["failure"] is expired
    # No duplicate-charge semantics live in ledger.resume_failed (cost_amount=0.0), not explicit settlement arguments.


@pytest.mark.asyncio
async def test_resume_other_exception_does_not_finalize(tmp_path):
    """Non-ResumeExpiredError, such as download timeout, leaves pending unchanged for worker retries."""
    gen = _build_generator(tmp_path)
    gen._video_backend = _FakeVideoBackend(raises=RuntimeError("network timeout"))

    with pytest.raises(RuntimeError):
        await gen.resume_video_async(
            job_id="provider-job-1",
            resource_type="videos",
            resource_id="E1S01",
            task_id="T-1",
        )

    assert gen.ledger.resumed == [], "非 ResumeExpired 不应 finalize pending"


@pytest.mark.asyncio
async def test_resume_calls_add_version_after_download(tmp_path):
    """Successful resume records add_version only after backend.resume_video, avoiding stale-file version registration;
    never pre-register ensure_current_tracked at entry."""
    gen = _build_generator(tmp_path)
    probe = _ProbeVideoBackend(gen.versions)
    gen._video_backend = probe

    await gen.resume_video_async(
        job_id="provider-job-1",
        resource_type="videos",
        resource_id="E1S01",
        task_id="T-1",
    )

    # At resume_video entry add_calls remains 0; add_version follows download.
    assert probe.add_calls_at_resume == 0
    assert len(gen.versions.add_calls) == 1
    # Never call ensure_current_tracked at start/end, avoiding leftover-file misregistration.
    assert gen.versions.ensure_calls == []


@pytest.mark.asyncio
async def test_resume_after_pre_version_crash_creates_v1(tmp_path):
    """Crash during submit/poll with empty versions.json: resumed downloads add v1, preventing finalize_video_task versions[-1] IndexError."""
    gen = _build_generator(tmp_path, initial_version=0)

    _, version, _, _ = await gen.resume_video_async(
        job_id="provider-job-1",
        resource_type="videos",
        resource_id="E1S01",
        task_id="T-1",
    )

    assert version == 1
    assert len(gen.versions.add_calls) == 1


@pytest.mark.asyncio
async def test_resume_after_version_v1_crash_bumps_to_v2(tmp_path):
    """Overwrite regeneration starts with v1 and crashes before add_version. Resume downloads over
    output_path, then must bump v2, aligning history/disk. Otherwise v1 records point to v2 bytes,
    corrupting rollback/history semantics."""
    gen = _build_generator(tmp_path, initial_version=1)

    _, version, _, _ = await gen.resume_video_async(
        job_id="provider-job-1",
        resource_type="videos",
        resource_id="E1S01",
        task_id="T-1",
    )

    assert version == 2, "已有 v1 + 覆盖式重新生成 → 必须登记 v2"
    assert len(gen.versions.add_calls) == 1


@pytest.mark.asyncio
async def test_resume_formal_output_uses_same_staged_version_transaction(tmp_path):
    from lib.artifacts.version_manager import VersionManager

    gen = _build_generator(tmp_path)
    gen.versions = VersionManager(gen.project_path)
    current = gen._get_output_path("reference_videos", "E1U1")
    current.parent.mkdir(parents=True)
    current.write_bytes(b"old-current")

    events = []

    async def _prepare(staged_file, duration_seconds, version_metadata):
        assert await asyncio.to_thread(staged_file.read_bytes) == b"fake-resume-video"
        assert duration_seconds == 8
        events.append("prepared")

    def _commit(*args):
        events.append("committed")
        return select_formal_video(gen)(*args)

    output, version, _, _ = await gen.resume_video_async(
        job_id="provider-job-1",
        resource_type="reference_videos",
        resource_id="E1U1",
        task_id="T-1",
        formal_output=True,
        before_formal_commit=_prepare,
        commit_formal_output=_commit,
        execution_request_digest="d" * 64,
    )

    assert output.read_bytes() == b"fake-resume-video"
    assert version == 2
    history = gen.versions.get_versions("reference_videos", "E1U1")
    assert [item["prompt"] for item in history["versions"]] == ["", ""]
    assert history["versions"][-1]["execution_request_digest"] == "d" * 64
    assert events == ["prepared", "committed"]


@pytest.mark.asyncio
async def test_resume_formal_prepare_failure_archives_paid_history_without_selecting_it(tmp_path):
    from lib.artifacts.version_manager import VersionManager

    gen = _build_generator(tmp_path)
    gen.versions = VersionManager(gen.project_path)
    current = gen._get_output_path("reference_videos", "E1U1")
    current.parent.mkdir(parents=True)
    current.write_bytes(b"old-current")

    async def _fail_prepare(*_args):
        raise RuntimeError("resume paid output validation failed")

    with pytest.raises(RuntimeError, match="resume paid output validation failed"):
        await gen.resume_video_async(
            job_id="provider-job-1",
            resource_type="reference_videos",
            resource_id="E1U1",
            task_id="T-1",
            formal_output=True,
            before_formal_commit=_fail_prepare,
        )

    assert current.read_bytes() == b"old-current"
    history = gen.versions.get_versions("reference_videos", "E1U1")
    assert history["current_version"] == 1
    assert len(history["versions"]) == 2
    assert history["versions"][-1]["is_current"] is False
    assert (gen.project_path / history["versions"][-1]["file"]).read_bytes() == b"fake-resume-video"


@pytest.mark.asyncio
async def test_resume_handles_float_string_duration(tmp_path):
    """Float-string duration_seconds such as "10.0" normalizes to int(10), not silently caught fallback 8
    from int("10.0") ValueError. Both add_version and VideoGenerationRequest receive integers."""
    gen = _build_generator(tmp_path, initial_version=1)

    await gen.resume_video_async(
        job_id="provider-job-1",
        resource_type="videos",
        resource_id="E1S01",
        duration_seconds="10.0",
        task_id="T-1",
    )

    # add_version receives duration_seconds=10 int, not "10.0" or 8.
    assert len(gen.versions.add_calls) == 1
    add_call = gen.versions.add_calls[0]
    assert add_call["duration_seconds"] == 10
    assert isinstance(add_call["duration_seconds"], int)

    # Provider duration_seconds also receives int(10).
    backend = gen._video_backend
    assert len(backend.calls) == 1
    _, request = backend.calls[0]
    assert request.duration_seconds == 10
    assert isinstance(request.duration_seconds, int), "归一后类型必须是 int 而非 str/float"


@pytest.mark.asyncio
async def test_resume_passes_usage_tokens_to_finalize(tmp_path):
    """Ark bills usage_tokens, whose default 0 would permanently zero costs. Resume must forward
    backend.resume_video usage_tokens to finalize_pending_by_call_id, equivalent to generate
    finish_call(..., usage_tokens=...) accounting."""

    class _ArkLikeResult:
        def __init__(self) -> None:
            self.video_uri = "video-uri-resume"
            self.usage_tokens = 12345  # Simulate Ark completion_tokens.
            self.generate_audio = True
            self.duration_seconds = 8
            self.seed = None
            self.provenance = None
            self.warnings = ()

    class _ArkLikeBackend:
        name = "ark"
        model = "doubao-seedance-1-0-pro"

        def __init__(self) -> None:
            self.calls: list[Any] = []

        async def generate(self, request):
            raise AssertionError("generate 不应被 resume 路径调用")

        async def resume_video(self, job_id, request):
            self.calls.append((job_id, request))
            await write_backend_output(request.output_path, b"fake-resume-video")
            return _ArkLikeResult()

    gen = _build_generator(tmp_path)
    gen._video_backend = _ArkLikeBackend()

    await gen.resume_video_async(
        job_id="provider-job-1",
        resource_type="videos",
        resource_id="E1S01",
        task_id="T-1",
    )

    assert len(gen.ledger.resumed) == 1
    call = gen.ledger.resumed[0]
    assert call["call_id"] == 42
    assert call["status"] == "success"
    assert call["result"].usage_tokens == 12345, "backend 结果对象必须递交，usage_tokens 由 ledger 分发透传"


@pytest.mark.asyncio
async def test_resume_without_settleable_call_row_warns_does_not_crash(tmp_path, caplog):
    """Missing task-linked unsettled calls still allow successful resume, with warnings only."""
    gen = _build_generator(tmp_path)
    gen.ledger = _FakeLedger(pending_call_id=None)

    output_path, version, _, _ = await gen.resume_video_async(
        job_id="provider-job-1",
        resource_type="videos",
        resource_id="E1S01",
        task_id="T-1",
    )

    assert output_path.exists()
    assert version == 1
    assert gen.ledger.resumed == [], "反查不到调用行时不应 finalize"


@pytest.mark.asyncio
async def test_resume_looks_up_settleable_call_by_task_id(tmp_path):
    """Resolve calls by task identity, never caller passthrough; api_calls.task_id solely links tasks/calls."""
    gen = _build_generator(tmp_path)

    await gen.resume_video_async(
        job_id="provider-job-1",
        resource_type="videos",
        resource_id="E1S01",
        task_id="T-1",
    )

    assert gen.ledger.lookups == ["T-1"]
    assert gen.ledger.resumed[0]["call_id"] == 42


class _FailingResumeLedger(_FakeLedger):
    """Simulate failing resume settlement/finalize itself."""

    def __init__(self, *, exc: Exception) -> None:
        super().__init__()
        self._exc = exc

    async def resume_success(
        self, *, call_id: int, result: Any, usage: Any = None, service_tier: str = "default"
    ) -> int:
        self.resumed.append({"status": "success", "call_id": call_id})
        raise self._exc

    async def resume_failed(self, *, call_id: int, failure: BaseException | str | None = None) -> int:
        self.resumed.append({"status": "failed", "call_id": call_id, "failure": failure})
        raise self._exc


@pytest.mark.asyncio
async def test_resume_success_propagates_finalize_exception(tmp_path):
    """finalize_pending_by_call_id(success) failures must propagate.

    Swallowing them leaves ApiCall pending forever: success has no later retry and expiry is
    terminal. Usage reports/settlement would have permanent gaps; fail fast for worker finally
    mark_failed fallback.
    """
    gen = _build_generator(tmp_path)
    gen.ledger = _FailingResumeLedger(exc=RuntimeError("db down"))

    with pytest.raises(RuntimeError, match="db down"):
        await gen.resume_video_async(
            job_id="provider-job-1",
            resource_type="videos",
            resource_id="E1S01",
            task_id="T-1",
        )

    # finalize ran once; capture arguments before propagation.
    assert len(gen.ledger.resumed) == 1


@pytest.mark.asyncio
async def test_resume_formal_output_cleans_staging_when_finalize_fails(tmp_path):
    from lib.artifacts.version_manager import VersionManager

    gen = _build_generator(tmp_path)
    gen.versions = VersionManager(gen.project_path)
    gen.ledger = _FailingResumeLedger(exc=RuntimeError("db down"))
    current = gen._get_output_path("reference_videos", "E1U1")
    current.parent.mkdir(parents=True)
    current.write_bytes(b"old-current")

    with pytest.raises(RuntimeError, match="db down"):
        await gen.resume_video_async(
            job_id="provider-job-1",
            resource_type="reference_videos",
            resource_id="E1U1",
            task_id="T-1",
            formal_output=True,
        )

    assert current.read_bytes() == b"old-current"
    assert list(current.parent.glob(".E1U1.*.mp4")) == []


@pytest.mark.asyncio
async def test_resume_expired_propagates_finalize_exception(tmp_path):
    """Expiry branches also propagate finalize failures so worker finally marks failed, avoiding permanently pending ApiCall."""
    gen = _build_generator(tmp_path)
    gen._video_backend = _FakeVideoBackend(raises=ResumeExpiredError(job_id="provider-job-1", provider="openai"))
    gen.ledger = _FailingResumeLedger(exc=RuntimeError("db down"))

    # finalize RuntimeError supersedes the intended ResumeExpiredError.
    with pytest.raises(RuntimeError, match="db down"):
        await gen.resume_video_async(
            job_id="provider-job-1",
            resource_type="videos",
            resource_id="E1S01",
            task_id="T-1",
        )

    assert len(gen.ledger.resumed) == 1


@pytest.mark.asyncio
async def test_resume_expired_cleans_formal_staging_when_finalize_fails(tmp_path):
    from lib.artifacts.version_manager import VersionManager

    class _WriteThenExpireBackend(_FakeVideoBackend):
        async def resume_video(self, job_id, request):
            await write_backend_output(request.output_path, b"partial-download")
            raise ResumeExpiredError(job_id=job_id, provider="openai")

    gen = _build_generator(tmp_path)
    gen.versions = VersionManager(gen.project_path)
    gen._video_backend = _WriteThenExpireBackend()
    gen.ledger = _FailingResumeLedger(exc=RuntimeError("db down"))
    current = gen._get_output_path("reference_videos", "E1U1")
    current.parent.mkdir(parents=True)
    current.write_bytes(b"old-current")

    with pytest.raises(RuntimeError, match="db down"):
        await gen.resume_video_async(
            job_id="provider-job-1",
            resource_type="reference_videos",
            resource_id="E1U1",
            task_id="T-1",
            formal_output=True,
        )

    assert current.read_bytes() == b"old-current"
    assert list(current.parent.glob(".E1U1.*.mp4")) == []


@pytest.mark.asyncio
async def test_resume_cancellation_cleans_partial_formal_staging(tmp_path):
    from lib.artifacts.version_manager import VersionManager

    class _WriteThenCancelBackend(_FakeVideoBackend):
        async def resume_video(self, job_id, request):
            await write_backend_output(request.output_path, b"partial-download")
            raise asyncio.CancelledError

    gen = _build_generator(tmp_path)
    gen.versions = VersionManager(gen.project_path)
    gen._video_backend = _WriteThenCancelBackend()
    current = gen._get_output_path("reference_videos", "E1U1")
    current.parent.mkdir(parents=True)
    current.write_bytes(b"old-current")

    with pytest.raises(asyncio.CancelledError):
        await gen.resume_video_async(
            job_id="provider-job-1",
            resource_type="reference_videos",
            resource_id="E1U1",
            task_id="T-1",
            formal_output=True,
        )

    assert current.read_bytes() == b"old-current"
    assert list(current.parent.glob(".E1U1.*.mp4")) == []


@pytest.mark.asyncio
async def test_resume_passes_generate_audio_to_finalize(tmp_path):
    """Providers may disable/downgrade audio after submission; resume forwards returned generate_audio
    to finalize_pending_by_call_id, preventing request-based misbilling, like generate
    finish_call(generate_audio=result.generate_audio)."""

    class _AudioDowngradeResult:
        def __init__(self) -> None:
            self.video_uri = "video-uri-resume"
            self.usage_tokens = 1234
            self.generate_audio = False  # Providers actually downgraded to silent.
            self.duration_seconds = 8
            self.seed = None
            self.provenance = None
            self.warnings = ()

    class _AudioDowngradeBackend:
        name = "fake"
        model = "video-model"

        def __init__(self) -> None:
            self.calls: list[Any] = []

        async def generate(self, request):
            raise AssertionError("generate 不应被 resume 路径调用")

        async def resume_video(self, job_id, request):
            self.calls.append((job_id, request))
            await write_backend_output(request.output_path, b"fake-resume-video")
            return _AudioDowngradeResult()

    gen = _build_generator(tmp_path)
    gen._video_backend = _AudioDowngradeBackend()

    await gen.resume_video_async(
        job_id="provider-job-1",
        resource_type="videos",
        resource_id="E1S01",
        task_id="T-1",
    )

    assert len(gen.ledger.resumed) == 1
    call = gen.ledger.resumed[0]
    assert call["result"].generate_audio is False, "backend 结果对象必须递交，generate_audio 由 ledger 分发透传"


@pytest.mark.asyncio
async def test_resume_passes_billed_duration_to_finalize(tmp_path):
    """DashScope resume/generate share polling; result.duration_seconds may include input-reference
    billing duration. Resume forwards it to finalize_pending_by_call_id, equivalent to generate
    finish_call(billed_duration_seconds=...) accounting."""

    class _BilledDurationResult:
        def __init__(self) -> None:
            self.video_uri = "video-uri-resume"
            self.usage_tokens = 0
            self.generate_audio = True
            self.duration_seconds = 15  # Requested 8 seconds, provider bills 15.
            self.seed = None
            self.provenance = None
            self.warnings = ()

    class _BilledDurationBackend:
        name = "dashscope"
        model = "wan2.7-r2v"

        def __init__(self) -> None:
            self.calls: list[Any] = []

        async def generate(self, request):
            raise AssertionError("generate 不应被 resume 路径调用")

        async def resume_video(self, job_id, request):
            self.calls.append((job_id, request))
            await write_backend_output(request.output_path, b"fake-resume-video")
            return _BilledDurationResult()

    gen = _build_generator(tmp_path)
    gen._video_backend = _BilledDurationBackend()

    await gen.resume_video_async(
        job_id="provider-job-1",
        resource_type="videos",
        resource_id="E1S01",
        duration_seconds="8",
        task_id="T-1",
    )

    assert len(gen.ledger.resumed) == 1
    call = gen.ledger.resumed[0]
    assert call["result"].duration_seconds == 15, "backend 结果对象必须递交，实际计费时长由 ledger 分发透传"


@pytest.mark.asyncio
async def test_resume_forwards_submitted_base_url_to_request(tmp_path):
    """Submission domains enter backend requests but never version metadata.

    These are replay values for polling; putting them into versions.json would misclassify
    connection parameters as version properties.
    """
    gen = _build_generator(tmp_path)
    backend = gen._video_backend

    await gen.resume_video_async(
        job_id="provider-job-1",
        resource_type="videos",
        resource_id="E1S01",
        task_id="T-1",
        submitted_base_url="https://maas-a.example.com/ws-1/api/v1",
    )

    _, request = backend.calls[0]
    assert request.submitted_base_url == "https://maas-a.example.com/ws-1/api/v1"
    assert all("submitted_base_url" not in kwargs for kwargs in gen.versions.add_calls)


@pytest.mark.asyncio
async def test_resume_records_the_actual_seed_and_provenance_like_a_first_run(tmp_path):
    """One task retains identical version metadata across restarts.

    Other metadata finalizes before submission, but actual seeds/workflow fingerprints exist only
    after backends finish. Merging them only on initial runs would make version provenance depend
    on process-restart timing.
    """
    gen = _build_generator(tmp_path)
    gen._video_backend = _FakeVideoBackend(result=_FakeVideoResult(seed=4242, provenance={"workflow_sha256": "abc123"}))

    await gen.resume_video_async(
        job_id="provider-job-1",
        resource_type="videos",
        resource_id="E1S01",
        task_id="T-1",
        seed=7,
    )

    recorded = gen.versions.add_calls[-1]
    assert recorded["seed"] == 4242
    assert recorded["workflow_sha256"] == "abc123"


@pytest.mark.asyncio
async def test_resume_hands_backend_warnings_to_the_caller(tmp_path):
    """Resume warnings share initial-run channels so identical events remain visible after resumption."""
    warning = {"key": "comfyui_multiple_outputs", "params": {"count": 2, "filename": "final.mp4"}}
    gen = _build_generator(tmp_path)
    gen._video_backend = _FakeVideoBackend(result=_FakeVideoResult(warnings=(warning,)))
    collected: list[dict[str, Any]] = []

    await gen.resume_video_async(
        job_id="provider-job-1",
        resource_type="videos",
        resource_id="E1S01",
        task_id="T-1",
        warnings=collected,
    )

    assert collected == [warning]
