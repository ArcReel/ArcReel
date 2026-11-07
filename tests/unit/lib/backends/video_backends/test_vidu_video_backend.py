"""ViduVideoBackend unit tests: endpoint selection, duration coercion, unchanged resolution passthrough, and build_request fields."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from arcreel_market_core.video_backend_contract import VideoCapabilityError, VideoGenerationRequest
from lib.backends.backend_assembly.specs import _VIDU_PROFILES, request_profile_endpoint_for_name
from lib.backends.providers import PROVIDER_VIDU
from lib.backends.video_backends.vidu import (
    DEFAULT_MODEL,
    VIDU_BACKENDS,
    ViduVideoBackend,
    _coerce_duration,
)
from lib.catalog import query as catalog
from tests.fakes import bounded_poll_clock


def _profile(model: str):
    return VIDU_BACKENDS[request_profile_endpoint_for_name("vidu", model) or "vidu-video-generic"]


_DURATION_RULES = {
    (model, path): list(values)
    for model in _VIDU_PROFILES
    for path, values in _profile(model).endpoint.durations.items()
}


@pytest.fixture
def video_output_path(tmp_path: Path) -> Path:
    return tmp_path / "out.mp4"


class TestBackendBasics:
    def test_default_model_is_q3_turbo(self):
        backend = _profile(DEFAULT_MODEL)(api_key="test-key")
        assert backend.name == PROVIDER_VIDU
        assert backend.model == DEFAULT_MODEL == "viduq3-turbo"

    def test_max_reference_images_seven(self):
        backend = _profile(DEFAULT_MODEL)(api_key="test-key")
        assert backend.video_capabilities.max_reference_images == 7

    def test_entry_narrows_the_channel_for_non_reference2video_model(self):
        # The channel implements four endpoints; viduq3-pro explicitly accepts 0 reference images, so two-sided evaluation disables reference-to-video.
        # Presentation does not claim reference-image support; execution rejects requests containing reference images.
        from lib.backends.backend_assembly.specs import builtin_video_capabilities_for_model

        assert (
            _profile("viduq3-pro")(api_key="test-key", model="viduq3-pro").video_capabilities.max_reference_images == 7
        )
        caps = builtin_video_capabilities_for_model("vidu", "viduq3-pro")
        assert caps.first_frame is True
        assert caps.last_frame is True
        assert caps.max_reference_images == 0

    def test_last_frame_true_for_start_end2video_model(self):
        from lib.backends.backend_assembly.specs import builtin_video_capabilities_for_model

        assert builtin_video_capabilities_for_model("vidu", "viduq3-turbo").last_frame is True

    async def test_marks_resubmit_unsafe_after_task_creation(self, video_output_path: Path):
        backend = _profile(DEFAULT_MODEL)(api_key="test-key")
        client = MagicMock()
        client_context = MagicMock()
        client_context.__aenter__ = AsyncMock(return_value=client)
        client_context.__aexit__ = AsyncMock(return_value=False)
        resubmit_unsafe = MagicMock()
        request = VideoGenerationRequest(
            prompt="x",
            output_path=video_output_path,
            on_provider_resubmit_unsafe=resubmit_unsafe,
        )

        with (
            patch("lib.backends.video_backends.vidu.create_vidu_client", return_value=client_context),
            patch.object(backend, "_create_task", new=AsyncMock(return_value={"task_id": "job-1"})),
            patch(
                "lib.backends.video_backends.vidu.poll_with_retry",
                new=AsyncMock(side_effect=RuntimeError("poll failed")),
            ),
            pytest.raises(RuntimeError, match="poll failed"),
        ):
            await backend.generate(request)

        resubmit_unsafe.assert_called_once_with()


class TestEndpointSelection:
    def _backend(self, model: str = "viduq3-turbo") -> ViduVideoBackend:
        return _profile(model)(api_key="test-key", model=model)

    def test_text_only_picks_text2video(self, video_output_path: Path):
        backend = self._backend()
        req = VideoGenerationRequest(prompt="x", output_path=video_output_path)
        assert backend._select_endpoint(req) == "/text2video"

    def test_start_image_picks_img2video(self, tmp_path: Path, video_output_path: Path):
        backend = self._backend()
        start = tmp_path / "start.png"
        start.write_bytes(b"x")
        req = VideoGenerationRequest(prompt="x", output_path=video_output_path, start_image=start)
        assert backend._select_endpoint(req) == "/img2video"

    def test_start_and_end_image_picks_start_end2video(self, tmp_path: Path, video_output_path: Path):
        backend = self._backend()
        start, end = tmp_path / "s.png", tmp_path / "e.png"
        for p in (start, end):
            p.write_bytes(b"x")
        req = VideoGenerationRequest(prompt="x", output_path=video_output_path, start_image=start, end_image=end)
        assert backend._select_endpoint(req) == "/start-end2video"

    def test_reference_images_take_priority(self, tmp_path: Path, video_output_path: Path):
        """With refs, select reference2video even when start_image exists (implementation checks refs first)."""
        backend = self._backend()
        ref = tmp_path / "ref.png"
        ref.write_bytes(b"x")
        req = VideoGenerationRequest(prompt="x", output_path=video_output_path, reference_images=[ref])
        assert backend._select_endpoint(req) == "/reference2video"


class TestCoerceDuration:
    def test_q3_turbo_text2video_passthrough_in_range(self):
        assert _coerce_duration(_profile("viduq3-turbo").endpoint, "/text2video", 8) == 8

    def test_q3_turbo_text2video_clamps_to_nearest(self):
        # Range 1..16; values above 16 become 16.
        assert _coerce_duration(_profile("viduq3-turbo").endpoint, "/text2video", 30) == 16

    def test_vidu2_0_img2video_only_4_or_8(self):
        assert _coerce_duration(_profile("vidu2.0").endpoint, "/img2video", 12) == 8
        assert _coerce_duration(_profile("vidu2.0").endpoint, "/img2video", 5) == 4
        assert _coerce_duration(_profile("vidu2.0").endpoint, "/img2video", 4) == 4
        assert _coerce_duration(_profile("vidu2.0").endpoint, "/img2video", 8) == 8

    def test_vidu2_0_reference2video_only_4(self):
        assert _coerce_duration(_profile("vidu2.0").endpoint, "/reference2video", 8) == 4

    def test_q1_text2video_only_5(self):
        assert _coerce_duration(_profile("viduq1").endpoint, "/text2video", 10) == 5

    def test_q3_reference2video_min_3(self):
        # Range 3..16; requested 1 selects nearest 3.
        assert _coerce_duration(_profile("viduq3").endpoint, "/reference2video", 1) == 3

    @pytest.mark.parametrize("model", ["viduq2-pro", "viduq2-turbo", "viduq2-pro-fast"])
    @pytest.mark.parametrize("seconds", [9, 10])
    def test_q2_start_end2video_keeps_9_and_10_seconds(self, model: str, seconds: int):
        # Official first/last-frame range is 1–10 seconds (also viduq2-pro, viduq2-turbo, viduq2-pro-fast); send 9 and 10 unchanged.
        assert _coerce_duration(_profile(model).endpoint, "/start-end2video", seconds) == seconds

    def test_unknown_combination_passthrough(self):
        # Without a table entry, do not coerce (pass through / fall back to 5).
        assert _coerce_duration(_profile("unknown-model").endpoint, "/img2video", 9) == 9
        assert _coerce_duration(_profile("unknown-model").endpoint, "/img2video", None) == 5


class TestBuildRequest:
    """_build_request connects endpoint selection with duration/resolution/aspect_ratio/audio field assembly."""

    @patch("lib.backends.video_backends.vidu.image_to_data_uri")
    def test_text2video_body_minimal(self, mock_data_uri, video_output_path: Path):
        backend = _profile("viduq3-turbo")(api_key="test-key", model="viduq3-turbo")
        req = VideoGenerationRequest(
            prompt="hello",
            output_path=video_output_path,
            aspect_ratio="9:16",
            duration_seconds=8,
            resolution="720p",
        )
        endpoint, body = backend._build_request(req)

        assert endpoint == "/text2video"
        assert body["model"] == "viduq3-turbo"
        assert body["prompt"] == "hello"
        assert body["duration"] == 8
        assert body["resolution"] == "720p"
        assert body["aspect_ratio"] == "9:16"
        # q3 passes audio through by default.
        assert body["audio"] is True
        # text2video carries no images.
        assert "images" not in body

    @pytest.mark.parametrize(
        ("model", "resolution"),
        [("viduq1", "720p"), ("viduq3-turbo", "4K"), ("unknown-model", "9000p")],
    )
    def test_resolution_is_sent_as_requested(self, video_output_path: Path, model: str, resolution: str):
        """Model-catalog entries determine tiers: send values absent from all local tables unchanged for upstream rejection (docs/adr/0104)."""
        backend = _profile(model)(api_key="test-key", model=model)
        req = VideoGenerationRequest(prompt="x", output_path=video_output_path, resolution=resolution)

        _, body = backend._build_request(req)

        assert body["resolution"] == resolution

    def test_resolution_omitted_when_not_requested(self, video_output_path: Path):
        backend = _profile("viduq3-turbo")(api_key="test-key", model="viduq3-turbo")

        _, body = backend._build_request(VideoGenerationRequest(prompt="x", output_path=video_output_path))

        assert "resolution" not in body

    def test_metered_usage_reports_requested_resolution(self, video_output_path: Path):
        request = VideoGenerationRequest(prompt="x", output_path=video_output_path, resolution="720p")

        assert ViduVideoBackend.metered_usage("viduq1", request).dimensions["resolution"] == "720p"

    @patch("lib.backends.video_backends.vidu.image_to_data_uri", new=MagicMock(return_value="data:image/png;base64,XX"))
    def test_img2video_passes_one_image_no_aspect_ratio(self, tmp_path: Path, video_output_path: Path):
        start = tmp_path / "s.png"
        start.write_bytes(b"x")

        backend = _profile("viduq3-turbo")(api_key="test-key", model="viduq3-turbo")
        req = VideoGenerationRequest(
            prompt="x",
            output_path=video_output_path,
            start_image=start,
            aspect_ratio="9:16",  # Must be discarded.
            duration_seconds=5,
        )
        endpoint, body = backend._build_request(req)

        assert endpoint == "/img2video"
        assert body["images"] == ["data:image/png;base64,XX"]
        # img2video does not accept aspect_ratio.
        assert "aspect_ratio" not in body

    @patch("lib.backends.video_backends.vidu.image_to_data_uri", new=MagicMock(return_value="data:image/png;base64,XX"))
    def test_reference2video_with_q3_turbo(self, tmp_path: Path, video_output_path: Path):
        ref1, ref2 = tmp_path / "r1.png", tmp_path / "r2.png"
        for p in (ref1, ref2):
            p.write_bytes(b"x")

        backend = _profile("viduq3-turbo")(api_key="test-key", model="viduq3-turbo")
        req = VideoGenerationRequest(
            prompt="x",
            output_path=video_output_path,
            reference_images=[ref1, ref2],
            aspect_ratio="9:16",
            duration_seconds=5,
        )
        endpoint, body = backend._build_request(req)

        assert endpoint == "/reference2video"
        assert len(body["images"]) == 2
        # reference2video accepts aspect_ratio.
        assert body["aspect_ratio"] == "9:16"
        # Range 3..16; pass 5 through.
        assert body["duration"] == 5

    def test_non_q3_model_does_not_send_audio(self, video_output_path: Path):
        # The execution gate evaluates both sides for text-to-video support; request construction only shapes fields: non-q3 models omit the audio switch.
        backend = _profile("vidu2.0")(api_key="test-key", model="vidu2.0")
        req = VideoGenerationRequest(prompt="x", output_path=video_output_path, duration_seconds=4)
        endpoint, body = backend._build_request(req)
        assert endpoint == "/text2video"
        assert "audio" not in body


class TestUnsendableInputsRejected:
    """Reject inputs unsupported by the endpoint during assembly instead of silently discarding them."""

    @staticmethod
    def _image(tmp_path: Path, name: str) -> Path:
        p = tmp_path / name
        p.write_bytes(b"x")
        return p

    @pytest.mark.parametrize("frame", ["start_image", "end_image"])
    def test_reference_images_with_frame(self, tmp_path: Path, video_output_path: Path, frame: str):
        req = VideoGenerationRequest(
            prompt="x",
            output_path=video_output_path,
            reference_images=[self._image(tmp_path, "r.png")],
            **{frame: self._image(tmp_path, "f.png")},
        )
        with pytest.raises(VideoCapabilityError) as exc:
            _profile("viduq3-turbo")(api_key="test-key", model="viduq3-turbo")._build_request(req)
        assert exc.value.code == "video_reference_images_with_frames_unsupported"

    def test_reference_images_over_limit(self, tmp_path: Path, video_output_path: Path):
        refs = [self._image(tmp_path, f"r{i}.png") for i in range(8)]
        req = VideoGenerationRequest(prompt="x", output_path=video_output_path, reference_images=refs)
        with pytest.raises(VideoCapabilityError) as exc:
            _profile("viduq3-turbo")(api_key="test-key", model="viduq3-turbo")._build_request(req)
        assert exc.value.code == "video_reference_images_exceeded"
        assert exc.value.params == {"model": "viduq3-turbo", "limit": 7, "count": 8}

    def test_end_frame_without_start_frame(self, tmp_path: Path, video_output_path: Path):
        req = VideoGenerationRequest(
            prompt="x", output_path=video_output_path, end_image=self._image(tmp_path, "e.png")
        )
        with pytest.raises(VideoCapabilityError) as exc:
            _profile("viduq3-turbo")(api_key="test-key", model="viduq3-turbo")._build_request(req)
        assert exc.value.code == "video_end_image_requires_start_image"

    def test_reference_audio(self, tmp_path: Path, video_output_path: Path):
        req = VideoGenerationRequest(
            prompt="x", output_path=video_output_path, reference_audio_files=[self._image(tmp_path, "a.mp3")]
        )
        with pytest.raises(VideoCapabilityError) as exc:
            _profile("viduq3-turbo")(api_key="test-key", model="viduq3-turbo")._build_request(req)
        assert exc.value.code == "video_reference_audio_unsupported"


class TestPromptLength:
    """Always reject over-limit prompts; no more silent client-side truncation."""

    @pytest.mark.parametrize("model", ["viduq3", "proxy-x"])
    def test_reference_only_endpoint_declares_narrow_limit(self, model: str):
        """vidu-video-q3-reference uses only /reference2video; static declarations take that subpath's narrow value, independent of model name."""
        assert VIDU_BACKENDS["vidu-video-q3-reference"].video_capabilities_for_model(model).max_prompt_chars == 2000

    @pytest.mark.parametrize("model", ["viduq3", "viduq3-turbo", "proxy-x"])
    def test_multi_path_endpoint_declares_wide_limit(self, model: str):
        """Endpoints also supporting t2v/i2v declare the wider static value to avoid rejecting valid long prompts; construction guards r2v requests."""
        assert VIDU_BACKENDS["vidu-video-q3-turbo"].video_capabilities_for_model(model).max_prompt_chars == 5000
        assert VIDU_BACKENDS["vidu-video-generic"].video_capabilities_for_model(model).max_prompt_chars == 5000

    def test_reference2video_over_limit_raises(self, video_output_path: Path):
        backend = _profile("viduq3-turbo")(api_key="k", model="viduq3-turbo")
        req = VideoGenerationRequest(
            prompt="字" * 2001,
            output_path=video_output_path,
            duration_seconds=8,
            reference_images=[Path("a.png")],
        )
        with pytest.raises(VideoCapabilityError) as exc:
            backend._build_request(req)

        assert exc.value.code == "video_prompt_too_long"
        assert exc.value.params == {
            "provider": PROVIDER_VIDU,
            "model": "viduq3-turbo",
            "limit": 2000,
            "count": 2001,
        }

    @patch("lib.backends.video_backends.vidu.image_to_data_uri", new=MagicMock(return_value="data:image/png;base64,XX"))
    def test_reference2video_at_limit_passes_untruncated(self, video_output_path: Path):
        backend = _profile("viduq3-turbo")(api_key="k", model="viduq3-turbo")
        prompt = "字" * 2000
        req = VideoGenerationRequest(
            prompt=prompt,
            output_path=video_output_path,
            duration_seconds=8,
            reference_images=[Path("a.png")],
        )
        _endpoint, body = backend._build_request(req)

        assert body["prompt"] == prompt

    def test_text2video_allows_longer_than_reference_limit(self, video_output_path: Path):
        """Compute each endpoint's limit independently: t2v's 5000 is unaffected by /reference2video's 2000."""
        backend = _profile("viduq3-turbo")(api_key="k", model="viduq3-turbo")
        req = VideoGenerationRequest(prompt="字" * 3000, output_path=video_output_path, duration_seconds=8)
        _endpoint, body = backend._build_request(req)

        assert len(body["prompt"]) == 3000


