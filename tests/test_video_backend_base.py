from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from lib.video_backends.base import (
    VideoCapability,
    VideoGenerationRequest,
    VideoGenerationResult,
    poll_with_retry,
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
        assert req.resolution == "1080p"
        assert req.start_image is None
        assert req.generate_audio is True
        assert req.negative_prompt is None
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
            negative_prompt="no music",
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
