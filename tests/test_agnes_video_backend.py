"""AgnesVideoBackend 单元测试（mock httpx，注入时钟、不打真实墙钟）。"""

from __future__ import annotations

import base64
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from lib.providers import PROVIDER_AGNES
from lib.video_backends.base import (
    ResumeExpiredError,
    VideoCapability,
    VideoCapabilityError,
    VideoGenerationRequest,
)


def _make_response(status_code: int, json_body: dict) -> MagicMock:
    resp = MagicMock()
    resp.status_code = status_code
    resp.json.return_value = json_body
    resp.raise_for_status = MagicMock()
    return resp


def _make_http_error(status_code: int, message: str) -> httpx.HTTPStatusError:
    request = httpx.Request("POST", "https://apihub.agnes-ai.com/v1/videos")
    response = httpx.Response(status_code, request=request, text=message)
    return httpx.HTTPStatusError(f"Server error '{status_code}'", request=request, response=response)


def _fake_download_factory(payload: bytes = b"mp4-bytes"):
    async def _fake(url: str, output_path: Path, *, timeout: int = 120) -> None:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(payload)

    return _fake


def _completed(task_id: str = "task-1", url: str = "https://cdn.agnes/out.mp4", **extra) -> dict:
    body = {
        "task_id": task_id,
        "status": "completed",
        "size": "720x1280",
        "remixed_from_video_id": url,
    }
    body.update(extra)
    return body


def _mock_client(*, post=None, get=None) -> AsyncMock:
    client = AsyncMock()
    client.post = post or AsyncMock()
    client.get = get or AsyncMock()
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=None)
    return client


def _write_image(path: Path, payload: bytes) -> Path:
    path.write_bytes(payload)
    return path


class TestCapabilities:
    def test_name_and_model(self):
        from lib.video_backends.agnes import AgnesVideoBackend

        backend = AgnesVideoBackend(api_key="sk-test", base_url="https://apihub.agnes-ai.com/v1")
        assert backend.name == PROVIDER_AGNES
        assert backend.model == "agnes-video-v2.0"

    def test_default_model_when_unset(self):
        from lib.video_backends.agnes import AgnesVideoBackend

        backend = AgnesVideoBackend(api_key="sk-test")
        assert backend.model == "agnes-video-v2.0"

    def test_capabilities_and_video_capabilities(self):
        from lib.video_backends.agnes import AgnesVideoBackend

        backend = AgnesVideoBackend(api_key="sk-test")
        assert VideoCapability.TEXT_TO_VIDEO in backend.capabilities
        assert VideoCapability.IMAGE_TO_VIDEO in backend.capabilities
        caps = backend.video_capabilities
        assert caps.first_frame is True
        assert caps.last_frame is True
        assert caps.reference_images is True
        assert caps.max_reference_images == 4
        # 单通道 + mode 不可叠加：参考图不与首帧并存
        assert caps.reference_images_with_start_frame is False


class TestNumFramesAndSize:
    @pytest.mark.parametrize(
        ("duration", "expected_frames"),
        [(1, 25), (3, 73), (5, 121), (10, 241), (18, 433)],
    )
    def test_duration_to_num_frames_aligns_to_8n_plus_1(self, duration: int, expected_frames: int):
        from lib.video_backends.agnes import _duration_to_num_frames

        frames = _duration_to_num_frames(duration)
        assert frames == expected_frames
        assert (frames - 1) % 8 == 0  # 形如 8n+1
        assert frames <= 441

    def test_resolve_size_portrait_explicit_hw(self):
        from lib.video_backends.agnes import _resolve_size

        width, height = _resolve_size("720p", "9:16")
        assert (width, height) == (720, 1280)
        assert width % 8 == 0 and height % 8 == 0


