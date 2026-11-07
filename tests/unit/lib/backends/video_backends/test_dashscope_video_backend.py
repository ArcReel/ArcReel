"""DashScopeVideoBackend two-step asynchronous HTTP-substitute tests."""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from arcreel_market_core.video_backend_contract import (
    ReferenceAudioMode,
    ResumeExpiredError,
    VideoCapabilities,
    VideoCapabilityError,
    VideoGenerationRequest,
)
from lib.backends.http_status_errors import ArtifactDownloadError
from lib.backends.providers import PROVIDER_DASHSCOPE
from lib.backends.video_backends.dashscope import (
    DASHSCOPE_VIDEO_BACKENDS,
    DashScopeAlwaysOnVideoBackend,
    DashScopeReferenceVideoBackend,
    DashScopeReferenceVoiceVideoBackend,
    DashScopeWan3VideoBackend,
)
from lib.custom_provider.endpoints import infer_endpoint
from tests.fakes import bounded_poll_clock, captured_provider_job_ids
from tests.http_capture import capture_http


def _resp(json_body: dict, status_code: int = 200) -> MagicMock:
    resp = MagicMock()
    resp.status_code = status_code
    resp.json.return_value = json_body
    # Match httpx text as original body; >=400 logs/rejection summaries read it, so MagicMock
    # would diverge from real responses along production paths.
    resp.text = json.dumps(json_body, ensure_ascii=False)
    resp.raise_for_status = MagicMock()
    return resp


def _http_error(status_code: int, message: str) -> httpx.HTTPStatusError:
    request = httpx.Request("GET", "https://x/api/v1/tasks/t")
    response = httpx.Response(status_code, request=request, text=message)
    return httpx.HTTPStatusError(f"error {status_code}", request=request, response=response)


def _http_error_503_in_message(status_code: int) -> httpx.HTTPStatusError:
    """Create actual HTTPStatusError with str() 503 substrings but supplied status_code.

    raise_for_status includes request URLs with task_id 503; old string fallbacks misclassified
    them. Status predicates inspect response.status_code only, unaffected by message/URL substrings.
    """
    request = httpx.Request("POST", "https://x/api/v1/tasks/job-503-xyz")
    response = httpx.Response(status_code, request=request)
    try:
        response.raise_for_status()
    except httpx.HTTPStatusError as exc:
        return exc
    raise AssertionError("expected HTTPStatusError")  # pragma: no cover


def _submit(task_id: str = "t-1") -> dict:
    return {"output": {"task_id": task_id, "task_status": "PENDING"}}


def _succeeded(url: str = "https://x/o.mp4", duration: int = 5) -> dict:
    return {
        "output": {"task_status": "SUCCEEDED", "video_url": url},
        "usage": {"duration": duration, "input_video_duration": 0, "output_video_duration": duration},
    }


class _RecordingClient:
    """httpx.AsyncClient substitute records post/get URLs/arguments with injected responses.

    Assert actual endpoint/body/auth content under ``posts`` / ``gets``, never substitute callables.
    """

    def __init__(self, *, post: AsyncMock | None = None, get: AsyncMock | None = None) -> None:
        self.posts: list[dict[str, Any]] = []
        self.gets: list[dict[str, Any]] = []
        self._post = post or AsyncMock()
        self._get = get or AsyncMock()

    async def post(self, url: str, **kwargs: Any):
        self.posts.append({"url": url, **kwargs})
        return await self._post(url, **kwargs)

    async def get(self, url: str, **kwargs: Any):
        self.gets.append({"url": url, **kwargs})
        return await self._get(url, **kwargs)

    async def __aenter__(self) -> _RecordingClient:
        return self

    async def __aexit__(self, *_exc: object) -> None:
        return None


def _client(*, post=None, get=None) -> _RecordingClient:
    return _RecordingClient(post=post, get=get)


def _patches(client: _RecordingClient, download: AsyncMock):
    return (
        patch("httpx.AsyncClient", return_value=client),
        patch("lib.backends.video_backends.dashscope.download_resumable_video", download),
        bounded_poll_clock(),
    )


def _ref(tmp_path: Path, name: str) -> Path:
    p = tmp_path / name
    p.write_bytes(b"\x89PNG\r\nfake")
    return p


def _effective(model: str) -> VideoCapabilities:
    """Effective built-in DashScope capabilities jointly evaluate channels/entries, then execution gates attach them to requests (docs/adr/0104)."""
    from lib.backends.backend_assembly.specs import builtin_video_capabilities_for_model

    return builtin_video_capabilities_for_model("dashscope", model)


def _profile_key(model: str) -> str | None:
    from lib.backends.video_backends.dashscope import _profile_key_for_model

    return _profile_key_for_model(model)


class TestCapabilities:
    def test_name_and_model(self):

        b = DashScopeAlwaysOnVideoBackend(api_key="sk", model="happyhorse-1.0-i2v")
        assert b.name == PROVIDER_DASHSCOPE
        assert b.model == "happyhorse-1.0-i2v"

    def test_channel_declares_every_input_path_without_a_reference_cap(self):
        """Channel declarations ignore names: implement first/last frames, text, references/audio, without reference counts caps."""

        for model in ("happyhorse-1.0-r2v", "wan2.7-i2v", "proxy-custom-video"):
            caps = DashScopeReferenceVoiceVideoBackend.video_capabilities_for_model(model)
            assert caps.text_to_video is True
            assert caps.first_frame is True
            assert caps.last_frame is True
            assert caps.max_reference_images is None
            assert caps.reference_audio_mode is ReferenceAudioMode.DIRECT
            assert caps.max_reference_audio_count is None

    def test_happyhorse_r2v_caps(self):
        vc = _effective("happyhorse-1.0-r2v")
        assert vc.max_reference_images == 9
        assert vc.first_frame is False

    def test_happyhorse_11_caps(self):
        """1.1 modalities follow entries: t2v lacks first frames, i2v has first frames/no references, r2v has 9 references/no first frame."""
        t2v = _effective("happyhorse-1.1-t2v")
        assert t2v.first_frame is False
        assert t2v.max_reference_images == 0

        i2v = _effective("happyhorse-1.1-i2v")
        assert i2v.first_frame is True
        assert i2v.max_reference_images == 0

        r2v = _effective("happyhorse-1.1-r2v")
        assert r2v.first_frame is False
        assert r2v.max_reference_images == 9

    def test_default_model_is_happyhorse_11_i2v(self):
        from lib.backends.video_backends.dashscope import DEFAULT_MODEL, DashScopeVideoBackend

        assert DEFAULT_MODEL == "happyhorse-1.1-i2v"
        assert DashScopeVideoBackend(api_key="sk").model == "happyhorse-1.1-i2v"

    def test_wan_r2v_caps(self):
        vc = _effective("wan2.7-r2v")
        assert vc.max_reference_images == 5
        assert vc.first_frame is True

    def test_t2v_caps(self):
        vc = _effective("happyhorse-1.0-t2v")
        assert vc.first_frame is False
        assert vc.max_reference_images == 0

    def test_i2v_caps(self):
        assert _effective("wan2.7-i2v").first_frame is True

    def test_decorated_model_name_resolves_r2v_request_shape(self):
        """Delimited proxy prefixes/suffixes routed by infer_endpoint to async DashScope must resolve
        r2v request profiles requiring references, never generic channel fallback."""
        from lib.backends.backend_assembly.specs import dashscope_video_endpoint_for_name

        for model, _expected_key in (
            ("proxy/happyhorse-1.0-r2v", "happyhorse-1.0-r2v"),
            ("provider:wan2.7-r2v", "wan2.7-r2v"),
            ("wan2.7-r2v-0715", "wan2.7-r2v"),  # Version suffix
            ("Pro/HappyHorse-1.0-R2V", "happyhorse-1.0-r2v"),  # Case insensitive
        ):
            assert dashscope_video_endpoint_for_name(model) in {
                "dashscope-reference-video",
                "dashscope-reference-voice-video",
            }

    def test_alnum_glued_prefix_does_not_resolve_r2v_caps(self):
        """Alphanumeric-attached prefixes without separators are not decorated names and have no known profile.

        Opposite boundary case: stripping my from myhappyhorse-1.0-r2v yields happyhorse-1.0-r2v
        exactly. Substring matching needs identifier boundaries; decorations use nonalphanumeric
        / or : separators, which directly attached my lacks.
        """

        assert _profile_key("myhappyhorse-1.0-r2v") is None

    @pytest.mark.parametrize("model", ["wan2.7-i2vfoo", "happyhorse-1.0-r2vfoo"])
    def test_alnum_glued_suffix_does_not_resolve_known_profile(self, model):
        """Alphanumeric-attached suffixes likewise have no recognized decorated profile.

        Third complementary boundary: wan2.7-i2vfoo truncates foo to known wan2.7-i2v. Checking
        left boundaries only would misclassify unknown suffixes as known modalities. Date suffixes
        such as -0715 use separating hyphens and retain valid right boundaries.
        """
        from lib.backends.video_backends.dashscope import DashScopeVideoBackend

        assert _profile_key(model) is None
        assert DashScopeVideoBackend.video_capabilities_for_model(model).max_prompt_chars is None

    def test_unknown_bare_series_falls_back_to_default(self):
        """Bare happyhorse without modality suffix cannot resolve t2v/i2v/r2v, so use channel declarations."""
        from lib.backends.video_backends.dashscope import _CHANNEL_CAPABILITIES, DashScopeVideoBackend

        assert _profile_key("happyhorse") is None
        assert DashScopeVideoBackend(api_key="sk", model="happyhorse").video_capabilities == replace(
            _CHANNEL_CAPABILITIES, audio_track=DashScopeVideoBackend.endpoint.audio_track
        )

    def test_wan27_declares_prompt_char_limit(self):
        """wan2.7 prompts cap 5000 characters; upstream silently truncates while billing, requiring prepayment gates."""
        from lib.backends.video_backends.dashscope import DashScopeVideoBackend

        for model in ("wan2.7-t2v", "wan2.7-i2v", "wan2.7-r2v"):
            assert DashScopeVideoBackend.video_capabilities_for_model(model).max_prompt_chars == 5000

    def test_models_without_verified_limit_declare_none(self):
        """Unverified model limits remain undeclared, not zero; invented declarations would falsely reject valid inputs."""
        from lib.backends.video_backends.dashscope import DashScopeVideoBackend

        for model in ("happyhorse-1.0-t2v", "happyhorse-1.0-i2v", "happyhorse-1.0-r2v", "happyhorse"):
            assert DashScopeVideoBackend.video_capabilities_for_model(model).max_prompt_chars is None


