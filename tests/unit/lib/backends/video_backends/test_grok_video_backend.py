"""GrokVideoBackend 单元测试。"""

from __future__ import annotations

from collections.abc import Generator
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
import respx

from arcreel_market_core.video_backend_contract import (
    ReferenceAudioMode,
    VideoAudioMode,
    VideoCapabilityError,
    VideoGenerationRequest,
)
from lib.backends.providers import PROVIDER_GROK
from tests.fakes import bounded_poll_clock
from tests.http_capture import capture_http


@contextmanager
def _video_download(url: str, content: bytes) -> Generator[respx.Route]:
    """成片下载的出站流：base 层用真实 httpx 流式取字节，走 respx 在 transport 层拦截。"""
    with capture_http() as router:
        yield router.get(url).mock(return_value=httpx.Response(200, content=content))


@pytest.fixture
def video_output_path(tmp_path: Path) -> Path:
    return tmp_path / "output.mp4"


class TestGrokVideoBackend:
    @patch("lib.backends.video_backends.grok.create_grok_client")
    def test_name_and_model(self, mock_create):
        from lib.backends.video_backends.grok import GrokVideoBackend

        backend = GrokVideoBackend(api_key="test-key")
        assert backend.name == PROVIDER_GROK
        assert backend.model == "grok-imagine-video"

    @patch("lib.backends.video_backends.grok.create_grok_client")
    def test_capabilities(self, mock_create):
        from lib.backends.video_backends.grok import GrokVideoBackend

        backend = GrokVideoBackend(api_key="test-key")
        assert backend.video_capabilities.max_reference_images == 7

    @patch("lib.backends.video_backends.grok.create_grok_client")
    def test_custom_model(self, mock_create):
        from lib.backends.video_backends.grok import GrokVideoBackend

        backend = GrokVideoBackend(api_key="test-key", model="grok-imagine-video-2")
        assert backend.model == "grok-imagine-video-2"

    def test_missing_api_key_raises(self):
        from lib.backends.video_backends.grok import GrokVideoBackend

        with pytest.raises(ValueError, match="xAI API Key"):
            GrokVideoBackend(api_key=None)

    async def test_text_to_video(self, video_output_path: Path):
        from lib.backends.video_backends.grok import GrokVideoBackend

        mock_response = MagicMock()
        mock_response.url = "https://vidgen.x.ai/test/video.mp4"
        mock_response.duration = 5

        mock_video = MagicMock()
        mock_video.generate = AsyncMock(return_value=mock_response)

        mock_client = MagicMock()
        mock_client.video = mock_video

        with patch("lib.backends.video_backends.grok.create_grok_client", return_value=mock_client):
            backend = GrokVideoBackend(api_key="test-key")

            with _video_download(mock_response.url, b"fake-video-data"):
                request = VideoGenerationRequest(
                    prompt="A cat walking",
                    output_path=video_output_path,
                    aspect_ratio="16:9",
                    duration_seconds=5,
                    resolution="720p",
                )

                result = await backend.generate(request)

            assert result.provider == PROVIDER_GROK
            assert result.model == "grok-imagine-video"
            assert result.duration_seconds == 5
            assert result.video_path == video_output_path

            mock_video.generate.assert_awaited_once()
            call_kwargs = mock_video.generate.call_args[1]
            assert call_kwargs["prompt"] == "A cat walking"
            assert call_kwargs["model"] == "grok-imagine-video"
            assert call_kwargs["duration"] == 5
            assert call_kwargs["aspect_ratio"] == "16:9"
            assert call_kwargs["resolution"] == "720p"
            assert "image_url" not in call_kwargs

    @pytest.mark.parametrize("generate_audio", [True, False])
    async def test_audio_intent_reaches_sdk_and_settlement(self, video_output_path: Path, generate_audio: bool):
        """音轨开关原样下发为 SDK 的 generate_audio，结算按实际下发值记录，关闭即落无声。"""
        from lib.backends.video_backends.grok import GrokVideoBackend

        mock_response = MagicMock()
        mock_response.url = "https://vidgen.x.ai/test/video.mp4"
        mock_response.duration = 5

        mock_video = MagicMock()
        mock_video.generate = AsyncMock(return_value=mock_response)
        mock_client = MagicMock()
        mock_client.video = mock_video

        with patch("lib.backends.video_backends.grok.create_grok_client", return_value=mock_client):
            backend = GrokVideoBackend(api_key="test-key")

            with _video_download(mock_response.url, b"fake-video-data"):
                request = VideoGenerationRequest(
                    prompt="A cat walking",
                    output_path=video_output_path,
                    duration_seconds=5,
                    generate_audio=generate_audio,
                )
                result = await backend.generate(request)

        assert mock_video.generate.call_args[1]["generate_audio"] is generate_audio
        assert result.generate_audio is generate_audio

    async def test_default_request_keeps_audio(self, video_output_path: Path):
        """请求不声明音轨意图时保持有声：下发 True，结算记录有声。"""
        from lib.backends.video_backends.grok import GrokVideoBackend

        mock_response = MagicMock()
        mock_response.url = "https://vidgen.x.ai/test/video.mp4"
        mock_response.duration = 5

        mock_video = MagicMock()
        mock_video.generate = AsyncMock(return_value=mock_response)
        mock_client = MagicMock()
        mock_client.video = mock_video

        with patch("lib.backends.video_backends.grok.create_grok_client", return_value=mock_client):
            backend = GrokVideoBackend(api_key="test-key")

            with _video_download(mock_response.url, b"fake-video-data"):
                request = VideoGenerationRequest(
                    prompt="A cat walking", output_path=video_output_path, duration_seconds=5
                )
                result = await backend.generate(request)

        assert mock_video.generate.call_args[1]["generate_audio"] is True
        assert result.generate_audio is True

    async def test_marks_resubmit_unsafe_before_opaque_provider_call(self, video_output_path: Path):
        from lib.backends.video_backends.grok import GrokVideoBackend

        mock_video = MagicMock()
        mock_video.generate = AsyncMock(side_effect=RuntimeError("service unavailable"))
        mock_client = MagicMock()
        mock_client.video = mock_video
        resubmit_unsafe = MagicMock()

        with (
            patch("lib.backends.video_backends.grok.create_grok_client", return_value=mock_client),
            bounded_poll_clock(),
        ):
            backend = GrokVideoBackend(api_key="test-key")
            request = VideoGenerationRequest(
                prompt="A cat walking",
                output_path=video_output_path,
                duration_seconds=5,
                on_provider_resubmit_unsafe=resubmit_unsafe,
            )

            with pytest.raises(RuntimeError, match="service unavailable"):
                await backend.generate(request)

        resubmit_unsafe.assert_called_once_with()
        assert mock_video.generate.await_count == 1

    async def test_image_to_video(self, video_output_path: Path, tmp_path: Path):
        from lib.backends.video_backends.grok import GrokVideoBackend

        image_path = tmp_path / "start.png"
        image_path.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 100)

        mock_response = MagicMock()
        mock_response.url = "https://vidgen.x.ai/test/video.mp4"
        mock_response.duration = 8

        mock_video = MagicMock()
        mock_video.generate = AsyncMock(return_value=mock_response)

        mock_client = MagicMock()
        mock_client.video = mock_video

        with patch("lib.backends.video_backends.grok.create_grok_client", return_value=mock_client):
            backend = GrokVideoBackend(api_key="test-key")

            with _video_download(mock_response.url, b"fake-video-data"):
                request = VideoGenerationRequest(
                    prompt="Animate this scene",
                    output_path=video_output_path,
                    start_image=image_path,
                    duration_seconds=8,
                    resolution="720p",
                )

                result = await backend.generate(request)

            assert result.duration_seconds == 8

            call_kwargs = mock_video.generate.call_args[1]
            assert "image_url" in call_kwargs
            assert call_kwargs["image_url"].startswith("data:image/png;base64,")

    @pytest.mark.parametrize(
        ("raw_duration", "expected"),
        [
            (15, 15),  # 整数直接收窄
            ("15.0", 15),  # 浮点字符串先经 float 解析
            (7.8, 8),  # 浮点 half-up 取整，与 dashscope 计费口径一致
            (4.4, 4),  # half-up：不足半秒舍去
            (0, 5),  # 零值回落请求时长
            (-10, 5),  # 负值回落请求时长
            (0.3, 5),  # 取整到 0 同样回落，保持结果恒为正
            ("unknown", 5),  # 不可解析回落请求时长
            (float("inf"), 5),  # 溢出回落请求时长
            (1e100, 5),  # 超出合理上限回落请求时长，防 DB Integer 列溢出
            (86400.9, 5),  # 上限基于取整前原始值：小数已超 24h 不得因取整落回上限内
            (None, 5),  # 缺失回落请求时长
        ],
    )
    async def test_duration_narrowed_to_int_with_fallback(self, video_output_path: Path, raw_duration, expected):
        """SDK 回报的 duration 未类型化：可解析数值收窄为 int 作为实际计费时长，否则回落请求时长。"""
        from lib.backends.video_backends.grok import GrokVideoBackend

        mock_response = MagicMock()
        mock_response.url = "https://vidgen.x.ai/test/video.mp4"
        mock_response.duration = raw_duration

        mock_video = MagicMock()
        mock_video.generate = AsyncMock(return_value=mock_response)

        mock_client = MagicMock()
        mock_client.video = mock_video

        with patch("lib.backends.video_backends.grok.create_grok_client", return_value=mock_client):
            backend = GrokVideoBackend(api_key="test-key")

            with _video_download(mock_response.url, b"fake-video-data"):
                request = VideoGenerationRequest(
                    prompt="A cat walking",
                    output_path=video_output_path,
                    duration_seconds=5,
                    resolution="720p",
                )

                result = await backend.generate(request)

            assert result.duration_seconds == expected


