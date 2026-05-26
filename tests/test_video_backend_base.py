import logging
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy.exc import OperationalError

from lib.video_backends.base import (
    VideoCapability,
    VideoGenerationRequest,
    VideoGenerationResult,
    persist_job_id_if_in_task_context,
    poll_with_retry,
    reset_current_task_id,
    set_current_task_id,
)


class TestVideoCapability:
    def test_enum_values(self):
        assert VideoCapability.TEXT_TO_VIDEO == "text_to_video"
        assert VideoCapability.IMAGE_TO_VIDEO == "image_to_video"
        assert VideoCapability.GENERATE_AUDIO == "generate_audio"
        assert VideoCapability.NEGATIVE_PROMPT == "negative_prompt"
        assert VideoCapability.VIDEO_EXTEND == "video_extend"
        assert VideoCapability.SEED_CONTROL == "seed_control"
        assert VideoCapability.FLEX_TIER == "flex_tier"

    def test_enum_is_str(self):
        assert isinstance(VideoCapability.TEXT_TO_VIDEO, str)


class TestVideoGenerationRequest:
    def test_defaults(self):
        req = VideoGenerationRequest(prompt="test", output_path=Path("/tmp/out.mp4"))
        assert req.aspect_ratio == "9:16"
        assert req.duration_seconds == 5
        assert req.resolution is None
        assert req.start_image is None
        assert req.generate_audio is True
        assert req.service_tier == "default"
        assert req.seed is None

    def test_all_fields(self):
        req = VideoGenerationRequest(
            prompt="action",
            output_path=Path("/tmp/out.mp4"),
            aspect_ratio="16:9",
            duration_seconds=8,
            resolution="720p",
            start_image=Path("/tmp/frame.png"),
            generate_audio=False,
            service_tier="flex",
            seed=42,
        )
        assert req.duration_seconds == 8
        assert req.seed == 42
        assert req.service_tier == "flex"


class TestVideoGenerationResult:
    def test_required_fields(self):
        result = VideoGenerationResult(
            video_path=Path("/tmp/out.mp4"),
            provider="gemini",
            model="veo-3.1-generate-001",
            duration_seconds=8,
        )
        assert result.video_uri is None
        assert result.seed is None
        assert result.usage_tokens is None
        assert result.task_id is None

    def test_optional_fields(self):
        result = VideoGenerationResult(
            video_path=Path("/tmp/out.mp4"),
            provider="ark",
            model="doubao-seedance-1-5-pro-251215",
            duration_seconds=5,
            video_uri="https://cdn.example.com/video.mp4",
            seed=58944,
            usage_tokens=246840,
            task_id="cgt-20250101",
        )
        assert result.usage_tokens == 246840
        assert result.task_id == "cgt-20250101"