class TestReferenceToVideo:
    async def test_r2v_happy_path(self, tmp_path: Path):
        post = AsyncMock(return_value=_resp(_submit("t-r2v")))
        get = AsyncMock(return_value=_resp(_succeeded(duration=8)))
        client = _client(post=post, get=get)
        download = AsyncMock()
        ref1, ref2 = _ref(tmp_path, "a.png"), _ref(tmp_path, "b.png")
        p1, p2, p3 = _patches(client, download)
        with p1, p2, p3:
            b = DashScopeReferenceVideoBackend(api_key="sk", model="happyhorse-1.0-r2v")
            result = await b.generate(
                VideoGenerationRequest(
                    prompt="[Image 1] dances",
                    output_path=tmp_path / "o.mp4",
                    reference_images=[ref1, ref2],
                    resolution="720p",
                    aspect_ratio="16:9",
                    duration_seconds=5,
                )
            )

        body = client.posts[-1]["json"]
        assert body["model"] == "happyhorse-1.0-r2v"
        media = body["input"]["media"]
        assert len(media) == 2
        assert all(m["type"] == "reference_image" for m in media)
        assert media[0]["url"].startswith("data:image/png;base64,")
        # Uppercase resolution, disable watermarks, forward ratio.
        assert body["parameters"]["resolution"] == "720P"
        assert body["parameters"]["watermark"] is False
        assert body["parameters"]["ratio"] == "16:9"
        # Submission endpoint plus async headers
        assert client.posts[-1]["url"].endswith("/api/v1/services/aigc/video-generation/video-synthesis")
        assert client.posts[-1]["headers"]["X-DashScope-Async"] == "enable"
        # Bill usage.duration, not requested 5.
        assert result.duration_seconds == 8
        assert result.provider == PROVIDER_DASHSCOPE
        assert result.task_id == "t-r2v"
        download.assert_called_once()

    async def test_r2v_ref_limit_happyhorse_9(self, tmp_path: Path):
        post = AsyncMock(return_value=_resp(_submit()))
        get = AsyncMock(return_value=_resp(_succeeded()))
        client = _client(post=post, get=get)
        refs = [_ref(tmp_path, f"r{i}.png") for i in range(9)]
        p1, p2, p3 = _patches(client, AsyncMock())
        with p1, p2, p3:
            b = DashScopeReferenceVideoBackend(api_key="sk", model="happyhorse-1.0-r2v")
            await b.generate(
                VideoGenerationRequest(
                    prompt="p", output_path=tmp_path / "o.mp4", reference_images=refs, resolution="720p"
                )
            )
        assert len(client.posts[-1]["json"]["input"]["media"]) == 9

    async def test_r2v_refs_over_limit_rejected_before_submit(self, tmp_path: Path):
        """References exceeding model caps reject before submission, never truncate."""
        post = AsyncMock(return_value=_resp(_submit()))
        client = _client(post=post, get=AsyncMock())
        refs = [_ref(tmp_path, f"r{i}.png") for i in range(6)]
        p1, p2, p3 = _patches(client, AsyncMock())
        with p1, p2, p3:
            b = DashScopeReferenceVoiceVideoBackend(api_key="sk", model="wan2.7-r2v")
            with pytest.raises(VideoCapabilityError) as ei:
                await b.generate(
                    VideoGenerationRequest(
                        prompt="p",
                        output_path=tmp_path / "o.mp4",
                        reference_images=refs,
                        resolution="1080p",
                        video_capabilities=_effective("wan2.7-r2v"),
                    )
                )
        assert ei.value.code == "video_reference_images_exceeded"
        assert ei.value.params == {"model": "wan2.7-r2v", "limit": 5, "count": 6}
        post.assert_not_awaited()

    def test_refs_on_model_without_reference_capability_rejected(self, tmp_path: Path):

        with pytest.raises(VideoCapabilityError) as ei:
            DashScopeAlwaysOnVideoBackend(api_key="sk", model="wan2.7-i2v")._build_payload(
                VideoGenerationRequest(
                    prompt="p",
                    output_path=tmp_path / "o.mp4",
                    reference_images=[_ref(tmp_path, "r.png")],
                    video_capabilities=_effective("wan2.7-i2v"),
                )
            )
        assert ei.value.code == "video_reference_images_unsupported"

    async def test_start_image_rejected_when_model_has_no_first_frame(self, tmp_path: Path):
        # first_frame=False from entries rejects supplied frames; silently dropping them would bill ignored frames.
        post = AsyncMock(return_value=_resp(_submit()))
        client = _client(post=post, get=AsyncMock())
        p1, p2, p3 = _patches(client, AsyncMock())
        start = tmp_path / "start.png"
        start.write_bytes(b"\x89PNG")
        with p1, p2, p3:
            b = DashScopeAlwaysOnVideoBackend(api_key="sk", model="wan2.7-t2v")
            with pytest.raises(VideoCapabilityError) as ei:
                await b.generate(
                    VideoGenerationRequest(
                        prompt="p",
                        output_path=tmp_path / "o.mp4",
                        start_image=start,
                        video_capabilities=_effective("wan2.7-t2v"),
                    )
                )
        assert ei.value.code == "video_first_frame_unsupported"
        post.assert_not_awaited()

    async def test_r2v_all_refs_missing_fail_loud(self, tmp_path: Path):
        # Missing/unreadable r2v references, including declarations after empty filtering, fail loudly and name files, never degrade.
        post = AsyncMock(return_value=_resp(_submit()))
        client = _client(post=post, get=AsyncMock())
        p1, p2, p3 = _patches(client, AsyncMock())
        with p1, p2, p3:
            b = DashScopeReferenceVoiceVideoBackend(api_key="sk", model="wan2.7-r2v")
            with pytest.raises(VideoCapabilityError) as ei:
                await b.generate(
                    VideoGenerationRequest(
                        prompt="p",
                        output_path=tmp_path / "o.mp4",
                        reference_images=[str(tmp_path / "nope.png"), ""],
                        resolution="720p",
                    )
                )
        assert ei.value.code == "video_reference_images_unreadable"
        assert "nope.png" in ei.value.params["names"]
        # No submission requests should occur.
        post.assert_not_called()

    async def test_r2v_no_refs_provided_raises(self, tmp_path: Path):
        # r2v without any references, None/empty, raises required errors rather than sends medialess requests.
        post = AsyncMock(return_value=_resp(_submit()))
        client = _client(post=post, get=AsyncMock())
        p1, p2, p3 = _patches(client, AsyncMock())
        with p1, p2, p3:
            b = DashScopeReferenceVideoBackend(api_key="sk", model="happyhorse-1.0-r2v")
            with pytest.raises(VideoCapabilityError) as ei:
                await b.generate(VideoGenerationRequest(prompt="p", output_path=tmp_path / "o.mp4", resolution="720p"))
        assert ei.value.code == "video_reference_images_required"
        post.assert_not_called()

    async def test_r2v_partial_unreadable_refs_fail_loud(self, tmp_path: Path):
        # Partially unreadable references after is_file success fail loudly and name files,
        # never generate from readable subsets with wrong output and normal charges.
        post = AsyncMock(return_value=_resp(_submit("t-r2v")))
        get = AsyncMock(return_value=_resp(_succeeded()))
        client = _client(post=post, get=get)
        ra, rb = _ref(tmp_path, "a.png"), _ref(tmp_path, "b.png")

        def fake_uri(p: Path) -> str:
            if p.name == "a.png":
                raise OSError("io error")
            return "data:image/png;base64,OK"

        p1, p2, p3 = _patches(client, AsyncMock())
        with p1, p2, p3, patch("lib.backends.video_backends.dashscope.image_to_data_uri", side_effect=fake_uri):
            b = DashScopeReferenceVoiceVideoBackend(api_key="sk", model="wan2.7-r2v")
            with pytest.raises(VideoCapabilityError) as ei:
                await b.generate(
                    VideoGenerationRequest(
                        prompt="p",
                        output_path=tmp_path / "o.mp4",
                        reference_images=[str(ra), str(rb)],
                        resolution="720p",
                    )
                )
        assert ei.value.code == "video_reference_images_unreadable"
        assert "a.png" in ei.value.params["names"]
        post.assert_not_called()

    async def test_r2v_all_refs_unreadable_oserror_fail_loud(self, tmp_path: Path):
        # All unreadable references fail loudly, never send medialess r2v.
        post = AsyncMock(return_value=_resp(_submit()))
        client = _client(post=post, get=AsyncMock())
        ra = _ref(tmp_path, "a.png")
        p1, p2, p3 = _patches(client, AsyncMock())
        with (
            p1,
            p2,
            p3,
            patch("lib.backends.video_backends.dashscope.image_to_data_uri", side_effect=OSError("denied")),
        ):
            b = DashScopeReferenceVoiceVideoBackend(api_key="sk", model="wan2.7-r2v")
            with pytest.raises(VideoCapabilityError) as ei:
                await b.generate(
                    VideoGenerationRequest(
                        prompt="p", output_path=tmp_path / "o.mp4", reference_images=[str(ra)], resolution="720p"
                    )
                )
        assert ei.value.code == "video_reference_images_unreadable"
        post.assert_not_called()