class TestDurationCoercion:
    @pytest.mark.parametrize(
        ("value", "expected"),
        [
            (10, 10),
            ("10.0", 10),
            ("9.6", 10),  # half-up 取整，不少计费秒
            ("9.4", 9),
            (4.5, 5),
            (0, None),  # 非正值回 None，由 caller 回落请求时长
            ("0", None),
            (-3, None),
            ("abc", None),
            (None, None),
        ],
    )
    def test_coerce_duration(self, value: object, expected: int | None):
        from lib.video_backends.agnes import _coerce_duration

        assert _coerce_duration(value) == expected

    def test_extract_duration_falls_back_when_usage_zero(self):
        from lib.video_backends.agnes import _extract_duration_seconds

        # usage.duration_seconds=0 不应落到结果对象，回落请求时长
        assert _extract_duration_seconds({"usage": {"duration_seconds": 0}}, fallback=5) == 5
        # 优先 usage，其次顶层 seconds，再回落
        assert _extract_duration_seconds({"usage": {"duration_seconds": "9.6"}}, fallback=5) == 10
        assert _extract_duration_seconds({"seconds": "7"}, fallback=5) == 7
        assert _extract_duration_seconds({}, fallback=5) == 5


class TestTextToVideo:
    async def test_happy_path_submits_and_polls(self, tmp_path: Path):
        create_resp = _make_response(200, {"task_id": "task-42", "status": "queued"})
        poll_resp = _make_response(200, _completed("task-42", "https://cdn.agnes/out.mp4", seconds="5.0"))

        client = _mock_client(
            post=AsyncMock(return_value=create_resp),
            get=AsyncMock(return_value=poll_resp),
        )
        fake_download = AsyncMock(side_effect=_fake_download_factory(b"mp4-bytes"))

        with (
            patch("httpx.AsyncClient", return_value=client),
            patch("lib.video_backends.agnes._POLL_INTERVAL_SECONDS", 0.0),
            patch("lib.video_backends.agnes.download_video", fake_download),
        ):
            from lib.video_backends.agnes import AgnesVideoBackend

            backend = AgnesVideoBackend(api_key="sk-test", base_url="https://apihub.agnes-ai.com/v1")
            result = await backend.generate(
                VideoGenerationRequest(
                    prompt="A cat running",
                    output_path=tmp_path / "out.mp4",
                    aspect_ratio="9:16",
                    resolution="720p",
                    duration_seconds=5,
                    seed=7,
                )
            )

        assert result.video_path == tmp_path / "out.mp4"
        assert result.video_path.read_bytes() == b"mp4-bytes"
        assert result.provider == PROVIDER_AGNES
        assert result.model == "agnes-video-v2.0"
        assert result.duration_seconds == 5
        assert result.task_id == "task-42"
        assert result.video_uri == "https://cdn.agnes/out.mp4"
        # Agnes 无音频能力，成片恒无声
        assert result.generate_audio is False

        post_call = client.post.call_args
        assert post_call.args[0] == "https://apihub.agnes-ai.com/v1/videos"
        body = post_call.kwargs["json"]
        assert body["model"] == "agnes-video-v2.0"
        assert body["prompt"] == "A cat running"
        assert body["height"] == 1280
        assert body["width"] == 720
        assert body["num_frames"] == 121
        assert body["frame_rate"] == 24
        assert body["seed"] == 7
        # 文生视频：无任何图像通道
        assert "image" not in body
        assert "extra_body" not in body
        assert post_call.kwargs["headers"]["Authorization"] == "Bearer sk-test"
        # submit 用长超时覆盖上游长阻塞
        assert post_call.kwargs["timeout"] == 300.0

        # 下载从 remixed_from_video_id 成片 URL，不带 auth
        fake_download.assert_called_once()
        assert fake_download.call_args.args[0] == "https://cdn.agnes/out.mp4"

    async def test_polls_through_in_progress(self, tmp_path: Path):
        create_resp = _make_response(200, {"task_id": "t3", "status": "queued"})
        in_progress = _make_response(200, {"task_id": "t3", "status": "in_progress", "progress": 40})
        completed = _make_response(200, _completed("t3"))

        client = _mock_client(
            post=AsyncMock(return_value=create_resp),
            get=AsyncMock(side_effect=[in_progress, in_progress, completed]),
        )
        fake_download = AsyncMock(side_effect=_fake_download_factory(b"v"))

        with (
            patch("httpx.AsyncClient", return_value=client),
            patch("lib.video_backends.agnes._POLL_INTERVAL_SECONDS", 0.0),
            patch("lib.video_backends.agnes.download_video", fake_download),
        ):
            from lib.video_backends.agnes import AgnesVideoBackend

            backend = AgnesVideoBackend(api_key="k", base_url="https://x/v1")
            result = await backend.generate(
                VideoGenerationRequest(
                    prompt="p", output_path=tmp_path / "o.mp4", aspect_ratio="9:16", duration_seconds=5
                )
            )

        assert result.task_id == "t3"
        assert client.get.call_count == 3
        fake_download.assert_called_once()


