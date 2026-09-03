"""AgnesVideoBackend 单元测试（respx 捕获出站请求，假表压缩轮询等待）。"""

from __future__ import annotations

import base64
import re
from collections.abc import Generator
from contextlib import contextmanager
from pathlib import Path
from typing import NamedTuple

import httpx
import pytest
import respx

from lib.providers import PROVIDER_AGNES
from lib.video_backends.agnes import AgnesVideoBackend
from lib.video_backends.base import (
    AmbiguousSubmitError,
    ArtifactDownloadError,
    ProviderResponseStage,
    ResumeExpiredError,
    VideoCapabilityError,
    VideoGenerationRequest,
)
from tests.fakes import bounded_poll_clock, captured_provider_job_ids
from tests.http_capture import capture_http, only_request, request_json

_BASE_URL = "https://x/v1"
_GATEWAY_BASE_URL = "https://apihub.agnes-ai.com/v1"
# 成片 CDN 与网关成片库两个下载域名，供下载路由整体拦截。
_DOWNLOAD_HOSTS = r"https://(cdn\.agnes|platform-outputs\.agnes-ai\.com)/"


class _AgnesRoutes(NamedTuple):
    """Agnes 的四条出站流量：建任务、任务轮询、成片查询、成片下载。"""

    submit: respx.Route
    poll: respx.Route
    query: respx.Route
    download: respx.Route


@contextmanager
def _agnes_api(*, base_url: str = _BASE_URL) -> Generator[_AgnesRoutes]:
    host = base_url.removesuffix("/v1")
    with capture_http() as router:
        yield _AgnesRoutes(
            submit=router.post(f"{base_url}/videos"),
            poll=router.get(url__regex=rf"^{re.escape(base_url)}/videos/[^/]+$"),
            query=router.get(f"{host}/agnesapi"),
            download=router.get(url__regex=rf"^{_DOWNLOAD_HOSTS}"),
        )


def _json(body: dict, status_code: int = 200) -> httpx.Response:
    return httpx.Response(status_code, json=body)


def _queued(task_id: str = "task-1") -> httpx.Response:
    return _json({"task_id": task_id, "status": "queued"})


def _completed(task_id: str = "task-1", url: str = "https://cdn.agnes/out.mp4", **extra) -> dict:
    """完成态响应：成片 URL 落在直接字段 ``url``；``remixed_from_video_id`` 是 remix 来源，
    普通图生/文生视频恒为 null，用 extra 显式覆盖才走旧网关兼容路径。
    """
    body = {
        "task_id": task_id,
        "status": "completed",
        "size": "720x1280",
        "url": url,
        "remixed_from_video_id": None,
    }
    body.update(extra)
    return body


def _request(tmp_path: Path, **overrides) -> VideoGenerationRequest:
    params: dict = {
        "prompt": "p",
        "output_path": tmp_path / "o.mp4",
        "aspect_ratio": "9:16",
        "duration_seconds": 5,
    }
    params.update(overrides)
    return VideoGenerationRequest(**params)


def _write_image(path: Path, payload: bytes) -> Path:
    path.write_bytes(payload)
    return path


def _sent_payload(routes: _AgnesRoutes) -> dict:
    return request_json(only_request(routes.submit))