class TestFirstFrameAndTextOnly:
    async def test_i2v_start_image_oserror_fail_loud(self, tmp_path: Path):
        # Declared first frames with read OSError, permissions/I/O, fail loudly, never silently ignore and generate.
        post = AsyncMock(return_value=_resp(_submit()))
        get = AsyncMock(return_value=_resp(_succeeded()))
        client = _client(post=post, get=get)
        start = _ref(tmp_path, "start.png")
        p1, p2, p3 = _patches(client, AsyncMock())
        with p1, p2, p3, patch("lib.backends.video_backends.dashscope.image_to_data_uri", side_effect=OSError("io")):
            b = DashScopeAlwaysOnVideoBackend(api_key="sk", model="wan2.7-i2v")
            with pytest.raises(VideoCapabilityError) as ei:
                await b.generate(
                    VideoGenerationRequest(
                        prompt="p", output_path=tmp_path / "o.mp4", start_image=start, resolution="720p"
                    )
                )
        assert ei.value.code == "video_start_image_unreadable"
        assert "start.png" in ei.value.params["name"]
        post.assert_not_called()

    async def test_i2v_first_frame(self, tmp_path: Path):
        post = AsyncMock(return_value=_resp(_submit()))
        get = AsyncMock(return_value=_resp(_succeeded()))
        client = _client(post=post, get=get)
        start = _ref(tmp_path, "start.png")
        p1, p2, p3 = _patches(client, AsyncMock())
        with p1, p2, p3:
            b = DashScopeAlwaysOnVideoBackend(api_key="sk", model="wan2.7-i2v")
            await b.generate(
                VideoGenerationRequest(prompt="p", output_path=tmp_path / "o.mp4", start_image=start, resolution="720p")
            )
        media = client.posts[-1]["json"]["input"]["media"]
        assert media == [{"type": "first_frame", "url": media[0]["url"]}]
        assert media[0]["url"].startswith("data:image/png;base64,")
        # First frames determine i2v ratios; never send ratio even when default aspect_ratio is nonempty, or upstream rejects.
        assert "ratio" not in client.posts[-1]["json"]["parameters"]

    async def test_t2v_no_media(self, tmp_path: Path):
        post = AsyncMock(return_value=_resp(_submit()))
        get = AsyncMock(return_value=_resp(_succeeded()))
        client = _client(post=post, get=get)
        p1, p2, p3 = _patches(client, AsyncMock())
        with p1, p2, p3:
            b = DashScopeAlwaysOnVideoBackend(api_key="sk", model="happyhorse-1.0-t2v")
            await b.generate(VideoGenerationRequest(prompt="p", output_path=tmp_path / "o.mp4", resolution="1080p"))
        assert "media" not in client.posts[-1]["json"]["input"]
        assert client.posts[-1]["json"]["parameters"]["resolution"] == "1080P"

    async def test_happyhorse_11_i2v_payload(self, tmp_path: Path):
        """1.1 matches 1.0: explicitly disable default-on watermarks and forward uppercase 480P tiers."""
        post = AsyncMock(return_value=_resp(_submit()))
        get = AsyncMock(return_value=_resp(_succeeded()))
        client = _client(post=post, get=get)
        start = _ref(tmp_path, "start.png")
        p1, p2, p3 = _patches(client, AsyncMock())
        with p1, p2, p3:
            b = DashScopeAlwaysOnVideoBackend(api_key="sk", model="happyhorse-1.1-i2v")
            await b.generate(
                VideoGenerationRequest(prompt="p", output_path=tmp_path / "o.mp4", start_image=start, resolution="480p")
            )
        params = client.posts[-1]["json"]["parameters"]
        assert client.posts[-1]["json"]["model"] == "happyhorse-1.1-i2v"
        assert params["watermark"] is False
        assert params["resolution"] == "480P"
        assert client.posts[-1]["json"]["input"]["media"][0]["type"] == "first_frame"


class TestUserRelaxedCapabilities:
    """Requests carrying execution-gate user overrides send widened capabilities normally, without model-profile rejection."""

    @staticmethod
    def _build(tmp_path: Path, model: str, **request_fields) -> dict:

        return DASHSCOPE_VIDEO_BACKENDS[infer_endpoint(model, "openai")](api_key="sk", model=model)._build_payload(
            VideoGenerationRequest(prompt="p", output_path=tmp_path / "o.mp4", **request_fields)
        )

    @staticmethod
    def _caps(model: str, **changes) -> VideoCapabilities:
        from lib.backends.video_backends.dashscope import DashScopeVideoBackend

        return replace(DashScopeVideoBackend.video_capabilities_for_model(model), **changes)

    def test_relaxed_last_frame_sent(self, tmp_path: Path):
        payload = self._build(
            tmp_path,
            "wan2.7-i2v",
            start_image=_ref(tmp_path, "s.png"),
            end_image=_ref(tmp_path, "e.png"),
            video_capabilities=self._caps("wan2.7-i2v", last_frame=True),
        )
        assert [m["type"] for m in payload["input"]["media"]] == ["first_frame", "last_frame"]

    def test_relaxed_first_frame_sent(self, tmp_path: Path):
        payload = self._build(
            tmp_path,
            "wan2.7-t2v",
            start_image=_ref(tmp_path, "s.png"),
            video_capabilities=self._caps("wan2.7-t2v", first_frame=True),
        )
        assert [m["type"] for m in payload["input"]["media"]] == ["first_frame"]

    def test_relaxed_reference_limit_sends_all_references(self, tmp_path: Path):
        refs = [_ref(tmp_path, f"r{i}.png") for i in range(6)]
        payload = self._build(
            tmp_path,
            "wan2.7-r2v",
            reference_images=refs,
            video_capabilities=self._caps("wan2.7-r2v", max_reference_images=6),
        )
        assert [m["type"] for m in payload["input"]["media"]] == ["reference_image"] * 6

    def test_relaxed_references_on_i2v_model_sent_and_not_required(self, tmp_path: Path):
        caps = self._caps("wan2.7-i2v", max_reference_images=2)
        with_refs = self._build(
            tmp_path, "wan2.7-i2v", reference_images=[_ref(tmp_path, "r.png")], video_capabilities=caps
        )
        assert [m["type"] for m in with_refs["input"]["media"]] == ["reference_image"]
        # Wider reference limits do not make i2v require references; first-frame-only calls remain valid.
        frame_only = self._build(tmp_path, "wan2.7-i2v", start_image=_ref(tmp_path, "s.png"), video_capabilities=caps)
        assert [m["type"] for m in frame_only["input"]["media"]] == ["first_frame"]

    def test_narrowed_reference_limit_still_requires_reference_on_r2v_model(self, tmp_path: Path):
        with pytest.raises(VideoCapabilityError) as ei:
            self._build(
                tmp_path,
                "happyhorse-1.0-r2v",
                video_capabilities=self._caps("happyhorse-1.0-r2v", max_reference_images=0),
            )
        assert ei.value.code == "video_reference_images_required"

    def test_metered_reference_count_follows_relaxed_limit(self, tmp_path: Path):
        from lib.backends.video_backends.dashscope import DashScopeVideoBackend

        refs = [_ref(tmp_path, f"r{i}.png") for i in range(6)]
        request = VideoGenerationRequest(
            prompt="p",
            output_path=tmp_path / "o.mp4",
            reference_images=refs,
            video_capabilities=self._caps("wan2.7-r2v", max_reference_images=6),
        )
        assert DashScopeVideoBackend.metered_usage("wan2.7-r2v", request).quantities["input_images"] == 6