class TestImageChannels:
    async def test_start_image_is_bare_base64_top_level_image(self, tmp_path: Path):
        """起始图为裸 base64（无 data: 前缀），未复用 data-URI helper。"""
        img_bytes = b"\x89PNG\r\nfake-start"
        img_path = _write_image(tmp_path / "start.png", img_bytes)

        create_resp = _make_response(200, {"task_id": "t1", "status": "queued"})
        poll_resp = _make_response(200, _completed("t1"))
        client = _mock_client(
            post=AsyncMock(return_value=create_resp),
            get=AsyncMock(return_value=poll_resp),
        )
        fake_download = AsyncMock(side_effect=_fake_download_factory(b"v"))

        with (
            patch("httpx.AsyncClient", return_value=client),
            patch("lib.video_backends.agnes._POLL_INTERVAL_SECONDS", 0.0),
            patch("lib.video_backends.agnes.download_video", fake_download),
        ):
            from lib.video_backends.agnes import AgnesVideoBackend

            backend = AgnesVideoBackend(api_key="k", base_url="https://x/v1")
            await backend.generate(
                VideoGenerationRequest(
                    prompt="p",
                    output_path=tmp_path / "o.mp4",
                    start_image=img_path,
                    aspect_ratio="9:16",
                    duration_seconds=5,
                )
            )

        sent = client.post.call_args.kwargs["json"]["image"]
        expected = base64.b64encode(img_bytes).decode("ascii")
        assert sent == expected
        # 裸 base64，绝不带 data: 前缀
        assert not sent.startswith("data:")
        assert "extra_body" not in client.post.call_args.kwargs["json"]

    async def test_first_last_keyframes_extra_body(self, tmp_path: Path):
        start = _write_image(tmp_path / "s.png", b"start-bytes")
        end = _write_image(tmp_path / "e.png", b"end-bytes")

        create_resp = _make_response(200, {"task_id": "t-kf", "status": "queued"})
        poll_resp = _make_response(200, _completed("t-kf"))
        client = _mock_client(
            post=AsyncMock(return_value=create_resp),
            get=AsyncMock(return_value=poll_resp),
        )
        fake_download = AsyncMock(side_effect=_fake_download_factory(b"v"))

        with (
            patch("httpx.AsyncClient", return_value=client),
            patch("lib.video_backends.agnes._POLL_INTERVAL_SECONDS", 0.0),
            patch("lib.video_backends.agnes.download_video", fake_download),
        ):
            from lib.video_backends.agnes import AgnesVideoBackend

            backend = AgnesVideoBackend(api_key="k", base_url="https://x/v1")
            await backend.generate(
                VideoGenerationRequest(
                    prompt="p",
                    output_path=tmp_path / "o.mp4",
                    start_image=start,
                    end_image=end,
                    aspect_ratio="9:16",
                    duration_seconds=5,
                )
            )

        body = client.post.call_args.kwargs["json"]
        assert "image" not in body  # 单通道：keyframes 走 extra_body，不占顶层 image
        extra = body["extra_body"]
        assert extra["mode"] == "keyframes"
        assert extra["image"] == [
            base64.b64encode(b"start-bytes").decode("ascii"),
            base64.b64encode(b"end-bytes").decode("ascii"),
        ]
        assert all(not s.startswith("data:") for s in extra["image"])

    async def test_reference_images_extra_body(self, tmp_path: Path):
        ref1 = _write_image(tmp_path / "r1.png", b"ref-1")
        ref2 = _write_image(tmp_path / "r2.png", b"ref-2")

        create_resp = _make_response(200, {"task_id": "t-ref", "status": "queued"})
        poll_resp = _make_response(200, _completed("t-ref"))
        client = _mock_client(
            post=AsyncMock(return_value=create_resp),
            get=AsyncMock(return_value=poll_resp),
        )
        fake_download = AsyncMock(side_effect=_fake_download_factory(b"v"))

        with (
            patch("httpx.AsyncClient", return_value=client),
            patch("lib.video_backends.agnes._POLL_INTERVAL_SECONDS", 0.0),
            patch("lib.video_backends.agnes.download_video", fake_download),
        ):
            from lib.video_backends.agnes import AgnesVideoBackend

            backend = AgnesVideoBackend(api_key="k", base_url="https://x/v1")
            await backend.generate(
                VideoGenerationRequest(
                    prompt="p",
                    output_path=tmp_path / "o.mp4",
                    reference_images=[ref1, ref2],
                    aspect_ratio="9:16",
                    duration_seconds=5,
                )
            )

        body = client.post.call_args.kwargs["json"]
        assert "image" not in body
        extra = body["extra_body"]
        assert "mode" not in extra  # 参考生视频不带 keyframes mode
        assert extra["image"] == [
            base64.b64encode(b"ref-1").decode("ascii"),
            base64.b64encode(b"ref-2").decode("ascii"),
        ]

    async def test_reference_images_exceeded_raises(self, tmp_path: Path):
        refs = [_write_image(tmp_path / f"r{i}.png", f"r{i}".encode()) for i in range(5)]
        client = _mock_client(post=AsyncMock(side_effect=AssertionError("超限不应提交")))

        with patch("httpx.AsyncClient", return_value=client):
            from lib.video_backends.agnes import AgnesVideoBackend

            backend = AgnesVideoBackend(api_key="k", base_url="https://x/v1")
            with pytest.raises(VideoCapabilityError) as ei:
                await backend.generate(
                    VideoGenerationRequest(
                        prompt="p",
                        output_path=tmp_path / "o.mp4",
                        reference_images=refs,
                        aspect_ratio="9:16",
                        duration_seconds=5,
                    )
                )
            assert ei.value.code == "video_reference_images_exceeded"

    @pytest.mark.parametrize("with_start", [True, False])
    async def test_reference_images_with_frame_fails_loud(self, tmp_path: Path, with_start: bool):
        """参考图与首/尾帧同时给出时 fail-loud（单通道互斥），不静默走参考图分支丢掉关键帧。"""
        ref = _write_image(tmp_path / "r.png", b"ref")
        frame = _write_image(tmp_path / "f.png", b"frame")
        client = _mock_client(post=AsyncMock(side_effect=AssertionError("混合输入不应提交")))

        with patch("httpx.AsyncClient", return_value=client):
            from lib.video_backends.agnes import AgnesVideoBackend

            backend = AgnesVideoBackend(api_key="k", base_url="https://x/v1")
            with pytest.raises(VideoCapabilityError) as ei:
                await backend.generate(
                    VideoGenerationRequest(
                        prompt="p",
                        output_path=tmp_path / "o.mp4",
                        reference_images=[ref],
                        start_image=frame if with_start else None,
                        end_image=None if with_start else frame,
                        aspect_ratio="9:16",
                        duration_seconds=5,
                    )
                )
            assert ei.value.code == "video_reference_images_with_frames_unsupported"
        client.post.assert_not_called()

    async def test_end_image_only_fails_loud(self, tmp_path: Path):
        """仅提供尾帧（无首帧）时 fail-loud——Agnes 无独立尾帧通道，不静默退化为文生视频。"""
        end = _write_image(tmp_path / "e.png", b"end")
        client = _mock_client(post=AsyncMock(side_effect=AssertionError("仅尾帧不应提交")))

        with patch("httpx.AsyncClient", return_value=client):
            from lib.video_backends.agnes import AgnesVideoBackend

            backend = AgnesVideoBackend(api_key="k", base_url="https://x/v1")
            with pytest.raises(VideoCapabilityError) as ei:
                await backend.generate(
                    VideoGenerationRequest(
                        prompt="p",
                        output_path=tmp_path / "o.mp4",
                        end_image=end,
                        aspect_ratio="9:16",
                        duration_seconds=5,
                    )
                )
            assert ei.value.code == "video_end_image_requires_start_image"
        client.post.assert_not_called()

    async def test_missing_start_image_fails_loud(self, tmp_path: Path):
        client = _mock_client(post=AsyncMock(side_effect=AssertionError("缺图不应提交")))

        with patch("httpx.AsyncClient", return_value=client):
            from lib.video_backends.agnes import AgnesVideoBackend

            backend = AgnesVideoBackend(api_key="k", base_url="https://x/v1")
            with pytest.raises(VideoCapabilityError) as ei:
                await backend.generate(
                    VideoGenerationRequest(
                        prompt="p",
                        output_path=tmp_path / "o.mp4",
                        start_image=tmp_path / "missing.png",
                        aspect_ratio="9:16",
                        duration_seconds=5,
                    )
                )
            assert ei.value.code == "video_start_image_unreadable"

    async def test_missing_end_image_fails_loud_with_end_code(self, tmp_path: Path):
        """首尾帧模式下尾帧缺失：错误码指向尾帧而非首帧。"""
        start = _write_image(tmp_path / "s.png", b"start-bytes")
        client = _mock_client(post=AsyncMock(side_effect=AssertionError("缺尾帧不应提交")))

        with patch("httpx.AsyncClient", return_value=client):
            from lib.video_backends.agnes import AgnesVideoBackend

            backend = AgnesVideoBackend(api_key="k", base_url="https://x/v1")
            with pytest.raises(VideoCapabilityError) as ei:
                await backend.generate(
                    VideoGenerationRequest(
                        prompt="p",
                        output_path=tmp_path / "o.mp4",
                        start_image=start,
                        end_image=tmp_path / "missing-end.png",
                        aspect_ratio="9:16",
                        duration_seconds=5,
                    )
                )
            assert ei.value.code == "video_end_image_unreadable"

    async def test_empty_path_objects_degrade_to_text_to_video(self, tmp_path: Path):
        """空 Path（``Path("")`` 塌成 ``Path(".")``）应归一化为 None，回落文生视频而非误报无法读取。"""
        create_resp = _make_response(200, {"task_id": "t-empty", "status": "queued"})
        poll_resp = _make_response(200, _completed("t-empty"))
        client = _mock_client(
            post=AsyncMock(return_value=create_resp),
            get=AsyncMock(return_value=poll_resp),
        )
        fake_download = AsyncMock(side_effect=_fake_download_factory(b"v"))

        with (
            patch("httpx.AsyncClient", return_value=client),
            patch("lib.video_backends.agnes._POLL_INTERVAL_SECONDS", 0.0),
            patch("lib.video_backends.agnes.download_video", fake_download),
        ):
            from lib.video_backends.agnes import AgnesVideoBackend

            backend = AgnesVideoBackend(api_key="k", base_url="https://x/v1")
            await backend.generate(
                VideoGenerationRequest(
                    prompt="p",
                    output_path=tmp_path / "o.mp4",
                    start_image=Path(""),
                    reference_images=[Path("")],
                    aspect_ratio="9:16",
                    duration_seconds=5,
                )
            )

        body = client.post.call_args.kwargs["json"]
        assert "image" not in body
        assert "extra_body" not in body


