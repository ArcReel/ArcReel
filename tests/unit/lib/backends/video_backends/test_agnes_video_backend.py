"""AgnesVideoBackend respx outbound tests with fake-clock compressed polling."""

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

from arcreel_market_core.video_backend_contract import (
    ProviderResponseStage,
    ResumeExpiredError,
    VideoAudioMode,
    VideoCapabilityError,
    VideoGenerationRequest,
)
from lib.backends.http_status_errors import AmbiguousSubmitError, ArtifactDownloadError
from lib.backends.providers import PROVIDER_AGNES
from lib.backends.video_backends.agnes import Agnes25VideoBackend, AgnesVideoBackend
from tests.fakes import bounded_poll_clock, captured_provider_job_ids
from tests.http_capture import capture_http, only_request, request_json

_BASE_URL = "https://x/v1"
_GATEWAY_BASE_URL = "https://apihub.agnes-ai.com/v1"
# Intercept both output CDN and gateway-library download domains.
_DOWNLOAD_HOSTS = r"https://(cdn\.agnes|platform-outputs\.agnes-ai\.com)/"


class _AgnesRoutes(NamedTuple):
    """Agnes outbound flows: creation, polling, output lookup, and downloads."""

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
    """Completed responses place URLs directly in ``url``; ``remixed_from_video_id`` identifies remix sources.
    Ordinary image/text-to-video keeps it null; explicit extra overrides exercise legacy gateway compatibility.
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
    def test_name_and_default_model(self):
        backend = AgnesVideoBackend(api_key="sk-test", base_url=_GATEWAY_BASE_URL)
        assert backend.name == PROVIDER_AGNES
        assert backend.model == "agnes-video-v2.0"

    def test_default_model_when_unset(self):
        backend = AgnesVideoBackend(api_key="sk-test")
        assert backend.model == "agnes-video-v2.0"

    def test_default_v25_flash_capabilities(self):
        backend = Agnes25VideoBackend(api_key="sk-test")
        caps = backend.video_capabilities
        assert caps.first_frame is True
        assert caps.last_frame is True
        # v2.5 channels declare no count cap; model limits come from entries.
        assert caps.max_reference_images is None
        assert caps.audio_track is VideoAudioMode.ALWAYS_ON

    @pytest.mark.parametrize(
        ("model", "max_reference_images", "audio_track"),
        [
            ("agnes-video-v2.0", 4, VideoAudioMode.ALWAYS_OFF),
            ("agnes-video-2.5-flash", 5, VideoAudioMode.ALWAYS_ON),
            ("agnes-video-2.5", 8, VideoAudioMode.ALWAYS_ON),
        ],
    )
    def test_video_capabilities_by_model(self, model: str, max_reference_images: int, audio_track: VideoAudioMode):
        from lib.backends.backend_assembly.specs import builtin_video_capabilities_for_model

        caps = builtin_video_capabilities_for_model("agnes", model)
        assert caps.first_frame is True
        assert caps.last_frame is True
        assert caps.max_reference_images == max_reference_images
        assert caps.audio_track is audio_track


class TestNumFramesAndSize:
    @pytest.mark.parametrize(
        ("duration", "expected_frames"),
        [(1, 25), (3, 73), (5, 121), (10, 241), (18, 433), (30, 721)],
    )
    def test_duration_to_num_frames_aligns_to_8n_plus_1(self, duration: int, expected_frames: int):
        from lib.backends.video_backends.agnes import _duration_to_num_frames

        frames = _duration_to_num_frames(duration)
        assert frames == expected_frames
        assert (frames - 1) % 8 == 0  # Form 8n+1.

    def test_resolve_size_portrait_explicit_hw(self):
        from lib.backends.video_backends.agnes import _resolve_size

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
            ("9.6", 10),  # Half-up rounding avoids underbilling seconds.
            ("9.4", 9),
            (4.5, 5),
            (0, None),  # Nonpositive values return None for requested-duration fallback.
            ("0", None),
            (-3, None),
            ("abc", None),
            (None, None),
        ],
    )
    def test_coerce_duration(self, value: object, expected: int | None):
        from lib.backends.video_backends.agnes import _coerce_duration

        assert _coerce_duration(value) == expected

    def test_extract_duration_reads_top_level_seconds_not_usage(self):
        from lib.backends.video_backends.agnes import _extract_duration_seconds

        # Top-level seconds solely describes output duration; usage.duration_seconds is processing time, never consumed.
        assert _extract_duration_seconds({"seconds": "8.0", "usage": {"duration_seconds": 184}}, None, fallback=5) == 8
        assert _extract_duration_seconds({"seconds": "7"}, None, fallback=5) == 7
        # Absent/nonpositive/unparseable seconds falls back to request duration, ignoring usage.
        assert _extract_duration_seconds({"usage": {"duration_seconds": 184}}, None, fallback=5) == 5
        assert _extract_duration_seconds({"seconds": "0"}, None, fallback=5) == 5
        assert _extract_duration_seconds({}, None, fallback=5) == 5
        # Missing terminal seconds reads secondary video_id query seconds.
        assert _extract_duration_seconds({}, {"seconds": "8.0"}, fallback=5) == 8
        # Fall back to request duration only if neither terminal nor lookup has seconds.
        assert _extract_duration_seconds({}, {}, fallback=5) == 5


class TestTextToVideo:
    async def test_reference_audio_rejected_before_submit(self, tmp_path: Path):
        """Payloads lack reference-audio fields; reject instead of discarding audio and submitting."""
        audio = tmp_path / "voice.mp3"
        audio.write_bytes(b"ID3")
        with _agnes_api(base_url=_GATEWAY_BASE_URL) as routes:
            backend = AgnesVideoBackend(api_key="sk-test", model="agnes-video-v2.0", base_url=_GATEWAY_BASE_URL)
            with pytest.raises(VideoCapabilityError) as exc:
                await backend.generate(_request(tmp_path, reference_audio_files=[audio]))
        assert exc.value.code == "video_reference_audio_unsupported"
        assert not routes.submit.called

    async def test_happy_path_submits_and_polls(self, tmp_path: Path):
        with _agnes_api(base_url=_GATEWAY_BASE_URL) as routes:
            routes.submit.mock(return_value=_queued("task-42"))
            routes.poll.mock(return_value=_json(_completed("task-42", seconds="5.0")))
            routes.download.mock(return_value=httpx.Response(200, content=b"mp4-bytes"))

            backend = AgnesVideoBackend(api_key="sk-test", model="agnes-video-v2.0", base_url=_GATEWAY_BASE_URL)
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
        # Agnes lacks audio capability; outputs are always silent.
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
        # Text-to-video uses no image channels.
        assert "image" not in body
        assert "extra_body" not in body
        assert submitted.headers["Authorization"] == "Bearer sk-test"
        # Long submit timeouts cover prolonged upstream blocking.
        assert submitted.extensions["timeout"]["read"] == 300.0

        # Download completed-output URLs without auth headers.
        downloaded = only_request(routes.download)
        assert str(downloaded.url) == "https://cdn.agnes/out.mp4"
        assert "Authorization" not in downloaded.headers

    async def test_submit_prefers_task_id_for_v20(self, tmp_path: Path):
        """v2.0 polls task_id; simultaneous video_id never replaces it."""
        with _agnes_api(base_url=_GATEWAY_BASE_URL) as routes:
            routes.submit.mock(return_value=_json({"task_id": "task-v20", "video_id": "video-v20", "status": "queued"}))
            routes.poll.mock(return_value=_json(_completed("task-v20", seconds="5")))
            routes.download.mock(return_value=httpx.Response(200, content=b"mp4-bytes"))

            backend = AgnesVideoBackend(api_key="sk-test", model="agnes-video-v2.0", base_url=_GATEWAY_BASE_URL)
            result = await backend.generate(_request(tmp_path))

        assert result.task_id == "task-v20"
        assert only_request(routes.poll).url.path == "/v1/videos/task-v20"

    async def test_polls_through_in_progress(self, tmp_path: Path):
        in_progress = _json({"task_id": "t3", "status": "in_progress", "progress": 40})

        with _agnes_api() as routes, bounded_poll_clock():
            routes.submit.mock(return_value=_queued("t3"))
            routes.poll.mock(side_effect=[in_progress, in_progress, _json(_completed("t3"))])
            routes.download.mock(return_value=httpx.Response(200, content=b"v"))

            backend = AgnesVideoBackend(api_key="k", model="agnes-video-v2.0", base_url=_BASE_URL)
            result = await backend.generate(_request(tmp_path))

            assert routes.poll.call_count == 3
            assert routes.download.call_count == 1

        assert result.task_id == "t3"


class TestImageChannels:
    async def test_start_image_is_bare_base64_top_level_image(self, tmp_path: Path):
        """Start images use raw base64 without data: prefixes, not data-URI helpers."""
        img_bytes = b"\x89PNG\r\nfake-start"
        img_path = _write_image(tmp_path / "start.png", img_bytes)

        with _agnes_api() as routes:
            routes.submit.mock(return_value=_queued("t1"))
            routes.poll.mock(return_value=_json(_completed("t1")))

            backend = AgnesVideoBackend(api_key="k", model="agnes-video-v2.0", base_url=_BASE_URL)
            await backend.generate(_request(tmp_path, start_image=img_path))

            body = _sent_payload(routes)

        sent = body["image"]
        assert sent == base64.b64encode(img_bytes).decode("ascii")
        # Raw base64, never data: prefixes.
        assert not sent.startswith("data:")
        assert "extra_body" not in body

    async def test_first_last_keyframes_extra_body(self, tmp_path: Path):
        start = _write_image(tmp_path / "s.png", b"start-bytes")
        end = _write_image(tmp_path / "e.png", b"end-bytes")

        with _agnes_api() as routes:
            routes.submit.mock(return_value=_queued("t-kf"))
            routes.poll.mock(return_value=_json(_completed("t-kf")))

            backend = AgnesVideoBackend(api_key="k", model="agnes-video-v2.0", base_url=_BASE_URL)
            await backend.generate(_request(tmp_path, start_image=start, end_image=end))

            body = _sent_payload(routes)

        assert "image" not in body  # Single channel puts keyframes into extra_body, not top-level image.
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

            backend = AgnesVideoBackend(api_key="k", model="agnes-video-v2.0", base_url=_BASE_URL)
            await backend.generate(_request(tmp_path, reference_images=[ref1, ref2]))

            body = _sent_payload(routes)

        assert "image" not in body
        extra = body["extra_body"]
        assert "mode" not in extra  # Reference-to-video omits keyframes mode.
        assert extra["image"] == [
            base64.b64encode(b"ref-1").decode("ascii"),
            base64.b64encode(b"ref-2").decode("ascii"),
        ]

    async def test_reference_images_exceeded_raises(self, tmp_path: Path):
        refs = [_write_image(tmp_path / f"r{i}.png", f"r{i}".encode()) for i in range(5)]

        with _agnes_api() as routes:
            backend = AgnesVideoBackend(api_key="k", model="agnes-video-v2.0", base_url=_BASE_URL)
            with pytest.raises(VideoCapabilityError) as ei:
                await backend.generate(_request(tmp_path, reference_images=refs))

            assert ei.value.code == "video_reference_images_exceeded"
            assert routes.submit.call_count == 0

    @pytest.mark.parametrize("with_start", [True, False])
    async def test_reference_images_with_frame_fails_loud(self, tmp_path: Path, with_start: bool):
        """References plus first/last frames fail loudly as mutually exclusive channels; never discard keyframes by taking references."""
        ref = _write_image(tmp_path / "r.png", b"ref")
        frame = _write_image(tmp_path / "f.png", b"frame")

        with _agnes_api() as routes:
            backend = AgnesVideoBackend(api_key="k", model="agnes-video-v2.0", base_url=_BASE_URL)
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
        """Last-frame-only requests fail loudly: Agnes has no independent last-frame channel and must not silently become text-to-video."""
        end = _write_image(tmp_path / "e.png", b"end")

        with _agnes_api() as routes:
            backend = AgnesVideoBackend(api_key="k", model="agnes-video-v2.0", base_url=_BASE_URL)
            with pytest.raises(VideoCapabilityError) as ei:
                await backend.generate(_request(tmp_path, end_image=end))

            assert ei.value.code == "video_end_image_requires_start_image"
            assert routes.submit.call_count == 0

    async def test_missing_start_image_fails_loud(self, tmp_path: Path):
        with _agnes_api() as routes:
            backend = AgnesVideoBackend(api_key="k", model="agnes-video-v2.0", base_url=_BASE_URL)
            with pytest.raises(VideoCapabilityError) as ei:
                await backend.generate(_request(tmp_path, start_image=tmp_path / "missing.png"))

            assert ei.value.code == "video_start_image_unreadable"
            assert routes.submit.call_count == 0

    async def test_missing_end_image_fails_loud_with_end_code(self, tmp_path: Path):
        """Missing last frames in first/last mode report last-frame codes, not first-frame codes."""
        start = _write_image(tmp_path / "s.png", b"start-bytes")

        with _agnes_api() as routes:
            backend = AgnesVideoBackend(api_key="k", model="agnes-video-v2.0", base_url=_BASE_URL)
            with pytest.raises(VideoCapabilityError) as ei:
                await backend.generate(_request(tmp_path, start_image=start, end_image=tmp_path / "missing-end.png"))

            assert ei.value.code == "video_end_image_unreadable"
            assert routes.submit.call_count == 0

    async def test_empty_path_objects_degrade_to_text_to_video(self, tmp_path: Path):
        """Empty paths, ``Path("")`` becoming ``Path(".")``, normalize None and fall back text-to-video rather than false unreadability."""
        with _agnes_api() as routes:
            routes.submit.mock(return_value=_queued("t-empty"))
            routes.poll.mock(return_value=_json(_completed("t-empty")))

            backend = AgnesVideoBackend(api_key="k", model="agnes-video-v2.0", base_url=_BASE_URL)
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

            backend = AgnesVideoBackend(api_key="k", model="agnes-video-v2.0", base_url=_BASE_URL)
            with pytest.raises(RuntimeError, match="upstream down"):
                await backend.generate(_request(tmp_path))

            assert routes.download.call_count == 0

    async def test_non_failed_terminal_status_fails_fast(self, tmp_path: Path):
        """Non-failed failure states such as cancelled terminate quickly, never poll until timeout."""
        with _agnes_api() as routes:
            routes.submit.mock(return_value=_queued("t-cxl"))
            routes.poll.mock(
                return_value=_json({"task_id": "t-cxl", "status": "cancelled", "error": {"message": "user cancelled"}})
            )

            backend = AgnesVideoBackend(api_key="k", model="agnes-video-v2.0", base_url=_BASE_URL)
            with pytest.raises(RuntimeError, match="user cancelled"):
                await backend.generate(_request(tmp_path))

            assert routes.poll.call_count == 1
            assert routes.download.call_count == 0

    async def test_completed_without_video_url_or_video_id_raises(self, tmp_path: Path):
        with _agnes_api() as routes:
            routes.submit.mock(return_value=_queued("t-nourl"))
            routes.poll.mock(return_value=_json({"task_id": "t-nourl", "status": "completed"}))

            backend = AgnesVideoBackend(api_key="k", model="agnes-video-v2.0", base_url=_BASE_URL)
            with pytest.raises(RuntimeError, match="缺少成片 URL 与 video_id"):
                await backend.generate(_request(tmp_path))

            assert routes.download.call_count == 0

    async def test_completed_with_null_remixed_from_video_id_uses_video_id_query(self, tmp_path: Path):
        """Ordinary image/text-to-video completed remixed_from_video_id stays null; resolve outputs through
        secondary video_id queries."""
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

            backend = AgnesVideoBackend(api_key="k", model="agnes-video-v2.0", base_url=_GATEWAY_BASE_URL)
            result = await backend.generate(_request(tmp_path, on_provider_response=_record))

            # Query host-root /agnesapi by video_id, without /v1 or task_id.
            assert str(only_request(routes.query).url) == "https://apihub.agnes-ai.com/agnesapi?video_id=vid-123"

        assert result.video_uri == "https://cdn.agnes/queried.mp4"
        assert result.duration_seconds == 8
        assert result.video_path.read_bytes() == b"queried-bytes"
        assert [stage for stage, _body in recorded] == ["submit", "poll", "result"]

    async def test_completed_with_direct_url_field_skips_video_id_query(self, tmp_path: Path):
        """Direct completed url fields download immediately without secondary video_id queries."""
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

            backend = AgnesVideoBackend(api_key="k", model="agnes-video-v2.0", base_url=_BASE_URL)
            result = await backend.generate(_request(tmp_path))

            assert routes.query.call_count == 0  # No secondary query.

        assert result.video_uri == "https://cdn.agnes/direct.mp4"

    async def test_completed_with_url_shaped_remixed_from_video_id_downloads_directly(self, tmp_path: Path):
        """Legacy URL-shaped remixed_from_video_id remains a download fallback, without secondary video_id queries."""
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

            backend = AgnesVideoBackend(api_key="k", model="agnes-video-v2.0", base_url=_BASE_URL)
            result = await backend.generate(_request(tmp_path))

            assert routes.query.call_count == 0

        assert result.video_uri == "https://cdn.agnes/legacy.mp4"

    async def test_remixed_from_video_id_non_url_value_not_used_as_download_url(self, tmp_path: Path):
        """Non-URL remix-source IDs in remixed_from_video_id are not downloads; use secondary video_id queries."""
        with _agnes_api() as routes:
            routes.submit.mock(return_value=_queued("t-remix-id"))
            routes.poll.mock(
                return_value=_json(
                    {
                        "task_id": "t-remix-id",
                        "video_id": "vid-456",
                        "status": "completed",
                        # Actual non-URL remix-source ID.
                        "remixed_from_video_id": "src-video-abc",
                    }
                )
            )
            routes.query.mock(return_value=_json({"url": "https://cdn.agnes/from-query.mp4"}))

            backend = AgnesVideoBackend(api_key="k", model="agnes-video-v2.0", base_url=_BASE_URL)
            result = await backend.generate(_request(tmp_path))

        assert result.video_uri == "https://cdn.agnes/from-query.mp4"

    async def test_metadata_url_takes_priority_over_url_shaped_remixed_from_video_id(self, tmp_path: Path):
        """When metadata.url coexists with URL-shaped remixed_from_video_id, metadata.url is authoritative;
        compatibility remix fallback must not win first."""
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

            backend = AgnesVideoBackend(api_key="k", model="agnes-video-v2.0", base_url=_BASE_URL)
            result = await backend.generate(_request(tmp_path))

            # Authoritative top-level/metadata fields skip secondary queries.
            assert routes.query.call_count == 0

        assert result.video_uri == "https://cdn.agnes/authoritative.mp4"

    async def test_video_id_query_url_under_metadata_is_used(self, tmp_path: Path):
        """Output lookups descend into metadata.url when direct top-level URL fields are absent."""
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

            backend = AgnesVideoBackend(api_key="k", model="agnes-video-v2.0", base_url=_BASE_URL)
            result = await backend.generate(_request(tmp_path))

        assert result.video_uri == "https://platform-outputs.agnes-ai.com/videos/meta.mp4"
        assert result.video_path.read_bytes() == b"meta-bytes"
        # Terminal seconds absent: parse secondary-query durations rather than requested fallback.
        assert result.duration_seconds == 8

    async def test_video_id_query_without_url_raises_descriptive_error(self, tmp_path: Path):
        with _agnes_api() as routes:
            routes.submit.mock(return_value=_queued("t-bad-query"))
            routes.poll.mock(
                return_value=_json({"task_id": "t-bad-query", "video_id": "vid-789", "status": "completed"})
            )
            routes.query.mock(return_value=_json({"video_id": "vid-789", "status": "unexpected"}))

            backend = AgnesVideoBackend(api_key="k", model="agnes-video-v2.0", base_url=_BASE_URL)
            with pytest.raises(RuntimeError, match="video_id 查询响应缺少成片 URL"):
                await backend.generate(_request(tmp_path))

            assert routes.download.call_count == 0

    async def test_polling_timeout_raises(self, tmp_path: Path):
        """Absent terminal states raise TimeoutError at max_wait, never poll forever."""
        with _agnes_api() as routes, bounded_poll_clock():
            routes.submit.mock(return_value=_queued("t-timeout"))
            routes.poll.mock(return_value=_json({"task_id": "t-timeout", "status": "in_progress"}))

            backend = AgnesVideoBackend(api_key="k", model="agnes-video-v2.0", base_url=_BASE_URL)
            with pytest.raises(TimeoutError, match="Agnes"):
                await backend.generate(_request(tmp_path))

            assert routes.poll.call_count > 1
            assert routes.download.call_count == 0


class TestSubmitResilience:
    async def test_submit_retries_on_503_busy(self, tmp_path: Path):
        """503 Service busy retries through should_retry_submit status_code gates."""
        busy = httpx.Response(503, text="Service busy")

        with _agnes_api() as routes, bounded_poll_clock():
            routes.submit.mock(side_effect=[busy, busy, _queued("t-retry")])
            routes.poll.mock(return_value=_json(_completed("t-retry")))

            backend = AgnesVideoBackend(api_key="k", model="agnes-video-v2.0", base_url=_BASE_URL)
            result = await backend.generate(_request(tmp_path))

            assert routes.submit.call_count == 3

        assert result.task_id == "t-retry"

    async def test_submit_non_retryable_4xx_fails_fast(self, tmp_path: Path):
        with _agnes_api() as routes, bounded_poll_clock():
            routes.submit.mock(return_value=_json({"error": "bad request"}, status_code=400))

            backend = AgnesVideoBackend(api_key="k", model="agnes-video-v2.0", base_url=_BASE_URL)
            with pytest.raises(httpx.HTTPStatusError):
                await backend.generate(_request(tmp_path))

            assert routes.submit.call_count == 1
            assert routes.poll.call_count == 0  # Submission 4xx never starts polling.

    async def test_submit_read_timeout_wraps_ambiguous(self, tmp_path: Path):
        with _agnes_api() as routes, bounded_poll_clock():
            routes.submit.mock(side_effect=httpx.ReadTimeout("read timed out"))

            backend = AgnesVideoBackend(api_key="k", model="agnes-video-v2.0", base_url=_BASE_URL)
            with pytest.raises(AmbiguousSubmitError):
                await backend.generate(_request(tmp_path))

            assert routes.submit.call_count == 1
            assert routes.poll.call_count == 0  # Ambiguous outcomes never start polling.


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
            backend = AgnesVideoBackend(api_key="k", model="agnes-video-v2.0", base_url=_BASE_URL)

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

            backend = AgnesVideoBackend(api_key="k", model="agnes-video-v2.0", base_url=_BASE_URL)
            result = await backend.resume_video("task-resume", _request(tmp_path, output_path=tmp_path / "out.mp4"))

            assert routes.submit.call_count == 0  # Resume never POSTs create.
            assert only_request(routes.poll).url.path.endswith("/videos/task-resume")

        assert result.task_id == "task-resume"
        assert (tmp_path / "out.mp4").read_bytes() == b"resumed"

    async def test_resume_completed_with_only_video_id_queries_and_downloads(self, tmp_path: Path):
        """Resuming completed video_id-only tasks shares initial terminal parsing/secondary downloads,
        never POSTing creation or duplicating billing.
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

            backend = AgnesVideoBackend(api_key="k", model="agnes-video-v2.0", base_url=_GATEWAY_BASE_URL)
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

            backend = AgnesVideoBackend(api_key="k", model="agnes-video-v2.0", base_url=_BASE_URL)
            with pytest.raises(ResumeExpiredError) as ei:
                await backend.resume_video("task-404", _request(tmp_path, output_path=tmp_path / "out.mp4"))

            assert ei.value.job_id == "task-404"
            assert ei.value.provider == PROVIDER_AGNES
            assert routes.poll.call_count == 1