class TestPollingAndFailures:
    async def test_polls_through_running(self, tmp_path: Path):
        post = AsyncMock(return_value=_resp(_submit("t3")))
        get = AsyncMock(
            side_effect=[
                _resp({"output": {"task_status": "RUNNING"}}),
                _resp({"output": {"task_status": "RUNNING"}}),
                _resp(_succeeded()),
            ]
        )
        client = _client(post=post, get=get)
        p1, p2, p3 = _patches(client, AsyncMock())
        with p1, p2, p3:
            b = DashScopeAlwaysOnVideoBackend(api_key="sk", model="happyhorse-1.0-i2v")
            result = await b.generate(
                VideoGenerationRequest(prompt="p", output_path=tmp_path / "o.mp4", resolution="720p")
            )
        assert len(client.gets) == 3
        assert result.task_id == "t3"

    async def test_failed_raises(self, tmp_path: Path):
        post = AsyncMock(return_value=_resp(_submit()))
        get = AsyncMock(return_value=_resp({"output": {"task_status": "FAILED", "code": "X", "message": "boom"}}))
        client = _client(post=post, get=get)
        download = AsyncMock()
        p1, p2, p3 = _patches(client, download)
        with p1, p2, p3:
            b = DashScopeAlwaysOnVideoBackend(api_key="sk", model="wan2.7-t2v")
            with pytest.raises(RuntimeError, match="boom"):
                await b.generate(VideoGenerationRequest(prompt="p", output_path=tmp_path / "o.mp4", resolution="720p"))
        download.assert_not_called()

    async def test_generate_unknown_raises_runtime(self, tmp_path: Path):
        post = AsyncMock(return_value=_resp(_submit("t-new")))
        get = AsyncMock(return_value=_resp({"output": {"task_status": "UNKNOWN"}}))
        client = _client(post=post, get=get)
        p1, p2, p3 = _patches(client, AsyncMock())
        with p1, p2, p3:
            b = DashScopeAlwaysOnVideoBackend(api_key="sk", model="wan2.7-t2v")
            with pytest.raises(RuntimeError) as ei:
                await b.generate(VideoGenerationRequest(prompt="p", output_path=tmp_path / "o.mp4", resolution="720p"))
            assert "expired" in str(ei.value).lower()
            assert not isinstance(ei.value, ResumeExpiredError)


class TestResume:
    async def test_download_failure_can_resume_without_resubmit(self, tmp_path: Path):
        download_ready = False

        def download_response(_request: httpx.Request) -> httpx.Response:
            if download_ready:
                return httpx.Response(200, content=b"resumed")
            return httpx.Response(503, text="cdn unavailable")

        with capture_http() as router, bounded_poll_clock(), captured_provider_job_ids() as persisted:
            submit = router.post(
                "https://dashscope.aliyuncs.com/api/v1/services/aigc/video-generation/video-synthesis"
            ).mock(return_value=httpx.Response(200, json=_submit("task-download")))
            router.get("https://dashscope.aliyuncs.com/api/v1/tasks/task-download").mock(
                return_value=httpx.Response(200, json=_succeeded(url="https://x/r.mp4"))
            )
            router.get("https://x/r.mp4").mock(side_effect=download_response)
            request = VideoGenerationRequest(
                prompt="p",
                output_path=tmp_path / "o.mp4",
                resolution="720p",
                task_id="worker-task",
                poll_timeout_seconds=1800,
            )

            backend = DashScopeAlwaysOnVideoBackend(api_key="sk", model="happyhorse-1.0-t2v")
            with pytest.raises(ArtifactDownloadError) as caught:
                await backend.generate(request)

            assert caught.value.code == "artifact_download_failed"
            download_ready = True
            result = await backend.resume_video(persisted[0]["job_id"], request)

            assert submit.call_count == 1

        assert result.video_path.read_bytes() == b"resumed"

    async def test_resume_polls_without_post(self, tmp_path: Path):
        post = AsyncMock(side_effect=AssertionError("resume 不应 POST"))
        get = AsyncMock(return_value=_resp(_succeeded(url="https://x/r.mp4")))
        client = _client(post=post, get=get)
        download = AsyncMock()
        p1, p2, p3 = _patches(client, download)
        with p1, p2, p3:
            b = DashScopeAlwaysOnVideoBackend(api_key="sk", model="happyhorse-1.0-i2v")
            result = await b.resume_video(
                "t-resume",
                VideoGenerationRequest(prompt="p", output_path=tmp_path / "o.mp4", resolution="720p"),
            )
        post.assert_not_called()
        assert client.gets[-1]["url"].endswith("/tasks/t-resume")
        assert result.task_id == "t-resume"

    async def test_resume_unknown_raises_resume_expired(self, tmp_path: Path):
        get = AsyncMock(return_value=_resp({"output": {"task_status": "UNKNOWN"}}))
        client = _client(get=get)
        p1, p2, p3 = _patches(client, AsyncMock())
        with p1, p2, p3:
            b = DashScopeReferenceVoiceVideoBackend(api_key="sk", model="wan2.7-r2v")
            with pytest.raises(ResumeExpiredError) as ei:
                await b.resume_video(
                    "t-exp",
                    VideoGenerationRequest(prompt="p", output_path=tmp_path / "o.mp4", resolution="720p"),
                )
            assert ei.value.job_id == "t-exp"
            assert ei.value.provider == PROVIDER_DASHSCOPE

    async def test_resume_404_raises_without_retry(self, tmp_path: Path):
        not_found = _resp({"error": "nope"}, status_code=404)
        not_found.raise_for_status = MagicMock(side_effect=_http_error(404, "not found"))
        get = AsyncMock(return_value=not_found)
        client = _client(get=get)
        p1, p2, p3 = _patches(client, AsyncMock())
        with p1, p2, p3:
            b = DashScopeReferenceVoiceVideoBackend(api_key="sk", model="wan2.7-r2v")
            with pytest.raises(ResumeExpiredError):
                await b.resume_video(
                    "t-404",
                    VideoGenerationRequest(prompt="p", output_path=tmp_path / "o.mp4", resolution="720p"),
                )
            assert len(client.gets) == 1


class TestPersist:
    async def test_persist_called_with_task_id(self, tmp_path: Path):
        post = AsyncMock(return_value=_resp(_submit("job-9")))
        get = AsyncMock(return_value=_resp(_succeeded()))
        client = _client(post=post, get=get)
        p1, p2, p3 = _patches(client, AsyncMock())
        with p1, p2, p3, captured_provider_job_ids() as persisted:
            b = DashScopeAlwaysOnVideoBackend(api_key="sk", model="happyhorse-1.0-i2v")
            await b.generate(
                VideoGenerationRequest(
                    prompt="p", output_path=tmp_path / "o.mp4", resolution="720p", task_id="db-task-1"
                )
            )
        assert [(r["task_id"], r["job_id"], r["provider"]) for r in persisted] == [
            ("db-task-1", "job-9", PROVIDER_DASHSCOPE)
        ]