class TestFailureAndTimeout:
    async def test_failed_status_raises(self, tmp_path: Path):
        create_resp = _make_response(200, {"task_id": "t2", "status": "queued"})
        poll_resp = _make_response(200, {"task_id": "t2", "status": "failed", "error": {"message": "upstream down"}})
        client = _mock_client(
            post=AsyncMock(return_value=create_resp),
            get=AsyncMock(return_value=poll_resp),
        )
        fake_download = AsyncMock()

        with (
            patch("httpx.AsyncClient", return_value=client),
            patch("lib.video_backends.agnes._POLL_INTERVAL_SECONDS", 0.0),
            patch("lib.video_backends.agnes.download_video", fake_download),
        ):
            from lib.video_backends.agnes import AgnesVideoBackend

            backend = AgnesVideoBackend(api_key="k", base_url="https://x/v1")
            with pytest.raises(RuntimeError, match="upstream down"):
                await backend.generate(
                    VideoGenerationRequest(
                        prompt="p", output_path=tmp_path / "o.mp4", aspect_ratio="9:16", duration_seconds=5
                    )
                )

        fake_download.assert_not_called()

    async def test_non_failed_terminal_status_fails_fast(self, tmp_path: Path):
        """上游以 cancelled 等非 failed 失败态收尾时快速失败，不轮询到 timeout。"""
        create_resp = _make_response(200, {"task_id": "t-cxl", "status": "queued"})
        cancelled = _make_response(
            200, {"task_id": "t-cxl", "status": "cancelled", "error": {"message": "user cancelled"}}
        )
        client = _mock_client(
            post=AsyncMock(return_value=create_resp),
            get=AsyncMock(return_value=cancelled),
        )
        fake_download = AsyncMock()

        with (
            patch("httpx.AsyncClient", return_value=client),
            patch("lib.video_backends.agnes._POLL_INTERVAL_SECONDS", 0.0),
            patch("lib.video_backends.agnes.download_video", fake_download),
        ):
            from lib.video_backends.agnes import AgnesVideoBackend

            backend = AgnesVideoBackend(api_key="k", base_url="https://x/v1")
            with pytest.raises(RuntimeError, match="user cancelled"):
                await backend.generate(
                    VideoGenerationRequest(
                        prompt="p", output_path=tmp_path / "o.mp4", aspect_ratio="9:16", duration_seconds=5
                    )
                )
        fake_download.assert_not_called()

    async def test_completed_without_video_url_raises(self, tmp_path: Path):
        create_resp = _make_response(200, {"task_id": "t-nourl", "status": "queued"})
        poll_resp = _make_response(200, {"task_id": "t-nourl", "status": "completed"})
        client = _mock_client(
            post=AsyncMock(return_value=create_resp),
            get=AsyncMock(return_value=poll_resp),
        )
        fake_download = AsyncMock()

        with (
            patch("httpx.AsyncClient", return_value=client),
            patch("lib.video_backends.agnes._POLL_INTERVAL_SECONDS", 0.0),
            patch("lib.video_backends.agnes.download_video", fake_download),
        ):
            from lib.video_backends.agnes import AgnesVideoBackend

            backend = AgnesVideoBackend(api_key="k", base_url="https://x/v1")
            with pytest.raises(RuntimeError, match="remixed_from_video_id"):
                await backend.generate(
                    VideoGenerationRequest(
                        prompt="p", output_path=tmp_path / "o.mp4", aspect_ratio="9:16", duration_seconds=5
                    )
                )
        fake_download.assert_not_called()

    async def test_polling_timeout_raises(self, tmp_path: Path):
        create_resp = _make_response(200, {"task_id": "t-timeout", "status": "queued"})
        in_progress = _make_response(200, {"task_id": "t-timeout", "status": "in_progress"})
        client = _mock_client(
            post=AsyncMock(return_value=create_resp),
            get=AsyncMock(return_value=in_progress),
        )
        fake_download = AsyncMock()

        with (
            patch("httpx.AsyncClient", return_value=client),
            patch("lib.video_backends.agnes._POLL_INTERVAL_SECONDS", 0.0),
            patch("lib.video_backends.agnes._MIN_POLL_TIMEOUT_SECONDS", 0.01),
            patch("lib.video_backends.agnes._POLL_TIMEOUT_PER_SECOND", 0),
            patch("lib.video_backends.agnes.download_video", fake_download),
        ):
            from lib.video_backends.agnes import AgnesVideoBackend

            backend = AgnesVideoBackend(api_key="k", base_url="https://x/v1")
            with pytest.raises(TimeoutError, match="Agnes"):
                await backend.generate(
                    VideoGenerationRequest(
                        prompt="p", output_path=tmp_path / "o.mp4", aspect_ratio="9:16", duration_seconds=5
                    )
                )
        fake_download.assert_not_called()