class TestPollWithRetry:
    """poll_with_retry 通用轮询辅助函数测试。"""

    async def test_immediate_done(self):
        """poll_fn 首次返回即完成。"""
        poll_fn = AsyncMock(return_value="done_result")

        with patch("lib.video_backends.base.asyncio.sleep", new_callable=AsyncMock):
            result = await poll_with_retry(
                poll_fn=poll_fn,
                is_done=lambda r: r == "done_result",
                is_failed=lambda r: None,
                poll_interval=1,
                max_wait=10,
            )

        assert result == "done_result"
        assert poll_fn.await_count == 1

    async def test_polls_until_done(self):
        """多次轮询后完成。"""
        poll_fn = AsyncMock(side_effect=["pending", "pending", "done"])

        with patch("lib.video_backends.base.asyncio.sleep", new_callable=AsyncMock):
            result = await poll_with_retry(
                poll_fn=poll_fn,
                is_done=lambda r: r == "done",
                is_failed=lambda r: None,
                poll_interval=1,
                max_wait=60,
            )

        assert result == "done"
        assert poll_fn.await_count == 3

    async def test_transient_error_retries(self):
        """轮询瞬态错误后重试成功。"""
        poll_fn = AsyncMock(side_effect=[ConnectionError("reset"), "done"])

        with patch("lib.video_backends.base.asyncio.sleep", new_callable=AsyncMock):
            result = await poll_with_retry(
                poll_fn=poll_fn,
                is_done=lambda r: r == "done",
                is_failed=lambda r: None,
                poll_interval=1,
                max_wait=60,
            )

        assert result == "done"
        assert poll_fn.await_count == 2

    async def test_non_retryable_error_propagates(self):
        """不可重试的错误立即抛出。"""
        poll_fn = AsyncMock(side_effect=ValueError("invalid"))

        with pytest.raises(ValueError, match="invalid"):
            with patch("lib.video_backends.base.asyncio.sleep", new_callable=AsyncMock):
                await poll_with_retry(
                    poll_fn=poll_fn,
                    is_done=lambda r: True,
                    is_failed=lambda r: None,
                    poll_interval=1,
                    max_wait=60,
                )

        assert poll_fn.await_count == 1

    async def test_timeout_raises(self):
        """超时抛出 TimeoutError。"""
        poll_fn = AsyncMock(return_value="pending")

        # 用 monotonic mock 模拟时间流逝
        times = iter([0, 0, 100, 100])  # 第二轮超时

        with (
            patch("lib.video_backends.base.asyncio.sleep", new_callable=AsyncMock),
            patch("lib.video_backends.base.time.monotonic", side_effect=times),
        ):
            with pytest.raises(TimeoutError, match="超时"):
                await poll_with_retry(
                    poll_fn=poll_fn,
                    is_done=lambda r: False,
                    is_failed=lambda r: None,
                    poll_interval=1,
                    max_wait=10,
                )

    async def test_failed_status_raises(self):
        """is_failed 返回错误信息时抛出 RuntimeError。"""
        poll_fn = AsyncMock(return_value="failed_result")

        with pytest.raises(RuntimeError, match="任务失败"):
            with patch("lib.video_backends.base.asyncio.sleep", new_callable=AsyncMock):
                await poll_with_retry(
                    poll_fn=poll_fn,
                    is_done=lambda r: False,
                    is_failed=lambda r: "任务失败" if r == "failed_result" else None,
                    poll_interval=1,
                    max_wait=60,
                )

    async def test_on_progress_called(self):
        """on_progress 回调被调用。"""
        poll_fn = AsyncMock(side_effect=["pending", "done"])
        progress_calls = []

        with patch("lib.video_backends.base.asyncio.sleep", new_callable=AsyncMock):
            await poll_with_retry(
                poll_fn=poll_fn,
                is_done=lambda r: r == "done",
                is_failed=lambda r: None,
                poll_interval=1,
                max_wait=60,
                on_progress=lambda r, elapsed: progress_calls.append(r),
            )

        assert progress_calls == ["pending"]


def _make_operational_error(msg: str) -> OperationalError:
    """构造 sqlalchemy OperationalError（params/orig/connection 仅签名形式占位）。"""
    return OperationalError(msg, params=None, orig=Exception(msg))