class TestRegistryBackendConsistency:
    """Vidu catalog model facts must agree with backend runtime tables (both verified against the same official docs).

    Duration tables are the endpoint-level exception allowed by docs/adr/0018; only a subset relationship is required.
    """

    def _vidu2_model_info(self) -> catalog.CatalogModel:
        model = catalog.get_model("vidu", "vidu2.0")
        assert model is not None
        return model

    def test_reference_image_durations_matches_reference2video_rule(self):
        model_info = self._vidu2_model_info()
        assert model_info.reference_image_durations == _DURATION_RULES[("vidu2.0", "/reference2video")]

    def test_supported_durations_covers_img2video_and_start_end2video(self):
        model_info = self._vidu2_model_info()
        expected_durations = set(_DURATION_RULES[("vidu2.0", "/img2video")]) | set(
            _DURATION_RULES[("vidu2.0", "/start-end2video")]
        )
        assert set(model_info.supported_durations) == expected_durations

    def test_duration_resolution_constraints_confines_non_720p_to_4s(self):
        """The 8-second tier only produces 720p; declare 360p / 1080p as 4-second-only so the UI offers no invalid duration × resolution combinations."""
        model_info = self._vidu2_model_info()
        assert model_info.duration_resolution_constraints == {"360p": [4], "1080p": [4]}

    @pytest.mark.parametrize("name", sorted(_VIDU_PROFILES))
    def test_durations_within_endpoint_rules(self, name: str):
        """Catalog durations must lie within endpoint duration tables; otherwise _coerce_duration silently changes the chosen duration to the nearest value.

        Endpoint-level coercion is an exception allowed by docs/adr/0018: require only a subset relationship, not equality.
        """
        model_info = catalog.get_model("vidu", name, media_type="video")
        if model_info is None:
            pytest.skip(f"目录未登记视频型号 {name}")
        non_reference = {
            d for (m, ep), ds in _DURATION_RULES.items() if m == name and ep != "/reference2video" for d in ds
        }
        reference = set(_DURATION_RULES.get((name, "/reference2video"), []))
        # Reference-to-video-only models offer precisely the reference-to-video endpoint durations.
        allowed = non_reference or reference
        extra = set(model_info.supported_durations) - allowed
        assert not extra, f"目录 vidu/{name} 的 supported_durations 含端点时长表外的 {sorted(extra)}"
        extra_ref = set(model_info.reference_image_durations or []) - reference
        assert not extra_ref, (
            f"目录 vidu/{name} 的 reference_image_durations 含 /reference2video 时长表外的 {sorted(extra_ref)}"
        )