class TestSubmit413:
    async def test_submit_413_surfaces_httpstatuserror_no_retry(self, tmp_path: Path):
        err413 = _resp({"code": "PayloadTooLarge"}, status_code=413)
        err413.raise_for_status = MagicMock(side_effect=_http_error(413, "Request Entity Too Large"))
        post = AsyncMock(return_value=err413)
        client = _client(post=post)
        download = AsyncMock()
        ref1 = _ref(tmp_path, "a.png")
        p1, p2, p3 = _patches(client, download)
        with p1, p2, p3:
            b = DashScopeReferenceVideoBackend(api_key="sk", model="happyhorse-1.0-r2v")
            with pytest.raises(httpx.HTTPStatusError) as ei:
                await b.generate(
                    VideoGenerationRequest(
                        prompt="[Image 1] x",
                        output_path=tmp_path / "o.mp4",
                        reference_images=[ref1],
                        resolution="720p",
                        aspect_ratio="16:9",
                        duration_seconds=5,
                    )
                )
        # Preserve status_code for throat 413 handling; nonretryable 413 submits once.
        assert ei.value.response.status_code == 413
        assert len(client.posts) == 1
        download.assert_not_called()


class TestRetryStatusGating:
    """Submission/poll retries depend on HTTP status_code, never mistaken message substrings."""

    async def test_submit_4xx_with_503_substring_no_retry(self, tmp_path: Path):
        # 4xx messages containing URL/task_id 503 formerly retried; status predicates fail fast at 400.
        err = _http_error_503_in_message(400)
        assert "503" in str(err)
        bad = _resp({"code": "InvalidParameter"}, status_code=400)
        bad.raise_for_status = MagicMock(side_effect=err)
        post = AsyncMock(return_value=bad)
        client = _client(post=post)
        p1, p2, p3 = _patches(client, AsyncMock())
        with p1, p2, p3, bounded_poll_clock():
            b = DashScopeAlwaysOnVideoBackend(api_key="sk", model="wan2.7-t2v")
            with pytest.raises(httpx.HTTPStatusError) as ei:
                await b.generate(VideoGenerationRequest(prompt="p", output_path=tmp_path / "o.mp4", resolution="720p"))
        assert ei.value.response.status_code == 400
        assert len(client.posts) == 1

    async def test_submit_real_503_retries_then_succeeds(self, tmp_path: Path):
        # Real 5xx retries by status, succeeding third time.
        err503 = _resp({"code": "ServiceUnavailable"}, status_code=503)
        err503.raise_for_status = MagicMock(side_effect=_http_error_503_in_message(503))
        post = AsyncMock(side_effect=[err503, err503, _resp(_submit("t-ok"))])
        get = AsyncMock(return_value=_resp(_succeeded()))
        client = _client(post=post, get=get)
        p1, p2, p3 = _patches(client, AsyncMock())
        with p1, p2, p3, bounded_poll_clock():
            b = DashScopeAlwaysOnVideoBackend(api_key="sk", model="happyhorse-1.0-t2v")
            result = await b.generate(
                VideoGenerationRequest(prompt="p", output_path=tmp_path / "o.mp4", resolution="720p")
            )
        assert len(client.posts) == 3
        assert result.task_id == "t-ok"

    async def test_submit_connect_error_retries(self, tmp_path: Path):
        # Proven-undelivered network connection failures remain retryable.
        post = AsyncMock(side_effect=[httpx.ConnectError("refused"), _resp(_submit("t-ok"))])
        get = AsyncMock(return_value=_resp(_succeeded()))
        client = _client(post=post, get=get)
        p1, p2, p3 = _patches(client, AsyncMock())
        with p1, p2, p3, bounded_poll_clock():
            b = DashScopeAlwaysOnVideoBackend(api_key="sk", model="happyhorse-1.0-t2v")
            result = await b.generate(
                VideoGenerationRequest(prompt="p", output_path=tmp_path / "o.mp4", resolution="720p")
            )
        assert len(client.posts) == 2
        assert result.task_id == "t-ok"

    async def test_poll_timeout_retries(self, tmp_path: Path):
        # Idempotent polling GET timeouts remain retryable.
        post = AsyncMock(return_value=_resp(_submit("t-poll")))
        get = AsyncMock(side_effect=[httpx.TimeoutException("read timed out"), _resp(_succeeded())])
        client = _client(post=post, get=get)
        p1, p2, p3 = _patches(client, AsyncMock())
        with p1, p2, p3:
            b = DashScopeAlwaysOnVideoBackend(api_key="sk", model="happyhorse-1.0-i2v")
            result = await b.generate(
                VideoGenerationRequest(prompt="p", output_path=tmp_path / "o.mp4", resolution="720p")
            )
        assert len(client.gets) == 2
        assert result.task_id == "t-poll"


class TestWan27ReferenceVoice:
    """wan2.7-r2v voices attach through reference_voice fields on reference assets, not independent media items."""

    @staticmethod
    def _backend():

        return DashScopeReferenceVoiceVideoBackend(api_key="sk", model="wan2.7-r2v")

    @staticmethod
    def _refs(tmp_path, count: int) -> list:
        out = []
        for i in range(count):
            p = tmp_path / f"r{i}.png"
            p.write_bytes(b"\x89PNG\r\n")
            out.append(p)
        return out

    @staticmethod
    def _audio(tmp_path, name: str) -> Path:
        p = tmp_path / name
        p.write_bytes(b"riff-audio")
        return p

    def test_r2v_declares_audio_capability(self):
        caps = _effective("wan2.7-r2v")
        assert caps.reference_audio_mode is ReferenceAudioMode.DIRECT
        # Attach audio to reference items; segment caps equal catalog total-reference caps.
        assert caps.max_reference_audio_count == 5
        assert caps.reference_audio_per_image is True

    def test_i2v_declares_no_audio_capability(self):
        assert _effective("wan2.7-i2v").reference_audio_mode is ReferenceAudioMode.NONE

    def test_audio_attached_to_reference_items_in_order(self, tmp_path):
        payload = self._backend()._build_payload(
            VideoGenerationRequest(
                prompt="两人对话",
                output_path=tmp_path / "o.mp4",
                reference_images=self._refs(tmp_path, 2),
                reference_audio_files=[self._audio(tmp_path, "a.mp3"), self._audio(tmp_path, "b.wav")],
            )
        )

        refs = [m for m in payload["input"]["media"] if m["type"] == "reference_image"]
        assert len(refs) == 2
        assert refs[0]["reference_voice"].startswith("data:audio/mpeg;base64,")
        assert refs[1]["reference_voice"].startswith("data:audio/wav;base64,")

    def test_reference_audio_targets_align_by_explicit_index_not_position(self, tmp_path):
        """Audio order from first speaker occurrences and image order from first mentions derive independently.

        With references=[场景, 张三] (图0=场景, 图1=张三), audio_speakers=[张三] identifies the sole
        speaker. Positional alignment would attach 张三’s voice to scenery; targets=[1] explicitly
        links that voice to image 1, requiring reference_items[1].
        """
        payload = self._backend()._build_payload(
            VideoGenerationRequest(
                prompt="场景先出现，张三说话",
                output_path=tmp_path / "o.mp4",
                reference_images=self._refs(tmp_path, 2),
                reference_audio_files=[self._audio(tmp_path, "zhangsan.mp3")],
                reference_audio_targets=[1],
            )
        )

        refs = [m for m in payload["input"]["media"] if m["type"] == "reference_image"]
        assert "reference_voice" not in refs[0]
        assert refs[1]["reference_voice"].startswith("data:audio/mpeg;base64,")

    def test_reference_audio_targets_out_of_range_raises_slots_insufficient(self, tmp_path):
        with pytest.raises(VideoCapabilityError) as exc:
            self._backend()._build_payload(
                VideoGenerationRequest(
                    prompt="x",
                    output_path=tmp_path / "o.mp4",
                    reference_images=self._refs(tmp_path, 1),
                    reference_audio_files=[self._audio(tmp_path, "a.mp3")],
                    reference_audio_targets=[5],
                )
            )

        assert exc.value.code == "video_reference_audio_slots_insufficient"

    def test_reference_audio_targets_duplicate_index_raises_instead_of_silently_overwriting(self, tmp_path):
        """Two audio clips targeting one reference would overwrite earlier bindings; fail hard instead."""
        with pytest.raises(VideoCapabilityError) as exc:
            self._backend()._build_payload(
                VideoGenerationRequest(
                    prompt="两人对话",
                    output_path=tmp_path / "o.mp4",
                    reference_images=self._refs(tmp_path, 2),
                    reference_audio_files=[self._audio(tmp_path, "a.mp3"), self._audio(tmp_path, "b.wav")],
                    reference_audio_targets=[0, 0],
                )
            )

        assert exc.value.code == "video_reference_audio_slots_insufficient"

    def test_fewer_audios_than_references_leaves_rest_unvoiced(self, tmp_path):
        payload = self._backend()._build_payload(
            VideoGenerationRequest(
                prompt="一人说话",
                output_path=tmp_path / "o.mp4",
                reference_images=self._refs(tmp_path, 3),
                reference_audio_files=[self._audio(tmp_path, "a.mp3")],
            )
        )

        refs = [m for m in payload["input"]["media"] if m["type"] == "reference_image"]
        assert "reference_voice" in refs[0]
        assert all("reference_voice" not in r for r in refs[1:])

    def test_no_audio_leaves_reference_items_untouched(self, tmp_path):
        payload = self._backend()._build_payload(
            VideoGenerationRequest(
                prompt="无对白",
                output_path=tmp_path / "o.mp4",
                reference_images=self._refs(tmp_path, 2),
            )
        )

        refs = [m for m in payload["input"]["media"] if m["type"] == "reference_image"]
        assert all("reference_voice" not in r for r in refs)

    def test_more_audios_than_references_raises(self, tmp_path):
        """Never drop unattachable audio, silently invalidating voice declarations while billing.

        Insufficient attachable references differs from gate model-cap overflow and uses another
        code: repairs are opposite, add references versus reduce characters, so sharing would mislead.
        """
        with pytest.raises(VideoCapabilityError) as exc:
            self._backend()._build_payload(
                VideoGenerationRequest(
                    prompt="三人对话",
                    output_path=tmp_path / "o.mp4",
                    reference_images=self._refs(tmp_path, 1),
                    reference_audio_files=[self._audio(tmp_path, "a.mp3"), self._audio(tmp_path, "b.mp3")],
                )
            )

        assert exc.value.code == "video_reference_audio_slots_insufficient"
        assert exc.value.params["slots"] == 1
        assert exc.value.params["count"] == 2

    def test_model_without_reference_images_rejects_audio_instead_of_dropping(self, tmp_path):
        """Audio on models without references must error, never disappear silently.

        Reachable when custom providers override endpoint reference_audio_mode to direct, whose
        delegate forwards audio, but models use no-reference profiles such as wan2.7-i2v. With
        nowhere to attach audio, dropping it would bill randomly voiced videos.
        """

        with pytest.raises(VideoCapabilityError) as exc:
            DashScopeAlwaysOnVideoBackend(api_key="sk", model="wan2.7-i2v")._build_payload(
                VideoGenerationRequest(
                    prompt="独白",
                    output_path=tmp_path / "o.mp4",
                    start_image=self._refs(tmp_path, 1)[0],
                    reference_audio_files=[self._audio(tmp_path, "a.mp3")],
                    video_capabilities=_effective("wan2.7-i2v"),
                )
            )

        assert exc.value.code == "video_reference_audio_unsupported"

    def test_missing_audio_file_raises(self, tmp_path):
        with pytest.raises(VideoCapabilityError) as exc:
            self._backend()._build_payload(
                VideoGenerationRequest(
                    prompt="x",
                    output_path=tmp_path / "o.mp4",
                    reference_images=self._refs(tmp_path, 1),
                    reference_audio_files=[tmp_path / "missing.mp3"],
                )
            )

        assert exc.value.code == "video_reference_audio_unreadable"

    def test_unsupported_audio_format_raises(self, tmp_path):
        with pytest.raises(VideoCapabilityError) as exc:
            self._backend()._build_payload(
                VideoGenerationRequest(
                    prompt="x",
                    output_path=tmp_path / "o.mp4",
                    reference_images=self._refs(tmp_path, 1),
                    reference_audio_files=[self._audio(tmp_path, "a.ogg")],
                )
            )

        assert exc.value.code == "video_reference_audio_format_unsupported"