class TestSubmitResilience:
    async def test_submit_retries_on_503_busy(self, tmp_path: Path):
        """503 Service busy → 经 should_retry_submit 的 status_code 闸门重试。"""
        busy = MagicMock()
        busy.status_code = 503
        busy.raise_for_status = MagicMock(side_effect=_make_http_error(503, "Service busy"))
        create_resp = _make_response(200, {"task_id": "t-retry", "status": "queued"})
        poll_resp = _make_response(200, _completed("t-retry"))

        client = _mock_client(
            post=AsyncMock(side_effect=[busy, busy, create_resp]),
            get=AsyncMock(return_value=poll_resp),
        )
        fake_download = AsyncMock(side_effect=_fake_download_factory(b"v"))

        with (
            patch("httpx.AsyncClient", return_value=client),
            patch("lib.video_backends.agnes._POLL_INTERVAL_SECONDS", 0.0),
            patch("lib.retry._compute_wait", lambda attempt, backoff: 0.0),
            patch("lib.video_backends.agnes.download_video", fake_download),
        ):
            from lib.video_backends.agnes import AgnesVideoBackend

            backend = AgnesVideoBackend(api_key="k", base_url="https://x/v1")
            result = await backend.generate(
                VideoGenerationRequest(
                    prompt="p", output_path=tmp_path / "o.mp4", aspect_ratio="9:16", duration_seconds=5
                )
            )

        assert result.task_id == "t-retry"
        assert client.post.call_count == 3

    async def test_submit_non_retryable_4xx_fails_fast(self, tmp_path: Path):
        bad = _make_response(400, {"error": "bad request"})
        bad.raise_for_status = MagicMock(side_effect=_make_http_error(400, "bad request"))
        client = _mock_client(
            post=AsyncMock(return_value=bad),
            get=AsyncMock(side_effect=AssertionError("4xx 应在提交阶段失败，不该轮询")),
        )

        with (
            patch("httpx.AsyncClient", return_value=client),
            patch("lib.retry._compute_wait", lambda attempt, backoff: 0.0),
        ):
            from lib.video_backends.agnes import AgnesVideoBackend

            backend = AgnesVideoBackend(api_key="k", base_url="https://x/v1")
            with pytest.raises(httpx.HTTPStatusError):
                await backend.generate(
                    VideoGenerationRequest(
                        prompt="p", output_path=tmp_path / "o.mp4", aspect_ratio="9:16", duration_seconds=5
                    )
                )
        assert client.post.call_count == 1

    async def test_submit_read_timeout_wraps_ambiguous(self, tmp_path: Path):
        from lib.video_backends.base import AmbiguousSubmitError

        client = _mock_client(
            post=AsyncMock(side_effect=httpx.ReadTimeout("read timed out")),
            get=AsyncMock(side_effect=AssertionError("歧义态不该轮询")),
        )

        with (
            patch("httpx.AsyncClient", return_value=client),
            patch("lib.retry._compute_wait", lambda attempt, backoff: 0.0),
        ):
            from lib.video_backends.agnes import AgnesVideoBackend

            backend = AgnesVideoBackend(api_key="k", base_url="https://x/v1")
            with pytest.raises(AmbiguousSubmitError):
                await backend.generate(
                    VideoGenerationRequest(
                        prompt="p", output_path=tmp_path / "o.mp4", aspect_ratio="9:16", duration_seconds=5
                    )
                )
        assert client.post.call_count == 1