def _png(path: Path) -> Path:
    path.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 100)
    return path


@contextmanager
def _grok_sdk(duration: int = 5) -> Generator[AsyncMock]:
    """替换 SDK client，产出 ``video.generate`` mock 以便断言下发参数。"""
    mock_response = MagicMock()
    mock_response.url = "https://vidgen.x.ai/test/video.mp4"
    mock_response.duration = duration
    mock_client = MagicMock()
    mock_client.video.generate = AsyncMock(return_value=mock_response)
    with (
        patch("lib.backends.video_backends.grok.create_grok_client", return_value=mock_client),
        _video_download(mock_response.url, b"fake-video-data"),
    ):
        yield mock_client.video.generate


class TestGrokVideo15Models:
    """grok-imagine-video-1.5 / 1.5-lite 的能力声明与请求下发。"""

    def test_1_5_declares_last_frame_and_reference_images(self):
        from lib.backends.video_backends.grok import GrokVideoBackend

        caps = GrokVideoBackend.video_capabilities_for_model("grok-imagine-video-1.5")
        assert caps.text_to_video is True
        assert caps.first_frame is True
        assert caps.last_frame is True
        assert caps.max_reference_images == 7
        assert caps.reference_audio_mode is ReferenceAudioMode.NONE
        assert caps.audio_track is VideoAudioMode.CONTROLLABLE

    def test_1_5_lite_declares_reference_images_without_last_frame(self):
        from lib.backends.video_backends.grok import GrokVideoBackend

        caps = GrokVideoBackend.video_capabilities_for_model("grok-imagine-video-1.5-lite")
        assert caps.text_to_video is True
        assert caps.first_frame is True
        assert caps.last_frame is False
        assert caps.max_reference_images == 7
        assert caps.audio_track is VideoAudioMode.CONTROLLABLE

    def test_classic_model_keeps_rejecting_last_frame(self):
        from lib.backends.video_backends.grok import GrokVideoBackend

        caps = GrokVideoBackend.video_capabilities_for_model("grok-imagine-video")
        assert caps.last_frame is False
        assert caps.max_reference_images == 7

    @pytest.mark.parametrize("model", ["grok-imagine-video-1.5", "grok-imagine-video-1.5-lite"])
    async def test_1080p_reaches_sdk(self, video_output_path: Path, tmp_path: Path, model: str):
        """文生与图生按请求分辨率原样下发 1080p，模型名与音轨开关一并透传。"""
        from lib.backends.video_backends.grok import GrokVideoBackend

        with _grok_sdk() as generate:
            backend = GrokVideoBackend(api_key="test-key", model=model)
            result = await backend.generate(
                VideoGenerationRequest(
                    prompt="A cat walking",
                    output_path=video_output_path,
                    start_image=_png(tmp_path / "start.png"),
                    duration_seconds=5,
                    resolution="1080p",
                    generate_audio=False,
                )
            )

        kwargs = generate.call_args[1]
        assert kwargs["model"] == model
        assert kwargs["resolution"] == "1080p"
        assert kwargs["generate_audio"] is False
        assert kwargs["image_url"].startswith("data:image/png;base64,")
        assert result.model == model

    async def test_1_5_sends_end_image_as_last_frame(self, video_output_path: Path, tmp_path: Path):
        from lib.backends.video_backends.grok import GrokVideoBackend

        with _grok_sdk() as generate:
            backend = GrokVideoBackend(api_key="test-key", model="grok-imagine-video-1.5")
            await backend.generate(
                VideoGenerationRequest(
                    prompt="Dolly to the window",
                    output_path=video_output_path,
                    start_image=_png(tmp_path / "start.png"),
                    end_image=_png(tmp_path / "end.png"),
                    duration_seconds=8,
                    resolution="720p",
                )
            )

        kwargs = generate.call_args[1]
        assert kwargs["image_url"].startswith("data:image/png;base64,")
        assert kwargs["last_frame_url"].startswith("data:image/png;base64,")
        assert kwargs["resolution"] == "720p"

    async def test_missing_end_image_fails_before_provider_call(self, video_output_path: Path, tmp_path: Path):
        """尾帧读不到不静默跳过：否则照常计费，成片却落不到分镜要求的结尾画面。"""
        from lib.backends.video_backends.grok import GrokVideoBackend

        with _grok_sdk() as generate:
            backend = GrokVideoBackend(api_key="test-key", model="grok-imagine-video-1.5")
            with pytest.raises(VideoCapabilityError) as exc:
                await backend.generate(
                    VideoGenerationRequest(
                        prompt="Dolly to the window",
                        output_path=video_output_path,
                        end_image=tmp_path / "missing.png",
                        duration_seconds=8,
                    )
                )

        assert exc.value.code == "video_end_image_unreadable"
        generate.assert_not_awaited()

    @pytest.mark.parametrize(
        ("model", "route"),
        [
            ("grok-imagine-video-1.5", "reference_images"),
            ("grok-imagine-video-1.5", "end_image"),
            ("grok-imagine-video-1.5-lite", "reference_images"),
        ],
    )
    async def test_reference_route_rejects_1080p_before_provider_call(
        self, video_output_path: Path, tmp_path: Path, model: str, route: str
    ):
        """参考生视频（参考图，或首帧加尾帧）官方上限 720p：1080p 在付费调用前拒绝，也不收回付费窗口。"""
        from lib.backends.video_backends.grok import GrokVideoBackend

        by_reference = route == "reference_images"
        resubmit_unsafe = MagicMock()
        with _grok_sdk() as generate:
            backend = GrokVideoBackend(api_key="test-key", model=model)
            with pytest.raises(VideoCapabilityError) as exc:
                await backend.generate(
                    VideoGenerationRequest(
                        prompt="The model from <IMAGE_0> walks in",
                        output_path=video_output_path,
                        duration_seconds=8,
                        resolution="1080p",
                        reference_images=[_png(tmp_path / "ref.png")] if by_reference else None,
                        start_image=None if by_reference else _png(tmp_path / "start.png"),
                        end_image=None if by_reference else _png(tmp_path / "end.png"),
                        on_provider_resubmit_unsafe=resubmit_unsafe,
                    )
                )

        assert exc.value.code == "video_reference_resolution_unsupported"
        assert exc.value.params["max_resolution"] == "720p"
        generate.assert_not_awaited()
        resubmit_unsafe.assert_not_called()

    @pytest.mark.parametrize("model", ["grok-imagine-video-1.5", "grok-imagine-video-1.5-lite"])
    async def test_reference_route_at_720p_reaches_sdk(self, video_output_path: Path, tmp_path: Path, model: str):
        from lib.backends.video_backends.grok import GrokVideoBackend

        with _grok_sdk() as generate:
            backend = GrokVideoBackend(api_key="test-key", model=model)
            await backend.generate(
                VideoGenerationRequest(
                    prompt="The model from <IMAGE_0> walks in",
                    output_path=video_output_path,
                    reference_images=[_png(tmp_path / "ref.png")],
                    duration_seconds=8,
                    resolution="720p",
                )
            )

        kwargs = generate.call_args[1]
        assert kwargs["resolution"] == "720p"
        assert len(kwargs["reference_image_urls"]) == 1