class TestWan2Aliases:
    """wan2.7 model_id profiles unify hyphen/underscore/dot aliases. WAN2_PATTERN covers 2.7 only,
    never other 2.x versions such as 2.1/2.2; see its source explanation.
    """

    @pytest.mark.parametrize("model", ["wan-2.7-r2v", "wan_2.7-r2v", "wan_2.7_r2v"])
    def test_alias_forms_get_wan27_r2v_capabilities(self, model):
        """Discovery hyphen/underscore aliases already routed here by endpoints.py must resolve wan2.7-r2v, not generic profiles.

        Share WAN2_PATTERN with endpoints.infer_endpoint; differing breadth would route here yet
        drop reference/first-frame parameters as generic models. wan_2.7_r2v additionally needs
        underscore modality separators normalized to hyphens to match _MODEL_PROFILES keys.
        """

        assert _profile_key(model) == "wan2.7-r2v"
        assert DashScopeReferenceVoiceVideoBackend.video_capabilities_for_model(model).reference_audio_per_image is True

    @pytest.mark.parametrize("model", ["wan-2.7-image-to-video", "wan_2.7-image2video", "wan_2.7_image_to_video"])
    def test_image_to_video_alias_gets_wan27_i2v_capabilities(self, model):
        """Normalized image-to-video aliases retain that long suffix, e.g. wan2.7-image-to-video, which
        never substring-matches wan2.7-i2v. Fold suffixes to i2v before lookup, or silent default
        profiles lose first_frame and _build_media omits start_image.
        """
        from lib.backends.video_backends.dashscope import DashScopeVideoBackend

        assert _profile_key(model) == "wan2.7-i2v"
        assert DashScopeVideoBackend.video_capabilities_for_model(model).max_prompt_chars == 5000

    def test_fully_underscored_t2v_alias_does_not_get_default_first_frame(self):
        """wan_2.7_t2v underscore version/modality separators must resolve wan2.7-t2v, never generic channel fallback."""
        from lib.backends.video_backends.dashscope import DashScopeVideoBackend

        assert _profile_key("wan_2.7_t2v") == "wan2.7-t2v"
        assert DashScopeVideoBackend.video_capabilities_for_model("wan_2.7_t2v").max_prompt_chars == 5000

    @pytest.mark.parametrize("model", ["swan2", "vendorwan2", "wan20", "swan2.7-r2v", "vendorwan2.7-t2v"])
    def test_substring_without_boundary_does_not_get_wan2_capabilities(self, model):
        """wan2 substrings lacking nonalphanumeric boundaries must not imply Wan 2.x membership.

        swan2.7-r2v/vendorwan2.7-t2v have attached left letters but valid modality suffixes;
        WAN2_PATTERN boundaries alone cannot prevent later _profile_for_model fallback matching.
        Removing the first character of swan2.7-r2v yields exact wan2.7-r2v keys, so that loop
        also needs left-boundary checks for every key.
        """
        from lib.backends.video_backends.dashscope import DashScopeVideoBackend

        assert _profile_key(model) is None
        assert DashScopeVideoBackend.video_capabilities_for_model(model).max_prompt_chars is None

    @pytest.mark.parametrize("model", ["wan-2.1-r2v", "wan_2.2-i2v"])
    def test_non_27_alias_forms_fall_back_to_default_profile(self, model):
        """WAN2_PATTERN covers 2.7 only; other hyphen/underscore 2.x versions cannot inherit its profiles.
        Fixed endpoints mismatch those versions’ protocols (see source notes), and endpoints.py
        never routes them here; profile resolution must likewise stay in scope.
        """
        from lib.backends.video_backends.dashscope import DashScopeVideoBackend

        assert _profile_key(model) is None
        assert DashScopeVideoBackend.video_capabilities_for_model(model).max_prompt_chars is None