class TestCreateTask413:
    """Normalize 413: _create_task exposes httpx.HTTPStatusError retaining its status code (the choke-point layer uses it to downgrade)."""

    async def test_create_task_413_surfaces_httpstatuserror_no_retry(self):
        from unittest.mock import AsyncMock, MagicMock

        import httpx

        request = httpx.Request("POST", "https://vidu/text2video")
        response = httpx.Response(413, request=request, text="Request Entity Too Large")
        err = httpx.HTTPStatusError("error 413", request=request, response=response)

        resp = MagicMock()
        resp.status_code = 413
        resp.text = "Request Entity Too Large"
        resp.raise_for_status = MagicMock(side_effect=err)
        client = AsyncMock()
        client.post = AsyncMock(return_value=resp)

        backend = _profile("viduq3-turbo")(api_key="k", model="viduq3-turbo")
        with pytest.raises(httpx.HTTPStatusError) as ei:
            await backend._create_task(client, "/text2video", {"model": "viduq3-turbo", "prompt": "x", "duration": 8})
        assert ei.value.response.status_code == 413
        # 413 is nonretryable: fail fast in one call.
        assert client.post.call_count == 1


class TestCreateTaskAmbiguity:
    """Narrow creation retries by definite request delivery to avoid duplicate tasks and billing."""

    async def test_read_timeout_fails_fast_with_manual_retry_hint(self):
        from unittest.mock import AsyncMock

        import httpx

        from lib.backends.http_status_errors import AmbiguousSubmitError

        client = AsyncMock()
        client.post = AsyncMock(side_effect=httpx.ReadTimeout("read timed out"))

        backend = _profile("viduq3-turbo")(api_key="k", model="viduq3-turbo")
        with (
            bounded_poll_clock(),
            pytest.raises(AmbiguousSubmitError, match="手动重试"),
        ):
            await backend._create_task(client, "/text2video", {"model": "viduq3-turbo", "prompt": "x", "duration": 8})
        # Ambiguous: the request may have arrived; do not retry.
        assert client.post.call_count == 1

    async def test_connect_error_retries(self):
        from unittest.mock import AsyncMock, MagicMock

        import httpx

        ok = MagicMock()
        ok.status_code = 200
        ok.json.return_value = {"task_id": "vidu-conn"}
        client = AsyncMock()
        client.post = AsyncMock(side_effect=[httpx.ConnectError("refused"), httpx.ConnectError("refused"), ok])

        backend = _profile("viduq3-turbo")(api_key="k", model="viduq3-turbo")
        with bounded_poll_clock():
            data = await backend._create_task(
                client, "/text2video", {"model": "viduq3-turbo", "prompt": "x", "duration": 8}
            )
        assert data["task_id"] == "vidu-conn"
        # ConnectError means definitely undelivered; retry.
        assert client.post.call_count == 3