class TestDurationPassthrough:
    async def test_duration_beyond_frame_limit_is_submitted_unclamped(self, tmp_path: Path):
        """Backends do not independently check duration: convert even upstream-frame-limit overflow using original seconds, leaving upstream judgment."""
        with _agnes_api() as routes:
            routes.submit.mock(return_value=_queued("task-30"))
            routes.poll.mock(return_value=_json(_completed("task-30", seconds="30.0")))
            routes.download.mock(return_value=httpx.Response(200, content=b"mp4"))

            backend = AgnesVideoBackend(api_key="k", model="agnes-video-v2.0", base_url=_BASE_URL)
            await backend.generate(_request(tmp_path, duration_seconds=30))

        assert request_json(only_request(routes.submit))["num_frames"] == 721


class TestProviderJobIdPersistence:
    async def test_persists_agnes_task_id_for_worker_request(self, tmp_path: Path):
        """Worker nonempty request.task_id persists returned Agnes task_id as job_id for resume."""
        with _agnes_api() as routes, captured_provider_job_ids() as persisted:
            routes.submit.mock(return_value=_queued("agnes-task-42"))
            routes.poll.mock(return_value=_json(_completed("agnes-task-42")))

            backend = AgnesVideoBackend(api_key="k", model="agnes-video-v2.0", base_url=_BASE_URL)
            await backend.generate(_request(tmp_path, task_id="worker-task-99"))

        assert persisted == [
            {
                "task_id": "worker-task-99",  # Worker task ID
                "job_id": "agnes-task-42",  # Persist Agnes-submitted task_id as job_id.
                "provider": PROVIDER_AGNES,
                "endpoint": None,
                "base_url": None,
            }
        ]

    async def test_non_worker_request_skips_persistence(self, tmp_path: Path):
        """Nonworker task_id=None never persists, avoiding empty task IDs in DB."""
        with _agnes_api() as routes, captured_provider_job_ids() as persisted:
            routes.submit.mock(return_value=_queued("agnes-task-1"))
            routes.poll.mock(return_value=_json(_completed("agnes-task-1")))

            backend = AgnesVideoBackend(api_key="k", model="agnes-video-v2.0", base_url=_BASE_URL)
            await backend.generate(_request(tmp_path))

        assert persisted == []