class TestWan3:
    """wan3.0-video supports all three paths, first/last frames, independent audio references, and controllable output audio."""

    @pytest.mark.parametrize("model", ["ep-opaque", "wan2.7-r2v"])
    def test_bound_model_uses_wan3_request_shape_and_endpoint(self, tmp_path, model):
        from lib.catalog import query as catalog

        entry = catalog.find_entry("wan3.0-video", media_type="video")
        assert entry is not None
        backend = DashScopeWan3VideoBackend(
            api_key="TEST-INFERENCE-KEY",
            model=model,
            wan3_base_url="https://maas.example.invalid/api/v1",
        ).with_catalog_entry(entry)

        payload = backend._build_payload(
            VideoGenerationRequest(prompt="test", output_path=tmp_path / "o.mp4", generate_audio=False)
        )

        assert backend.video_capabilities.text_to_video is True
        assert payload["model"] == model
        assert payload["parameters"]["audio"] is False
        assert "media" not in payload["input"]
        assert backend._request_base_url == "https://maas.example.invalid/api/v1"

    @staticmethod
    def _backend(**kwargs):

        return DashScopeWan3VideoBackend(api_key="sk", model="wan3.0-video", **kwargs)

    @staticmethod
    def _file(tmp_path, name: str, data: bytes = b"\x89PNG\r\n") -> Path:
        p = tmp_path / name
        p.write_bytes(data)
        return p

    def test_declares_capabilities(self):
        caps = _effective("wan3.0-video")
        assert caps.first_frame is True
        assert caps.last_frame is True
        assert caps.max_reference_images == 10
        assert caps.reference_audio_mode is ReferenceAudioMode.DIRECT
        assert caps.max_reference_audio_count == 5
        assert caps.max_reference_audio_total_seconds == 15.0
        assert caps.max_prompt_chars == 20000
        # Audio is independent media, unlike reference-item attachment in wan2.7-r2v.
        assert caps.reference_audio_per_image is False

    @pytest.mark.parametrize("model", ["wan-3-turbo", "wan3-turbo", "wan_3-turbo", "wan_3_turbo"])
    def test_alias_forms_get_wan3_capabilities(self, model):
        """Discovery hyphen/underscore aliases already routed here resolve wan3.0, never default profiles.

        Share WAN3_PATTERN with endpoints.infer_endpoint/duration_presets; differing breadth would
        route here but lose reference/last-frame/audio parameters as generic models.
        """
        from lib.backends.video_backends.dashscope import DashScopeVideoBackend

        assert _profile_key(model) == "wan3.0-video"
        assert DashScopeVideoBackend.video_capabilities_for_model(model).max_prompt_chars == 20000

    @pytest.mark.parametrize("model", ["swan3", "vendorwan3", "wan30"])
    def test_wan3_substring_without_boundary_does_not_get_wan3_capabilities(self, model):
        """wan3 substrings without nonalphanumeric boundaries never imply Wan 3.0 membership."""
        from lib.backends.video_backends.dashscope import DashScopeVideoBackend

        assert _profile_key(model) is None
        assert DashScopeVideoBackend.video_capabilities_for_model(model).max_prompt_chars is None

    def test_first_and_last_frame_in_media(self, tmp_path):
        payload = self._backend()._build_payload(
            VideoGenerationRequest(
                prompt="p",
                output_path=tmp_path / "o.mp4",
                start_image=self._file(tmp_path, "s.png"),
                end_image=self._file(tmp_path, "e.png"),
            )
        )
        types = [m["type"] for m in payload["input"]["media"]]
        assert types == ["first_frame", "last_frame"]
        # Present first frames determine ratios upstream; omit ratio.
        assert "ratio" not in payload["parameters"]

    def test_reference_audio_is_standalone_media_item(self, tmp_path):
        payload = self._backend()._build_payload(
            VideoGenerationRequest(
                prompt="p",
                output_path=tmp_path / "o.mp4",
                reference_images=[self._file(tmp_path, "r0.png")],
                reference_audio_files=[
                    self._file(tmp_path, "a.mp3", b"riff"),
                    self._file(tmp_path, "b.wav", b"riff"),
                ],
            )
        )
        media = payload["input"]["media"]
        assert [m["type"] for m in media] == ["reference_image", "reference_audio", "reference_audio"]
        # Ordering is the prompt 音频N contract; never reorder.
        assert media[1]["url"].startswith("data:audio/mpeg;base64,")
        assert media[2]["url"].startswith("data:audio/wav;base64,")
        # Audio never attaches to image-reference items.
        assert "reference_voice" not in media[0]

    def test_reference_images_optional(self, tmp_path):
        """All-path models’ text/image-to-video requests need no references, unlike r2v-specific required profiles."""
        payload = self._backend()._build_payload(
            VideoGenerationRequest(
                prompt="p", output_path=tmp_path / "o.mp4", start_image=self._file(tmp_path, "s.png")
            )
        )
        assert [m["type"] for m in payload["input"]["media"]] == ["first_frame"]

    @pytest.mark.parametrize("generate_audio", [True, False])
    def test_audio_switch_is_sent(self, tmp_path, generate_audio):
        payload = self._backend()._build_payload(
            VideoGenerationRequest(prompt="p", output_path=tmp_path / "o.mp4", generate_audio=generate_audio)
        )
        assert payload["parameters"]["audio"] is generate_audio

    @pytest.mark.parametrize("model", ["wan2.7-i2v", "happyhorse-1.1-i2v"])
    def test_audio_switch_not_sent_for_always_on_models(self, tmp_path, model):
        """Always-audible models reject this parameter upstream."""

        payload = DashScopeAlwaysOnVideoBackend(api_key="sk", model=model)._build_payload(
            VideoGenerationRequest(prompt="p", output_path=tmp_path / "o.mp4", generate_audio=True)
        )
        assert "audio" not in payload["parameters"]

    @pytest.mark.parametrize("model", ["wan2.7-i2v", "happyhorse-1.1-i2v"])
    def test_silent_request_on_always_on_model_rejected(self, tmp_path, model):
        """Always-audible models cannot request silence; reject rather than produce unwanted audio."""

        with pytest.raises(VideoCapabilityError) as ei:
            DashScopeAlwaysOnVideoBackend(api_key="sk", model=model)._build_payload(
                VideoGenerationRequest(prompt="p", output_path=tmp_path / "o.mp4", generate_audio=False)
            )
        assert ei.value.code == "video_audio_switch_not_supported"

    def test_end_frame_on_model_without_last_frame_rejected(self, tmp_path):

        with pytest.raises(VideoCapabilityError) as ei:
            DashScopeAlwaysOnVideoBackend(api_key="sk", model="happyhorse-1.0-i2v")._build_payload(
                VideoGenerationRequest(
                    prompt="p",
                    output_path=tmp_path / "o.mp4",
                    start_image=self._file(tmp_path, "s.png"),
                    end_image=self._file(tmp_path, "e.png"),
                    video_capabilities=_effective("happyhorse-1.0-i2v"),
                )
            )
        assert ei.value.code == "video_last_frame_unsupported"

    @pytest.mark.parametrize("model", ["wan-3-turbo", "wan3-turbo", "wan_3-turbo", "wan_3_turbo"])
    def test_audio_switch_is_sent_for_alias_forms(self, tmp_path, model):
        """Aliases also route as controllable-audio models, never always-audible defaults."""

        payload = DashScopeWan3VideoBackend(api_key="sk", model=model)._build_payload(
            VideoGenerationRequest(prompt="p", output_path=tmp_path / "o.mp4", generate_audio=False)
        )
        assert payload["parameters"]["audio"] is False

    def test_prompt_over_limit_rejected_before_submit(self, tmp_path):
        """Upstream silently truncates overlimits while billing; gates reject before payment."""
        from lib.backends.video_backends.dashscope import DashScopeVideoBackend
        from lib.backends.video_frame_slots import gate_video_request

        caps = DashScopeVideoBackend.video_capabilities_for_model("wan3.0-video")
        with pytest.raises(VideoCapabilityError) as exc:
            gate_video_request(
                caps=caps,
                provider=PROVIDER_DASHSCOPE,
                model="wan3.0-video",
                prompt="超" * 20001,
            )
        assert exc.value.code == "video_prompt_too_long"

    async def test_dedicated_base_url_used_for_submit_and_poll(self):
        b = self._backend(wan3_base_url="https://maas-cn-hangzhou.example.com/ws-123/api/v1/")
        # Normalize trailing slashes; submission/poll share hosts because task IDs exist only at creating endpoints.
        assert b._request_base_url == "https://maas-cn-hangzhou.example.com/ws-123/api/v1"
        # Assert real POST/GET URLs, not properties that would stay green after either path hardcodes base_url.
        post = AsyncMock(return_value=_resp(_submit("t-wan3")))
        get = AsyncMock(return_value=_resp(_succeeded()))
        client = _client(post=post, get=get)
        assert await b._create_task(client, {}) == "t-wan3"
        await b._poll_once(client, "t-wan3", b._request_base_url)
        from lib.backends.video_backends.dashscope import _VIDEO_ENDPOINT

        assert client.posts[-1]["url"] == f"https://maas-cn-hangzhou.example.com/ws-123/api/v1{_VIDEO_ENDPOINT}"
        assert client.gets[-1]["url"] == "https://maas-cn-hangzhou.example.com/ws-123/api/v1/tasks/t-wan3"

    def test_falls_back_to_shared_base_url(self):
        b = self._backend()
        assert b._request_base_url == b._base_url
        assert b._request_base_url.endswith("/api/v1")

    def test_dedicated_base_url_not_applied_to_other_models(self):

        b = DashScopeAlwaysOnVideoBackend(
            api_key="sk", model="wan2.7-i2v", wan3_base_url="https://maas.example.com/api/v1"
        )
        assert b._request_base_url == b._base_url

    async def test_submit_persists_actual_base_url(self, tmp_path: Path):
        """Persist actual submission domains with job_ids for resume replay."""
        post = AsyncMock(return_value=_resp(_submit("t-wan3")))
        get = AsyncMock(return_value=_resp(_succeeded()))
        client = _client(post=post, get=get)
        p1, p2, p3 = _patches(client, AsyncMock())
        with p1, p2, p3, captured_provider_job_ids() as persisted:
            b = self._backend(wan3_base_url="https://maas-a.example.com/ws-1/api/v1")
            await b.generate(
                VideoGenerationRequest(
                    prompt="p", output_path=tmp_path / "o.mp4", resolution="720p", task_id="db-task-1"
                )
            )

        assert [(r["base_url"], r["endpoint"]) for r in persisted] == [("https://maas-a.example.com/ws-1/api/v1", None)]

    async def test_resume_polls_submitted_base_url_after_config_change(self, tmp_path: Path):
        """Changing wan3_base_url in flight keeps resumed polling on submission domains, never newly resolved config."""
        get = AsyncMock(return_value=_resp(_succeeded()))
        client = _client(post=AsyncMock(), get=get)
        p1, p2, p3 = _patches(client, AsyncMock())
        with p1, p2, p3:
            # Config now B, submission used A.
            b = self._backend(wan3_base_url="https://maas-b.example.com/ws-2/api/v1")
            await b.resume_video(
                "t-wan3",
                VideoGenerationRequest(
                    prompt="p",
                    output_path=tmp_path / "o.mp4",
                    resolution="720p",
                    submitted_base_url="https://maas-a.example.com/ws-1/api/v1",
                ),
            )

        assert client.gets[-1]["url"] == "https://maas-a.example.com/ws-1/api/v1/tasks/t-wan3"