class TestQ4Endpoint:
    """vidu-video-q4: endpoint declarations supply reference images, prompt limits, and reference audio (official viduq4-preview image/reference-to-video docs)."""

    @staticmethod
    def _backend() -> ViduVideoBackend:
        return VIDU_BACKENDS["vidu-video-q4"](api_key="k", model="viduq4-preview")

    @staticmethod
    def _files(tmp_path: Path, suffix: str, count: int) -> list[Path]:
        paths = [tmp_path / f"f{i}{suffix}" for i in range(count)]
        for p in paths:
            p.write_bytes(b"x")
        return paths

    def test_prefilled_for_viduq4_preview(self):
        assert request_profile_endpoint_for_name("vidu", "viduq4-preview") == "vidu-video-q4"

    def test_declares_endpoint_limits(self):
        from arcreel_market_core.video_backend_contract import ReferenceAudioMode, VideoAudioMode

        caps = VIDU_BACKENDS["vidu-video-q4"].video_capabilities_for_model("viduq4-preview")
        assert caps.max_reference_images == 15
        assert caps.max_prompt_chars == 20000
        assert caps.reference_audio_mode is ReferenceAudioMode.DIRECT
        assert caps.max_reference_audio_count == 3
        assert caps.audio_track is VideoAudioMode.CONTROLLABLE

    @pytest.mark.parametrize("endpoint", sorted(set(VIDU_BACKENDS) - {"vidu-video-q4"}))
    def test_other_endpoints_keep_seven_images_and_no_reference_audio(self, endpoint: str):
        from arcreel_market_core.video_backend_contract import ReferenceAudioMode

        caps = VIDU_BACKENDS[endpoint].video_capabilities_for_model("opaque-id")
        assert caps.max_reference_images == 7
        assert caps.reference_audio_mode is ReferenceAudioMode.NONE

    @pytest.mark.parametrize(("requested", "sent"), [(1, 3), (3, 3), (16, 16), (20, 16)])
    def test_img2video_and_reference2video_durations_are_3_to_16(self, requested: int, sent: int):
        endpoint = VIDU_BACKENDS["vidu-video-q4"].endpoint
        assert _coerce_duration(endpoint, "/img2video", requested) == sent
        assert _coerce_duration(endpoint, "/reference2video", requested) == sent

    @patch("lib.backends.video_backends.vidu.image_to_data_uri", new=MagicMock(return_value="data:image/png;base64,XX"))
    def test_reference2video_accepts_15_images_and_long_prompt(self, tmp_path: Path, video_output_path: Path):
        req = VideoGenerationRequest(
            prompt="字" * 20000,
            output_path=video_output_path,
            reference_images=self._files(tmp_path, ".png", 15),
            duration_seconds=8,
        )
        endpoint, body = self._backend()._build_request(req)
        assert endpoint == "/reference2video"
        assert len(body["images"]) == 15
        assert len(body["prompt"]) == 20000
        # Default request has audio: send the endpoint audio switch.
        assert body["audio"] is True
        assert "sounds" not in body

    def test_reference2video_rejects_16_images(self, tmp_path: Path, video_output_path: Path):
        req = VideoGenerationRequest(
            prompt="x", output_path=video_output_path, reference_images=self._files(tmp_path, ".png", 16)
        )
        with pytest.raises(VideoCapabilityError) as exc:
            self._backend()._build_request(req)
        assert exc.value.code == "video_reference_images_exceeded"
        assert exc.value.params == {"model": "viduq4-preview", "limit": 15, "count": 16}

    def test_rejects_prompt_over_20000(self, tmp_path: Path, video_output_path: Path):
        req = VideoGenerationRequest(
            prompt="字" * 20001, output_path=video_output_path, start_image=self._files(tmp_path, ".png", 1)[0]
        )
        with pytest.raises(VideoCapabilityError) as exc:
            self._backend()._build_request(req)
        assert exc.value.code == "video_prompt_too_long"
        assert exc.value.params["limit"] == 20000

    @patch("lib.backends.video_backends.vidu.image_to_data_uri", new=MagicMock(return_value="data:image/png;base64,XX"))
    def test_reference2video_sends_sounds_in_order(self, tmp_path: Path, video_output_path: Path):
        audio = [tmp_path / f"voice{i}.mp3" for i in range(3)]
        for i, p in enumerate(audio):
            p.write_bytes(bytes([i]))
        req = VideoGenerationRequest(
            prompt="x",
            output_path=video_output_path,
            reference_images=self._files(tmp_path, ".png", 1),
            reference_audio_files=audio,
        )
        _endpoint, body = self._backend()._build_request(req)
        # The official example content-type string is data:video/mp3.
        assert body["sounds"] == [
            "data:video/mp3;base64,AA==",
            "data:video/mp3;base64,AQ==",
            "data:video/mp3;base64,Ag==",
        ]

    def test_reference2video_rejects_more_than_3_sounds(self, tmp_path: Path, video_output_path: Path):
        req = VideoGenerationRequest(
            prompt="x",
            output_path=video_output_path,
            reference_images=self._files(tmp_path, ".png", 1),
            reference_audio_files=self._files(tmp_path, ".mp3", 4),
        )
        with pytest.raises(VideoCapabilityError) as exc:
            self._backend()._build_request(req)
        assert exc.value.code == "video_reference_audio_exceeded"
        assert exc.value.params == {"provider": PROVIDER_VIDU, "model": "viduq4-preview", "limit": 3, "count": 4}

    def test_reference2video_rejects_non_mp3_sound(self, tmp_path: Path, video_output_path: Path):
        req = VideoGenerationRequest(
            prompt="x",
            output_path=video_output_path,
            reference_images=self._files(tmp_path, ".png", 1),
            reference_audio_files=self._files(tmp_path, ".wav", 1),
        )
        with pytest.raises(VideoCapabilityError) as exc:
            self._backend()._build_request(req)
        assert exc.value.code == "video_reference_audio_format_unsupported"

    def test_img2video_rejects_sounds(self, tmp_path: Path, video_output_path: Path):
        # Only reference-to-video accepts sounds; reject reference audio on image-to-video before submission instead of silently discarding it.
        req = VideoGenerationRequest(
            prompt="x",
            output_path=video_output_path,
            start_image=self._files(tmp_path, ".png", 1)[0],
            reference_audio_files=self._files(tmp_path, ".mp3", 1),
        )
        with pytest.raises(VideoCapabilityError) as exc:
            self._backend()._build_request(req)
        assert exc.value.code == "video_reference_audio_unsupported"

    def test_sounds_are_not_logged(self):
        from lib.backends.vidu_shared import safe_body_for_log

        view = safe_body_for_log({"model": "viduq4-preview", "sounds": ["data:video/mp3;base64,SECRET"]})
        assert view["sounds"] == "<1 data uri>"