class TestRegistration:
    def test_registered_in_video_backend_registry(self):
        from lib.backends.video_backends import create_backend, get_registered_backends

        assert {"agnes-video-2", "agnes-video-2-5"} <= set(get_registered_backends())
        backend = create_backend("agnes-video-2-5", api_key="sk-test", base_url=_BASE_URL)
        assert backend.name == PROVIDER_AGNES


class TestPollErrorRedaction:
    """Polling/lookup 4xx str(exc) enters task.error_message/logs; query credentials appear in
    raise_for_status URLs, requiring sanitization before raising."""

    async def test_poll_4xx_message_drops_query_credentials(self):
        backend = AgnesVideoBackend(api_key="k", model="agnes-video-v2.0", base_url=_BASE_URL)
        with _agnes_api() as routes:
            routes.poll.mock(return_value=_json({"message": "forbidden"}, 403))
            async with httpx.AsyncClient(params={"api_key": "SECRETKEY"}) as client:
                with pytest.raises(httpx.HTTPStatusError) as excinfo:
                    await backend._poll_once(client, "task-1")

        assert "SECRETKEY" not in str(excinfo.value)
        assert excinfo.value.response.status_code == 403

    async def test_video_query_4xx_message_drops_query_credentials(self, tmp_path: Path):
        backend = AgnesVideoBackend(api_key="k", model="agnes-video-v2.0", base_url=_BASE_URL)
        with _agnes_api() as routes:
            routes.query.mock(return_value=_json({"message": "forbidden"}, 403))
            async with httpx.AsyncClient(params={"api_key": "SECRETKEY"}) as client:
                with pytest.raises(httpx.HTTPStatusError) as excinfo:
                    await backend._query_video(client, "vid-1", _request(tmp_path))

        assert "SECRETKEY" not in str(excinfo.value)
        assert excinfo.value.response.status_code == 403