class TestGenericEndpointAudio:
    """Generic ``dashscope-video`` sends no audio switches; declarations, requests, and metering are always-audible.

    No generic DashScope video switch exists: legacy text-to-video/wan2.7 omit ``parameters.audio``;
    image-to-video only wan2.6-i2v-flash has it, default audible. Source:
    ``docs/api-docs/endpoints/dashscope-video.md``.
    """

    _MODELS = ("proxy-custom-video", "wan2.7-videoedit", "wan2.6-i2v-flash")

    @pytest.mark.parametrize("model", _MODELS)
    def test_capability_declares_fixed_audio(self, model):
        from arcreel_market_core.video_backend_contract import VideoAudioMode
        from lib.backends.backend_assembly.specs import declared_builtin_video_capabilities
        from lib.backends.video_backends.dashscope import DashScopeVideoBackend

        assert DashScopeVideoBackend.video_capabilities_for_model(model).audio_track is VideoAudioMode.ALWAYS_ON
        declared = declared_builtin_video_capabilities("dashscope", model, endpoint="dashscope-video")
        assert declared.audio_track is VideoAudioMode.ALWAYS_ON

    @pytest.mark.parametrize("model", _MODELS)
    def test_request_sends_no_audio_switch_and_rejects_silent(self, tmp_path, model):
        from lib.backends.video_backends.dashscope import DashScopeVideoBackend

        backend = DashScopeVideoBackend(api_key="sk", model=model)
        payload = backend._build_payload(
            VideoGenerationRequest(prompt="p", output_path=tmp_path / "o.mp4", generate_audio=True)
        )
        assert "audio" not in payload["parameters"]
        with pytest.raises(VideoCapabilityError) as ei:
            backend._build_payload(
                VideoGenerationRequest(prompt="p", output_path=tmp_path / "o.mp4", generate_audio=False)
            )
        assert ei.value.code == "video_audio_switch_not_supported"

    @pytest.mark.parametrize("generate_audio", [True, False])
    def test_metering_counts_audio(self, tmp_path, generate_audio):
        from lib.backends.video_backends.dashscope import DashScopeVideoBackend

        request = VideoGenerationRequest(
            prompt="p", output_path=tmp_path / "o.mp4", duration_seconds=5, generate_audio=generate_audio
        )
        assert DashScopeVideoBackend.metered_usage("proxy-custom-video", request).dimensions["audio"] is True

    async def test_resumed_silent_task_settles_as_audio(self, tmp_path: Path):
        """Silent requests reject before submission; only resume reaches settlement with persisted ``generate_audio=False``."""
        from lib.backends.video_backends.dashscope import DashScopeVideoBackend

        client = _client(get=AsyncMock(return_value=_resp(_succeeded())))
        p1, p2, p3 = _patches(client, AsyncMock())
        request = VideoGenerationRequest(
            prompt="p", output_path=tmp_path / "o.mp4", duration_seconds=5, generate_audio=False
        )
        with p1, p2, p3:
            result = await DashScopeVideoBackend(api_key="sk", model="proxy-custom-video").resume_video(
                "t-silent", request
            )
        assert result.generate_audio is True
        usage = DashScopeVideoBackend.metered_usage("proxy-custom-video", request, result)
        assert usage.dimensions["audio"] is True

    @pytest.mark.parametrize("endpoint", sorted(DASHSCOPE_VIDEO_BACKENDS))
    def test_every_endpoint_declares_controllable_audio_only_when_it_sends_the_switch(self, tmp_path, endpoint):
        from arcreel_market_core.video_backend_contract import VideoAudioMode

        backend_cls = DASHSCOPE_VIDEO_BACKENDS[endpoint]
        image = _ref(tmp_path, "ref.png")
        payload = backend_cls(api_key="sk", model="opaque-model")._build_payload(
            VideoGenerationRequest(
                prompt="p", output_path=tmp_path / "o.mp4", generate_audio=True, reference_images=[image]
            )
        )
        controllable = backend_cls.video_capabilities_for_model("opaque-model").audio_track
        assert ("audio" in payload["parameters"]) is (controllable is VideoAudioMode.CONTROLLABLE)


class TestCustomProviderBaseUrlReplay:
    """Custom DashScope delegation stores protocol/submission domains separately; resume replays submission domains."""

    @staticmethod
    def _wrapped(base_url: str):
        from lib.custom_provider.backends import CustomVideoBackend

        delegate = DashScopeWan3VideoBackend(api_key="sk", base_url=base_url, model="wan3.0-video")
        return CustomVideoBackend(
            provider_id="custom-7",
            delegate=delegate,
            model="wan3.0-video",
            endpoint="dashscope-video",
        )

    async def test_submit_persists_protocol_id_and_domain(self, tmp_path: Path):
        """Wrapped submissions persist protocol identities under endpoint, domains under base_url for replay."""
        post = AsyncMock(return_value=_resp(_submit("job-c1")))
        get = AsyncMock(return_value=_resp(_succeeded()))
        client = _client(post=post, get=get)
        p1, p2, p3 = _patches(client, AsyncMock())
        with p1, p2, p3, captured_provider_job_ids() as persisted:
            backend = self._wrapped("https://custom-a.example.com")
            await backend.generate(
                VideoGenerationRequest(
                    prompt="p", output_path=tmp_path / "o.mp4", resolution="720p", task_id="db-task-c1"
                )
            )

        assert [(r["endpoint"], r["base_url"]) for r in persisted] == [
            ("dashscope-video", "https://custom-a.example.com/api/v1")
        ]

    async def test_resume_polls_submitted_domain_after_base_url_change(self, tmp_path: Path):
        """In-flight custom-provider base_url changes still poll original submission hosts containing jobs."""
        get = AsyncMock(return_value=_resp(_succeeded()))
        client = _client(post=AsyncMock(), get=get)
        p1, p2, p3 = _patches(client, AsyncMock())
        with p1, p2, p3:
            # Config now B, submission used A.
            backend = self._wrapped("https://custom-b.example.com")
            await backend.resume_video(
                "job-c1",
                VideoGenerationRequest(
                    prompt="p",
                    output_path=tmp_path / "o.mp4",
                    resolution="720p",
                    submitted_base_url="https://custom-a.example.com/api/v1",
                ),
            )

        assert client.gets[-1]["url"] == "https://custom-a.example.com/api/v1/tasks/job-c1"


class TestPollErrorRedaction:
    async def test_poll_4xx_message_drops_query_credentials(self):
        from lib.backends.video_backends.dashscope import DashScopeVideoBackend

        # Polling 4xx str(exc) enters task.error_message/logs; query credentials appear in
        # raise_for_status URLs, requiring sanitization before raising.
        backend = DashScopeVideoBackend(api_key="k")
        base_url = "https://dashscope.example/api/v1"
        with capture_http() as router:
            router.get(url__regex=rf"^{base_url}/tasks/task-1").mock(
                return_value=httpx.Response(403, json={"message": "forbidden"})
            )
            async with httpx.AsyncClient(params={"api_key": "SECRETKEY"}) as client:
                with pytest.raises(httpx.HTTPStatusError) as excinfo:
                    await backend._poll_once(client, "task-1", base_url)

        assert "SECRETKEY" not in str(excinfo.value)
        assert excinfo.value.response.status_code == 403