class TestPersistJobIdRetry:
    """fix #647 #3：persist_provider_job_id 在 DB 瞬态错误下重试 + 结构化日志。"""

    async def test_no_op_outside_worker_context(self):
        """非 worker 路径（contextvar 未 set）→ no-op，不访问 queue。"""
        with patch("lib.generation_queue.get_generation_queue") as fake_get:
            await persist_job_id_if_in_task_context("job-1", provider="openai")
            fake_get.assert_not_called()

    async def test_retries_on_sqlite_locked(self, caplog):
        """前 2 次 OperationalError → 第 3 次成功；retry 实际执行 3 次。"""
        attempts = 0

        async def _flaky_persist(_tid: str, _job: str) -> None:
            nonlocal attempts
            attempts += 1
            if attempts < 3:
                raise _make_operational_error("database is locked")

        class _FakeQueue:
            async def persist_provider_job_id(self, tid: str, job_id: str) -> None:
                await _flaky_persist(tid, job_id)

        fake_queue = _FakeQueue()

        token = set_current_task_id("task-1")
        try:
            with (
                patch("lib.generation_queue.get_generation_queue", return_value=fake_queue),
                patch("lib.retry.asyncio.sleep", new_callable=AsyncMock),
                caplog.at_level(logging.INFO, logger="lib.video_backends.base"),
            ):
                await persist_job_id_if_in_task_context("job-1", provider="openai")
        finally:
            reset_current_task_id(token)

        assert attempts == 3
        assert any("provider_job_id 已持久化" in r.message for r in caplog.records)

    async def test_terminal_failure_logs_structured(self, caplog):
        """全部重试失败 → logger.error 记录 task_id / provider / job_id 三键 + 重抛。"""

        async def _always_fail(_tid: str, _job: str) -> None:
            raise _make_operational_error("database is locked")

        class _FailingQueue:
            async def persist_provider_job_id(self, tid: str, job_id: str) -> None:
                await _always_fail(tid, job_id)

        fake_queue = _FailingQueue()

        token = set_current_task_id("task-X")
        try:
            with (
                patch("lib.generation_queue.get_generation_queue", return_value=fake_queue),
                patch("lib.retry.asyncio.sleep", new_callable=AsyncMock),
                caplog.at_level(logging.ERROR, logger="lib.video_backends.base"),
            ):
                with pytest.raises(OperationalError):
                    await persist_job_id_if_in_task_context("job-X", provider="ark")
        finally:
            reset_current_task_id(token)

        terminal = [r for r in caplog.records if r.levelno == logging.ERROR]
        assert terminal, "expected logger.error call"
        msg = terminal[-1].message
        assert "task_id=task-X" in msg
        assert "provider=ark" in msg
        assert "job_id=job-X" in msg

    async def test_no_retry_for_value_error(self):
        """ValueError 不在 retryable_errors 内 → 立即抛出，retry 仅尝试 1 次。"""
        attempts = 0

        async def _bad(_tid: str, _job: str) -> None:
            nonlocal attempts
            attempts += 1
            raise ValueError("not retryable")

        class _BadQueue:
            async def persist_provider_job_id(self, tid: str, job_id: str) -> None:
                await _bad(tid, job_id)

        fake_queue = _BadQueue()

        token = set_current_task_id("task-V")
        try:
            with (
                patch("lib.generation_queue.get_generation_queue", return_value=fake_queue),
                patch("lib.retry.asyncio.sleep", new_callable=AsyncMock),
            ):
                with pytest.raises(ValueError, match="not retryable"):
                    await persist_job_id_if_in_task_context("job-V", provider="newapi")
        finally:
            reset_current_task_id(token)

        assert attempts == 1

    async def test_no_retry_for_value_error_with_transient_string(self):
        """业务异常即使消息含 ``timed out`` / ``503`` 等串，也不该被字符串兜底吞掉重试。

        默认 `_should_retry` 在 isinstance 不匹配时做 RETRYABLE_STATUS_PATTERNS 字符串
        子串兜底，会把 `ValueError("Connection timed out: rate")` 当瞬态错误重试；
        改用 `retry_if=lambda e: isinstance(e, _PERSIST_RETRYABLE_ERRORS)` 后严格 isinstance。
        """
        attempts = 0

        async def _bad(_tid: str, _job: str) -> None:
            nonlocal attempts
            attempts += 1
            raise ValueError("Connection timed out: rate limited at upstream")

        class _BadQueue:
            async def persist_provider_job_id(self, tid: str, job_id: str) -> None:
                await _bad(tid, job_id)

        fake_queue = _BadQueue()

        token = set_current_task_id("task-T")
        try:
            with (
                patch("lib.generation_queue.get_generation_queue", return_value=fake_queue),
                patch("lib.retry.asyncio.sleep", new_callable=AsyncMock),
            ):
                with pytest.raises(ValueError, match="timed out"):
                    await persist_job_id_if_in_task_context("job-T", provider="gemini")
        finally:
            reset_current_task_id(token)

        assert attempts == 1, "expects no string-fallback retry for ValueError"