class TestResume:
    async def test_resume_polls_existing_job_no_submit(self, tmp_path: Path):
        poll_resp = _make_response(200, _completed("task-resume", "https://cdn/resumed.mp4"))
        client = _mock_client(
            post=AsyncMock(side_effect=AssertionError("resume 不应 POST create")),
            get=AsyncMock(return_value=poll_resp),
        )
        fake_download = AsyncMock(side_effect=_fake_download_factory(b"resumed"))

        with (
            patch("httpx.AsyncClient", return_value=client),
            patch("lib.video_backends.agnes._POLL_INTERVAL_SECONDS", 0.0),
            patch("lib.video_backends.agnes.download_video", fake_download),
        ):
            from lib.video_backends.agnes import AgnesVideoBackend

            backend = AgnesVideoBackend(api_key="k", base_url="https://x/v1")
            result = await backend.resume_video(
                "task-resume",
                VideoGenerationRequest(
                    prompt="p", output_path=tmp_path / "out.mp4", aspect_ratio="9:16", duration_seconds=5
                ),
            )

        client.post.assert_not_called()
        assert client.get.call_args.args[0].endswith("/videos/task-resume")
        assert result.task_id == "task-resume"
        assert (tmp_path / "out.mp4").read_bytes() == b"resumed"

    async def test_resume_404_raises_resume_expired_without_retry(self, tmp_path: Path):
        not_found = _make_response(404, {"error": "task not found"})
        not_found.raise_for_status = MagicMock(side_effect=_make_http_error(404, "task not found"))
        client = _mock_client(get=AsyncMock(return_value=not_found))

        with (
            patch("httpx.AsyncClient", return_value=client),
            patch("lib.video_backends.agnes._POLL_INTERVAL_SECONDS", 0.0),
        ):
            from lib.video_backends.agnes import AgnesVideoBackend

            backend = AgnesVideoBackend(api_key="k", base_url="https://x/v1")
            with pytest.raises(ResumeExpiredError) as ei:
                await backend.resume_video(
                    "task-404",
                    VideoGenerationRequest(
                        prompt="p", output_path=tmp_path / "out.mp4", aspect_ratio="9:16", duration_seconds=5
                    ),
                )
            assert ei.value.job_id == "task-404"
            assert ei.value.provider == PROVIDER_AGNES
            assert client.get.call_count == 1