class TestCapabilities:
    def test_name_and_model(self):
        backend = AgnesVideoBackend(api_key="sk-test", base_url=_GATEWAY_BASE_URL)
        assert backend.name == PROVIDER_AGNES
        assert backend.model == "agnes-video-v2.0"

    def test_default_model_when_unset(self):
        backend = AgnesVideoBackend(api_key="sk-test")
        assert backend.model == "agnes-video-v2.0"

    def test_video_capabilities(self):
        backend = AgnesVideoBackend(api_key="sk-test")
        caps = backend.video_capabilities
        assert caps.first_frame is True
        assert caps.last_frame is True
        assert caps.max_reference_images == 4


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
        assert width % 8 == 0
        assert height % 8 == 0


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

    def test_extract_duration_reads_top_level_seconds_not_usage(self):
        from lib.video_backends.agnes import _extract_duration_seconds

        # 顶层 seconds 是成片时长真相源；usage.duration_seconds 是任务处理耗时，不得读取
        assert _extract_duration_seconds({"seconds": "8.0", "usage": {"duration_seconds": 184}}, None, fallback=5) == 8
        assert _extract_duration_seconds({"seconds": "7"}, None, fallback=5) == 7
        # seconds 缺失（含非正值/不可解析）一律回落请求时长，不看 usage
        assert _extract_duration_seconds({"usage": {"duration_seconds": 184}}, None, fallback=5) == 5
        assert _extract_duration_seconds({"seconds": "0"}, None, fallback=5) == 5
        assert _extract_duration_seconds({}, None, fallback=5) == 5
        # 终态无 seconds 时改读 video_id 二次查询响应的 seconds
        assert _extract_duration_seconds({}, {"seconds": "8.0"}, fallback=5) == 8
        # 终态与查询响应均无 seconds 才回落请求时长
        assert _extract_duration_seconds({}, {}, fallback=5) == 5


