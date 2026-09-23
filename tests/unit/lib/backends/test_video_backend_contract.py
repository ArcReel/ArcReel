from pathlib import Path

import pytest

from lib.backends.video_backend_contract import (
    DEFAULT_VIDEO_POLL_TIMEOUT_SECONDS,
    TERMINAL_PROVIDER_STATUSES,
    ProviderJobStatus,
    VideoGenerationRequest,
    VideoGenerationResult,
    normalize_provider_status,
)


class TestVideoGenerationRequest:
    def test_defaults(self):
        req = VideoGenerationRequest(prompt="test", output_path=Path("/tmp/out.mp4"))
        assert req.aspect_ratio == "9:16"
        assert req.duration_seconds == 5
        assert req.resolution is None
        assert req.start_image is None
        assert req.generate_audio is True
        assert req.reference_audio_files is None
        assert req.poll_timeout_seconds == 3600
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


class TestNormalizeProviderStatus:
    """跨厂商状态串归一：OpenAI 兼容代理会把底层厂商的状态串原样透传。"""

    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("completed", ProviderJobStatus.SUCCEEDED),
            ("succeeded", ProviderJobStatus.SUCCEEDED),
            ("succeed", ProviderJobStatus.SUCCEEDED),
            ("success", ProviderJobStatus.SUCCEEDED),
            ("SUCCEEDED", ProviderJobStatus.SUCCEEDED),
            ("  succeeded  ", ProviderJobStatus.SUCCEEDED),
            ("failed", ProviderJobStatus.FAILED),
            ("fail", ProviderJobStatus.FAILED),
            ("error", ProviderJobStatus.FAILED),
            ("FAILED", ProviderJobStatus.FAILED),
            ("canceled", ProviderJobStatus.FAILED),
            ("cancelled", ProviderJobStatus.FAILED),
            ("in_progress", ProviderJobStatus.RUNNING),
            ("Processing", ProviderJobStatus.RUNNING),
            ("generating", ProviderJobStatus.RUNNING),
            ("PENDING", ProviderJobStatus.QUEUED),
            ("submitted", ProviderJobStatus.QUEUED),
            # 未知 / 非字符串 → 当 running 继续轮询（保守：不对未就绪任务触发下载）
            ("NOT_START", ProviderJobStatus.RUNNING),
            ("weird-status", ProviderJobStatus.RUNNING),
            (None, ProviderJobStatus.RUNNING),
            (99, ProviderJobStatus.RUNNING),
        ],
    )
    def test_normalize(self, raw, expected):
        assert normalize_provider_status(raw) is expected

    @pytest.mark.parametrize("raw", ["expired", "EXPIRED", " Expired "])
    def test_expired_is_its_own_bucket(self, raw):
        """expired 不得折进 failed：caller 据其按 generate / resume 分流抛不同异常。"""
        assert normalize_provider_status(raw) is ProviderJobStatus.EXPIRED

    def test_terminal_set(self):
        assert (
            frozenset({ProviderJobStatus.SUCCEEDED, ProviderJobStatus.FAILED, ProviderJobStatus.EXPIRED})
            == TERMINAL_PROVIDER_STATUSES
        )
        assert ProviderJobStatus.RUNNING not in TERMINAL_PROVIDER_STATUSES
        assert ProviderJobStatus.QUEUED not in TERMINAL_PROVIDER_STATUSES


class TestPollTimeoutDefault:
    def test_matches_config_default(self):
        """请求缺省的轮询超时与配置层的缺省值是同一个数：两层因分层契约各声明一次。"""
        from lib.config.service import DEFAULT_VIDEO_POLL_TIMEOUT_SECONDS as config_default

        assert config_default == DEFAULT_VIDEO_POLL_TIMEOUT_SECONDS