class TestDurationValidation:
    @pytest.mark.parametrize("duration", [0, 19, 30])
    async def test_out_of_range_duration_fails_loud_without_submit(self, tmp_path: Path, duration: int):
        """越界时长（< 1 或 > 18）在建单前 fail-loud，不静默截帧到 441、不 POST、不错记计费时长。"""
        client = _mock_client(post=AsyncMock(side_effect=AssertionError("越界时长不应提交")))

        with patch("httpx.AsyncClient", return_value=client):
            from lib.video_backends.agnes import AgnesVideoBackend

            backend = AgnesVideoBackend(api_key="k", base_url="https://x/v1")
            with pytest.raises(VideoCapabilityError) as ei:
                await backend.generate(
                    VideoGenerationRequest(
                        prompt="p", output_path=tmp_path / "o.mp4", aspect_ratio="9:16", duration_seconds=duration
                    )
                )
            assert ei.value.code == "video_duration_not_supported"
        client.post.assert_not_called()


class TestProviderJobIdPersistence:
    async def test_persists_agnes_task_id_for_worker_request(self, tmp_path: Path):
        """worker 路径（request.task_id 非空）下，submit 返回的 Agnes task_id 作为 job_id 写回，覆盖 resume 契约。"""
        create_resp = _make_response(200, {"task_id": "agnes-task-42", "status": "queued"})
        poll_resp = _make_response(200, _completed("agnes-task-42"))
        client = _mock_client(
            post=AsyncMock(return_value=create_resp),
            get=AsyncMock(return_value=poll_resp),
        )
        fake_download = AsyncMock(side_effect=_fake_download_factory(b"v"))
        persist = AsyncMock()

        with (
            patch("httpx.AsyncClient", return_value=client),
            patch("lib.video_backends.agnes._POLL_INTERVAL_SECONDS", 0.0),
            patch("lib.video_backends.agnes.download_video", fake_download),
            patch("lib.video_backends.base.persist_provider_job_id", persist),
        ):
            from lib.video_backends.agnes import AgnesVideoBackend

            backend = AgnesVideoBackend(api_key="k", base_url="https://x/v1")
            await backend.generate(
                VideoGenerationRequest(
                    prompt="p",
                    output_path=tmp_path / "o.mp4",
                    aspect_ratio="9:16",
                    duration_seconds=5,
                    task_id="worker-task-99",
                )
            )

        persist.assert_awaited_once()
        args, kwargs = persist.call_args
        assert args[0] == "worker-task-99"  # worker 任务 id
        assert args[1] == "agnes-task-42"  # Agnes submit 返回的 task_id 作为 job_id 写回
        assert kwargs["provider"] == PROVIDER_AGNES

    async def test_non_worker_request_skips_persistence(self, tmp_path: Path):
        """非 worker 路径（task_id=None）不调用持久化，避免空 task_id 写库。"""
        create_resp = _make_response(200, {"task_id": "agnes-task-1", "status": "queued"})
        poll_resp = _make_response(200, _completed("agnes-task-1"))
        client = _mock_client(
            post=AsyncMock(return_value=create_resp),
            get=AsyncMock(return_value=poll_resp),
        )
        fake_download = AsyncMock(side_effect=_fake_download_factory(b"v"))
        persist = AsyncMock()

        with (
            patch("httpx.AsyncClient", return_value=client),
            patch("lib.video_backends.agnes._POLL_INTERVAL_SECONDS", 0.0),
            patch("lib.video_backends.agnes.download_video", fake_download),
            patch("lib.video_backends.base.persist_provider_job_id", persist),
        ):
            from lib.video_backends.agnes import AgnesVideoBackend

            backend = AgnesVideoBackend(api_key="k", base_url="https://x/v1")
            await backend.generate(
                VideoGenerationRequest(
                    prompt="p", output_path=tmp_path / "o.mp4", aspect_ratio="9:16", duration_seconds=5
                )
            )

        persist.assert_not_called()


class TestRegistration:
    def test_registered_in_video_backend_registry(self):
        from lib.video_backends import create_backend, get_registered_backends

        assert PROVIDER_AGNES in get_registered_backends()
        backend = create_backend(PROVIDER_AGNES, api_key="sk-test", base_url="https://x/v1")
        assert backend.name == PROVIDER_AGNES