class TestTextToVideo:
    async def test_happy_path_submits_and_polls(self, tmp_path: Path):
        with _agnes_api(base_url=_GATEWAY_BASE_URL) as routes:
            routes.submit.mock(return_value=_queued("task-42"))
            routes.poll.mock(return_value=_json(_completed("task-42", seconds="5.0")))
            routes.download.mock(return_value=httpx.Response(200, content=b"mp4-bytes"))

            backend = AgnesVideoBackend(api_key="sk-test", base_url=_GATEWAY_BASE_URL)
            result = await backend.generate(
                _request(
                    tmp_path,
                    prompt="A cat running",
                    output_path=tmp_path / "out.mp4",
                    resolution="720p",
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

        submitted = only_request(routes.submit)
        assert str(submitted.url) == f"{_GATEWAY_BASE_URL}/videos"
        body = request_json(submitted)
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
        assert submitted.headers["Authorization"] == "Bearer sk-test"
        # submit 用长超时覆盖上游长阻塞
        assert submitted.extensions["timeout"]["read"] == 300.0

        # 下载打完成态的成片 URL，不带鉴权头
        downloaded = only_request(routes.download)
        assert str(downloaded.url) == "https://cdn.agnes/out.mp4"
        assert "Authorization" not in downloaded.headers

    async def test_polls_through_in_progress(self, tmp_path: Path):
        in_progress = _json({"task_id": "t3", "status": "in_progress", "progress": 40})

        with _agnes_api() as routes, bounded_poll_clock():
            routes.submit.mock(return_value=_queued("t3"))
            routes.poll.mock(side_effect=[in_progress, in_progress, _json(_completed("t3"))])
            routes.download.mock(return_value=httpx.Response(200, content=b"v"))

            backend = AgnesVideoBackend(api_key="k", base_url=_BASE_URL)
            result = await backend.generate(_request(tmp_path))

            assert routes.poll.call_count == 3
            assert routes.download.call_count == 1

        assert result.task_id == "t3"


class TestImageChannels:
    async def test_start_image_is_bare_base64_top_level_image(self, tmp_path: Path):
        """起始图为裸 base64（无 data: 前缀），未复用 data-URI helper。"""
        img_bytes = b"\x89PNG\r\nfake-start"
        img_path = _write_image(tmp_path / "start.png", img_bytes)

        with _agnes_api() as routes:
            routes.submit.mock(return_value=_queued("t1"))
            routes.poll.mock(return_value=_json(_completed("t1")))

            backend = AgnesVideoBackend(api_key="k", base_url=_BASE_URL)
            await backend.generate(_request(tmp_path, start_image=img_path))

            body = _sent_payload(routes)

        sent = body["image"]
        assert sent == base64.b64encode(img_bytes).decode("ascii")
        # 裸 base64，绝不带 data: 前缀
        assert not sent.startswith("data:")
        assert "extra_body" not in body

    async def test_first_last_keyframes_extra_body(self, tmp_path: Path):
        start = _write_image(tmp_path / "s.png", b"start-bytes")
        end = _write_image(tmp_path / "e.png", b"end-bytes")

        with _agnes_api() as routes:
            routes.submit.mock(return_value=_queued("t-kf"))
            routes.poll.mock(return_value=_json(_completed("t-kf")))

            backend = AgnesVideoBackend(api_key="k", base_url=_BASE_URL)
            await backend.generate(_request(tmp_path, start_image=start, end_image=end))

            body = _sent_payload(routes)

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

        with _agnes_api() as routes:
            routes.submit.mock(return_value=_queued("t-ref"))
            routes.poll.mock(return_value=_json(_completed("t-ref")))

            backend = AgnesVideoBackend(api_key="k", base_url=_BASE_URL)
            await backend.generate(_request(tmp_path, reference_images=[ref1, ref2]))

            body = _sent_payload(routes)

        assert "image" not in body
        extra = body["extra_body"]
        assert "mode" not in extra  # 参考生视频不带 keyframes mode
        assert extra["image"] == [
            base64.b64encode(b"ref-1").decode("ascii"),
            base64.b64encode(b"ref-2").decode("ascii"),
        ]

    async def test_reference_images_exceeded_raises(self, tmp_path: Path):
        refs = [_write_image(tmp_path / f"r{i}.png", f"r{i}".encode()) for i in range(5)]

        with _agnes_api() as routes:
            backend = AgnesVideoBackend(api_key="k", base_url=_BASE_URL)
            with pytest.raises(VideoCapabilityError) as ei:
                await backend.generate(_request(tmp_path, reference_images=refs))

            assert ei.value.code == "video_reference_images_exceeded"
            assert routes.submit.call_count == 0

    @pytest.mark.parametrize("with_start", [True, False])
    async def test_reference_images_with_frame_fails_loud(self, tmp_path: Path, with_start: bool):
        """参考图与首/尾帧同时给出时 fail-loud（单通道互斥），不静默走参考图分支丢掉关键帧。"""
        ref = _write_image(tmp_path / "r.png", b"ref")
        frame = _write_image(tmp_path / "f.png", b"frame")

        with _agnes_api() as routes:
            backend = AgnesVideoBackend(api_key="k", base_url=_BASE_URL)
            with pytest.raises(VideoCapabilityError) as ei:
                await backend.generate(
                    _request(
                        tmp_path,
                        reference_images=[ref],
                        start_image=frame if with_start else None,
                        end_image=None if with_start else frame,
                    )
                )

            assert ei.value.code == "video_reference_images_with_frames_unsupported"
            assert routes.submit.call_count == 0

    async def test_end_image_only_fails_loud(self, tmp_path: Path):
        """仅提供尾帧（无首帧）时 fail-loud——Agnes 无独立尾帧通道，不静默退化为文生视频。"""
        end = _write_image(tmp_path / "e.png", b"end")

        with _agnes_api() as routes:
            backend = AgnesVideoBackend(api_key="k", base_url=_BASE_URL)
            with pytest.raises(VideoCapabilityError) as ei:
                await backend.generate(_request(tmp_path, end_image=end))

            assert ei.value.code == "video_end_image_requires_start_image"
            assert routes.submit.call_count == 0

    async def test_missing_start_image_fails_loud(self, tmp_path: Path):
        with _agnes_api() as routes:
            backend = AgnesVideoBackend(api_key="k", base_url=_BASE_URL)
            with pytest.raises(VideoCapabilityError) as ei:
                await backend.generate(_request(tmp_path, start_image=tmp_path / "missing.png"))

            assert ei.value.code == "video_start_image_unreadable"
            assert routes.submit.call_count == 0

    async def test_missing_end_image_fails_loud_with_end_code(self, tmp_path: Path):
        """首尾帧模式下尾帧缺失：错误码指向尾帧而非首帧。"""
        start = _write_image(tmp_path / "s.png", b"start-bytes")

        with _agnes_api() as routes:
            backend = AgnesVideoBackend(api_key="k", base_url=_BASE_URL)
            with pytest.raises(VideoCapabilityError) as ei:
                await backend.generate(_request(tmp_path, start_image=start, end_image=tmp_path / "missing-end.png"))

            assert ei.value.code == "video_end_image_unreadable"
            assert routes.submit.call_count == 0

    async def test_empty_path_objects_degrade_to_text_to_video(self, tmp_path: Path):
        """空 Path（``Path("")`` 塌成 ``Path(".")``）应归一化为 None，回落文生视频而非误报无法读取。"""
        with _agnes_api() as routes:
            routes.submit.mock(return_value=_queued("t-empty"))
            routes.poll.mock(return_value=_json(_completed("t-empty")))

            backend = AgnesVideoBackend(api_key="k", base_url=_BASE_URL)
            await backend.generate(_request(tmp_path, start_image=Path(""), reference_images=[Path("")]))

            body = _sent_payload(routes)

        assert "image" not in body
        assert "extra_body" not in body


class TestFailureAndTimeout:
    async def test_failed_status_raises(self, tmp_path: Path):
        with _agnes_api() as routes:
            routes.submit.mock(return_value=_queued("t2"))
            routes.poll.mock(
                return_value=_json({"task_id": "t2", "status": "failed", "error": {"message": "upstream down"}})
            )

            backend = AgnesVideoBackend(api_key="k", base_url=_BASE_URL)
            with pytest.raises(RuntimeError, match="upstream down"):
                await backend.generate(_request(tmp_path))

            assert routes.download.call_count == 0

    async def test_non_failed_terminal_status_fails_fast(self, tmp_path: Path):
        """上游以 cancelled 等非 failed 失败态收尾时快速失败，不轮询到 timeout。"""
        with _agnes_api() as routes:
            routes.submit.mock(return_value=_queued("t-cxl"))
            routes.poll.mock(
                return_value=_json({"task_id": "t-cxl", "status": "cancelled", "error": {"message": "user cancelled"}})
            )

            backend = AgnesVideoBackend(api_key="k", base_url=_BASE_URL)
            with pytest.raises(RuntimeError, match="user cancelled"):
                await backend.generate(_request(tmp_path))

            assert routes.poll.call_count == 1
            assert routes.download.call_count == 0

    async def test_completed_without_video_url_or_video_id_raises(self, tmp_path: Path):
        with _agnes_api() as routes:
            routes.submit.mock(return_value=_queued("t-nourl"))
            routes.poll.mock(return_value=_json({"task_id": "t-nourl", "status": "completed"}))

            backend = AgnesVideoBackend(api_key="k", base_url=_BASE_URL)
            with pytest.raises(RuntimeError, match="缺少成片 URL 与 video_id"):
                await backend.generate(_request(tmp_path))

            assert routes.download.call_count == 0

    async def test_completed_with_null_remixed_from_video_id_uses_video_id_query(self, tmp_path: Path):
        """普通图生/文生视频完成态 remixed_from_video_id 恒为 null，成片地址须按 video_id
        二次查询取得。"""
        recorded: list[tuple[ProviderResponseStage, object]] = []

        async def _record(stage: ProviderResponseStage, body: object) -> None:
            recorded.append((stage, body))

        with _agnes_api(base_url=_GATEWAY_BASE_URL) as routes:
            routes.submit.mock(return_value=_queued("t-null-remix"))
            routes.poll.mock(
                return_value=_json(
                    {
                        "task_id": "t-null-remix",
                        "video_id": "vid-123",
                        "status": "completed",
                        "remixed_from_video_id": None,
                        "seconds": "8.0",
                    }
                )
            )
            routes.query.mock(return_value=_json({"video_id": "vid-123", "url": "https://cdn.agnes/queried.mp4"}))
            routes.download.mock(return_value=httpx.Response(200, content=b"queried-bytes"))

            backend = AgnesVideoBackend(api_key="k", base_url=_GATEWAY_BASE_URL)
            result = await backend.generate(_request(tmp_path, on_provider_response=_record))

            # 二次查询打网关根下的 /agnesapi，按 video_id 传参（不带 /v1，也不用 task_id）
            assert str(only_request(routes.query).url) == "https://apihub.agnes-ai.com/agnesapi?video_id=vid-123"

        assert result.video_uri == "https://cdn.agnes/queried.mp4"
        assert result.duration_seconds == 8
        assert result.video_path.read_bytes() == b"queried-bytes"
        assert [stage for stage, _body in recorded] == ["submit", "poll", "result"]

    async def test_completed_with_direct_url_field_skips_video_id_query(self, tmp_path: Path):
        """完成态直接带 url 字段时直接下载，不发起 video_id 二次查询。"""
        with _agnes_api() as routes:
            routes.submit.mock(return_value=_queued("t-direct"))
            routes.poll.mock(
                return_value=_json(
                    {
                        "task_id": "t-direct",
                        "video_id": "vid-should-not-be-queried",
                        "status": "completed",
                        "url": "https://cdn.agnes/direct.mp4",
                        "remixed_from_video_id": None,
                    }
                )
            )

            backend = AgnesVideoBackend(api_key="k", base_url=_BASE_URL)
            result = await backend.generate(_request(tmp_path))

            assert routes.query.call_count == 0  # 未发起二次查询

        assert result.video_uri == "https://cdn.agnes/direct.mp4"

    async def test_completed_with_url_shaped_remixed_from_video_id_downloads_directly(self, tmp_path: Path):
        """旧网关把成片 URL 回填在 remixed_from_video_id：值是 URL 形态时仍作下载地址，
        不发起 video_id 二次查询。"""
        with _agnes_api() as routes:
            routes.submit.mock(return_value=_queued("t-legacy"))
            routes.poll.mock(
                return_value=_json(
                    {
                        "task_id": "t-legacy",
                        "video_id": "vid-should-not-be-queried",
                        "status": "completed",
                        "remixed_from_video_id": "https://cdn.agnes/legacy.mp4",
                    }
                )
            )

            backend = AgnesVideoBackend(api_key="k", base_url=_BASE_URL)
            result = await backend.generate(_request(tmp_path))

            assert routes.query.call_count == 0

        assert result.video_uri == "https://cdn.agnes/legacy.mp4"

    async def test_remixed_from_video_id_non_url_value_not_used_as_download_url(self, tmp_path: Path):
        """remixed_from_video_id 是非 URL 形态的 remix 来源 ID 时不得当下载地址，转向 video_id 二次查询。"""
        with _agnes_api() as routes:
            routes.submit.mock(return_value=_queued("t-remix-id"))
            routes.poll.mock(
                return_value=_json(
                    {
                        "task_id": "t-remix-id",
                        "video_id": "vid-456",
                        "status": "completed",
                        # 非 URL 形态：真正的 remix 来源 ID
                        "remixed_from_video_id": "src-video-abc",
                    }
                )
            )
            routes.query.mock(return_value=_json({"url": "https://cdn.agnes/from-query.mp4"}))

            backend = AgnesVideoBackend(api_key="k", base_url=_BASE_URL)
            result = await backend.generate(_request(tmp_path))

        assert result.video_uri == "https://cdn.agnes/from-query.mp4"

    async def test_metadata_url_takes_priority_over_url_shaped_remixed_from_video_id(self, tmp_path: Path):
        """顶层 remixed_from_video_id 是 URL 形态、metadata.url 也存在时，metadata.url
        才是权威成片地址，remixed_from_video_id 只是兼容兜底，不得抢先命中。"""
        with _agnes_api() as routes:
            routes.submit.mock(return_value=_queued("t-meta-priority"))
            routes.poll.mock(
                return_value=_json(
                    {
                        "task_id": "t-meta-priority",
                        "status": "completed",
                        "remixed_from_video_id": "https://cdn.agnes/legacy-compat.mp4",
                        "metadata": {"url": "https://cdn.agnes/authoritative.mp4"},
                    }
                )
            )

            backend = AgnesVideoBackend(api_key="k", base_url=_BASE_URL)
            result = await backend.generate(_request(tmp_path))

            # 顶层/metadata 已命中权威字段，未发起二次查询
            assert routes.query.call_count == 0

        assert result.video_uri == "https://cdn.agnes/authoritative.mp4"

    async def test_video_id_query_url_under_metadata_is_used(self, tmp_path: Path):
        """成片查询把下载地址放在 metadata.url：顶层无直接 URL 字段时下探 metadata 取到。"""
        with _agnes_api() as routes:
            routes.submit.mock(return_value=_queued("t-meta"))
            routes.poll.mock(
                return_value=_json(
                    {
                        "task_id": "t-meta",
                        "video_id": "vid-meta",
                        "status": "completed",
                        "remixed_from_video_id": None,
                    }
                )
            )
            routes.query.mock(
                return_value=_json(
                    {
                        "video_id": "vid-meta",
                        "status": "completed",
                        "seconds": "8.0",
                        "metadata": {"url": "https://platform-outputs.agnes-ai.com/videos/meta.mp4"},
                    }
                )
            )
            routes.download.mock(return_value=httpx.Response(200, content=b"meta-bytes"))

            backend = AgnesVideoBackend(api_key="k", base_url=_BASE_URL)
            result = await backend.generate(_request(tmp_path))

        assert result.video_uri == "https://platform-outputs.agnes-ai.com/videos/meta.mp4"
        assert result.video_path.read_bytes() == b"meta-bytes"
        # 终态响应无 seconds，时长须从二次查询响应解析，而非回落请求时长
        assert result.duration_seconds == 8

    async def test_video_id_query_without_url_raises_descriptive_error(self, tmp_path: Path):
        with _agnes_api() as routes:
            routes.submit.mock(return_value=_queued("t-bad-query"))
            routes.poll.mock(
                return_value=_json({"task_id": "t-bad-query", "video_id": "vid-789", "status": "completed"})
            )
            routes.query.mock(return_value=_json({"video_id": "vid-789", "status": "unexpected"}))

            backend = AgnesVideoBackend(api_key="k", base_url=_BASE_URL)
            with pytest.raises(RuntimeError, match="video_id 查询响应缺少成片 URL"):
                await backend.generate(_request(tmp_path))

            assert routes.download.call_count == 0

    async def test_polling_timeout_raises(self, tmp_path: Path):
        """终态迟迟不来时按 max_wait 抛 TimeoutError，不无限轮询下去。"""
        with _agnes_api() as routes, bounded_poll_clock():
            routes.submit.mock(return_value=_queued("t-timeout"))
            routes.poll.mock(return_value=_json({"task_id": "t-timeout", "status": "in_progress"}))

            backend = AgnesVideoBackend(api_key="k", base_url=_BASE_URL)
            with pytest.raises(TimeoutError, match="Agnes"):
                await backend.generate(_request(tmp_path))

            assert routes.poll.call_count > 1
            assert routes.download.call_count == 0


class TestSubmitResilience:
    async def test_submit_retries_on_503_busy(self, tmp_path: Path):
        """503 Service busy → 经 should_retry_submit 的 status_code 闸门重试。"""
        busy = httpx.Response(503, text="Service busy")

        with _agnes_api() as routes, bounded_poll_clock():
            routes.submit.mock(side_effect=[busy, busy, _queued("t-retry")])
            routes.poll.mock(return_value=_json(_completed("t-retry")))

            backend = AgnesVideoBackend(api_key="k", base_url=_BASE_URL)
            result = await backend.generate(_request(tmp_path))

            assert routes.submit.call_count == 3

        assert result.task_id == "t-retry"

    async def test_submit_non_retryable_4xx_fails_fast(self, tmp_path: Path):
        with _agnes_api() as routes, bounded_poll_clock():
            routes.submit.mock(return_value=_json({"error": "bad request"}, status_code=400))

            backend = AgnesVideoBackend(api_key="k", base_url=_BASE_URL)
            with pytest.raises(httpx.HTTPStatusError):
                await backend.generate(_request(tmp_path))

            assert routes.submit.call_count == 1
            assert routes.poll.call_count == 0  # 4xx 在提交阶段失败，不该轮询

    async def test_submit_read_timeout_wraps_ambiguous(self, tmp_path: Path):
        with _agnes_api() as routes, bounded_poll_clock():
            routes.submit.mock(side_effect=httpx.ReadTimeout("read timed out"))

            backend = AgnesVideoBackend(api_key="k", base_url=_BASE_URL)
            with pytest.raises(AmbiguousSubmitError):
                await backend.generate(_request(tmp_path))

            assert routes.submit.call_count == 1
            assert routes.poll.call_count == 0  # 歧义态不该轮询


class TestResume:
    async def test_download_failure_can_resume_without_resubmit(self, tmp_path: Path):
        download_ready = False

        def download_response(_request: httpx.Request) -> httpx.Response:
            if download_ready:
                return httpx.Response(200, content=b"resumed")
            return httpx.Response(503, text="cdn unavailable")

        with _agnes_api() as routes, bounded_poll_clock(), captured_provider_job_ids() as persisted:
            routes.submit.mock(return_value=_queued("task-download"))
            routes.poll.mock(return_value=_json(_completed("task-download", "https://cdn.agnes/resumed.mp4")))
            routes.download.mock(side_effect=download_response)
            request = _request(tmp_path, task_id="worker-task", poll_timeout_seconds=1800)
            backend = AgnesVideoBackend(api_key="k", base_url=_BASE_URL)

            with pytest.raises(ArtifactDownloadError) as caught:
                await backend.generate(request)

            assert caught.value.code == "artifact_download_failed"
            download_ready = True
            result = await backend.resume_video(persisted[0]["job_id"], request)

            assert routes.submit.call_count == 1

        assert result.video_path.read_bytes() == b"resumed"

    async def test_resume_polls_existing_job_no_submit(self, tmp_path: Path):
        with _agnes_api() as routes:
            routes.poll.mock(return_value=_json(_completed("task-resume", "https://cdn.agnes/resumed.mp4")))
            routes.download.mock(return_value=httpx.Response(200, content=b"resumed"))

            backend = AgnesVideoBackend(api_key="k", base_url=_BASE_URL)
            result = await backend.resume_video("task-resume", _request(tmp_path, output_path=tmp_path / "out.mp4"))

            assert routes.submit.call_count == 0  # resume 不 POST create
            assert only_request(routes.poll).url.path.endswith("/videos/task-resume")

        assert result.task_id == "task-resume"
        assert (tmp_path / "out.mp4").read_bytes() == b"resumed"

    async def test_resume_completed_with_only_video_id_queries_and_downloads(self, tmp_path: Path):
        """resume 已完成任务、完成态只带 video_id 时，与首跑共用同一套终态解析：经二次查询取回
        成片，全程不 POST 建任务（不重复计费）。
        """
        with _agnes_api(base_url=_GATEWAY_BASE_URL) as routes:
            routes.poll.mock(
                return_value=_json(
                    {
                        "task_id": "task-resume-vid",
                        "video_id": "vid-resume",
                        "status": "completed",
                        "remixed_from_video_id": None,
                        "seconds": "8.0",
                    }
                )
            )
            routes.query.mock(
                return_value=_json({"video_id": "vid-resume", "url": "https://cdn.agnes/resume-queried.mp4"})
            )
            routes.download.mock(return_value=httpx.Response(200, content=b"resume-queried"))

            backend = AgnesVideoBackend(api_key="k", base_url=_GATEWAY_BASE_URL)
            result = await backend.resume_video("task-resume-vid", _request(tmp_path, output_path=tmp_path / "out.mp4"))

            assert routes.submit.call_count == 0
            assert only_request(routes.poll).url.path.endswith("/videos/task-resume-vid")
            assert str(only_request(routes.query).url) == "https://apihub.agnes-ai.com/agnesapi?video_id=vid-resume"

        assert result.video_uri == "https://cdn.agnes/resume-queried.mp4"
        assert result.duration_seconds == 8
        assert (tmp_path / "out.mp4").read_bytes() == b"resume-queried"

    async def test_resume_404_raises_resume_expired_without_retry(self, tmp_path: Path):
        with _agnes_api() as routes:
            routes.poll.mock(return_value=_json({"error": "task not found"}, status_code=404))

            backend = AgnesVideoBackend(api_key="k", base_url=_BASE_URL)
            with pytest.raises(ResumeExpiredError) as ei:
                await backend.resume_video("task-404", _request(tmp_path, output_path=tmp_path / "out.mp4"))

            assert ei.value.job_id == "task-404"
            assert ei.value.provider == PROVIDER_AGNES
            assert routes.poll.call_count == 1


class TestDurationValidation:
    @pytest.mark.parametrize("duration", [0, 19, 30])
    async def test_out_of_range_duration_fails_loud_without_submit(self, tmp_path: Path, duration: int):
        """越界时长（< 1 或 > 18）在建单前 fail-loud，不静默截帧到 441、不 POST、不错记计费时长。"""
        with _agnes_api() as routes:
            backend = AgnesVideoBackend(api_key="k", base_url=_BASE_URL)
            with pytest.raises(VideoCapabilityError) as ei:
                await backend.generate(_request(tmp_path, duration_seconds=duration))

            assert ei.value.code == "video_duration_not_supported"
            assert routes.submit.call_count == 0


class TestProviderJobIdPersistence:
    async def test_persists_agnes_task_id_for_worker_request(self, tmp_path: Path):
        """worker 路径（request.task_id 非空）下，submit 返回的 Agnes task_id 作为 job_id 写回，覆盖 resume 契约。"""
        with _agnes_api() as routes, captured_provider_job_ids() as persisted:
            routes.submit.mock(return_value=_queued("agnes-task-42"))
            routes.poll.mock(return_value=_json(_completed("agnes-task-42")))

            backend = AgnesVideoBackend(api_key="k", base_url=_BASE_URL)
            await backend.generate(_request(tmp_path, task_id="worker-task-99"))

        assert persisted == [
            {
                "task_id": "worker-task-99",  # worker 任务 id
                "job_id": "agnes-task-42",  # Agnes submit 返回的 task_id 作为 job_id 写回
                "provider": PROVIDER_AGNES,
                "endpoint": None,
                "base_url": None,
            }
        ]

    async def test_non_worker_request_skips_persistence(self, tmp_path: Path):
        """非 worker 路径（task_id=None）不调用持久化，避免空 task_id 写库。"""
        with _agnes_api() as routes, captured_provider_job_ids() as persisted:
            routes.submit.mock(return_value=_queued("agnes-task-1"))
            routes.poll.mock(return_value=_json(_completed("agnes-task-1")))

            backend = AgnesVideoBackend(api_key="k", base_url=_BASE_URL)
            await backend.generate(_request(tmp_path))

        assert persisted == []


class TestRegistration:
    def test_registered_in_video_backend_registry(self):
        from lib.video_backends import create_backend, get_registered_backends

        assert PROVIDER_AGNES in get_registered_backends()
        backend = create_backend(PROVIDER_AGNES, api_key="sk-test", base_url=_BASE_URL)
        assert backend.name == PROVIDER_AGNES


class TestPollErrorRedaction:
    """轮询与成片查询的 4xx 异常消息会经 str(exc) 落进 task.error_message 与日志；按 query
    传的凭证渲染在 raise_for_status 写进消息的那条 URL 里，须先脱敏再抛。"""

    async def test_poll_4xx_message_drops_query_credentials(self):
        backend = AgnesVideoBackend(api_key="k", base_url=_BASE_URL)
        with _agnes_api() as routes:
            routes.poll.mock(return_value=_json({"message": "forbidden"}, 403))
            async with httpx.AsyncClient(params={"api_key": "SECRETKEY"}) as client:
                with pytest.raises(httpx.HTTPStatusError) as excinfo:
                    await backend._poll_once(client, "task-1")

        assert "SECRETKEY" not in str(excinfo.value)
        assert excinfo.value.response.status_code == 403

    async def test_video_query_4xx_message_drops_query_credentials(self, tmp_path: Path):
        backend = AgnesVideoBackend(api_key="k", base_url=_BASE_URL)
        with _agnes_api() as routes:
            routes.query.mock(return_value=_json({"message": "forbidden"}, 403))
            async with httpx.AsyncClient(params={"api_key": "SECRETKEY"}) as client:
                with pytest.raises(httpx.HTTPStatusError) as excinfo:
                    await backend._query_video(client, "vid-1", _request(tmp_path))

        assert "SECRETKEY" not in str(excinfo.value)
        assert excinfo.value.response.status_code == 403
