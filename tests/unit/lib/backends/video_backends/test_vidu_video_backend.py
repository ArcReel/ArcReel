"""ViduVideoBackend 单元测试 — 重点：endpoint 选择、duration 强制、resolution 原样透传、build_request 字段。"""

from __future__ import annotations

from base64 import b64encode
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from arcreel_market_core.video_backend_contract import VideoCapabilityError, VideoGenerationRequest
from lib.backends.endpoint_prefill import _VIDU_PROFILES, request_profile_endpoint_for_name
from lib.backends.providers import PROVIDER_VIDU
from lib.backends.video_backends.vidu import (
    DEFAULT_MODEL,
    VIDU_BACKENDS,
    ViduVideoBackend,
    _coerce_duration,
)
from lib.catalog import query as catalog
from tests.factories import image_bytes
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
        start.write_bytes(image_bytes())
        req = VideoGenerationRequest(prompt="x", output_path=video_output_path, start_image=start)
        assert backend._select_endpoint(req) == "/img2video"

    def test_start_and_end_image_picks_start_end2video(self, tmp_path: Path, video_output_path: Path):
        backend = self._backend()
        start, end = tmp_path / "s.png", tmp_path / "e.png"
        for p in (start, end):
            p.write_bytes(image_bytes())
        req = VideoGenerationRequest(prompt="x", output_path=video_output_path, start_image=start, end_image=end)
        assert backend._select_endpoint(req) == "/start-end2video"

    def test_reference_images_take_priority(self, tmp_path: Path, video_output_path: Path):
        """有 refs 时即使带 start_image 也应走 reference2video（依实现：refs 先判）。"""
        backend = self._backend()
        ref = tmp_path / "ref.png"
        ref.write_bytes(image_bytes())
        req = VideoGenerationRequest(prompt="x", output_path=video_output_path, reference_images=[ref])
        assert backend._select_endpoint(req) == "/reference2video"


class TestCoerceDuration:
    def test_q3_turbo_text2video_passthrough_in_range(self):
        assert _coerce_duration(_profile("viduq3-turbo").endpoint, "/text2video", 8) == 8

    def test_q3_turbo_text2video_clamps_to_nearest(self):
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
        assert _coerce_duration(_profile("viduq3").endpoint, "/reference2video", 1) == 3

    @pytest.mark.parametrize("model", ["viduq2-pro", "viduq2-turbo", "viduq2-pro-fast"])
    @pytest.mark.parametrize("seconds", [9, 10])
    def test_q2_start_end2video_keeps_9_and_10_seconds(self, model: str, seconds: int):
        assert _coerce_duration(_profile(model).endpoint, "/start-end2video", seconds) == seconds

    def test_unknown_combination_passthrough(self):
        assert _coerce_duration(_profile("unknown-model").endpoint, "/img2video", 9) == 9
        assert _coerce_duration(_profile("unknown-model").endpoint, "/img2video", None) == 5


class TestBuildRequest:
    """_build_request 是核心串联函数：endpoint 选择 + duration/resolution/aspect_ratio/audio 字段拼装。"""

    def test_text2video_body_minimal(self, video_output_path: Path):
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
        assert body["audio"] is True
        assert "images" not in body

    @pytest.mark.parametrize(
        ("model", "resolution"),
        [("viduq1", "720p"), ("viduq3-turbo", "4K"), ("unknown-model", "9000p")],
    )
    def test_resolution_is_sent_as_requested(self, video_output_path: Path, model: str, resolution: str):
        """档位以模型目录条目为准：不在任何本地表里的值也原样下发，由上游拒绝（docs/adr/0104）。"""
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

    def test_img2video_passes_one_image_no_aspect_ratio(self, tmp_path: Path, video_output_path: Path):
        start = tmp_path / "s.png"
        start.write_bytes(image_bytes())

        backend = _profile("viduq3-turbo")(api_key="test-key", model="viduq3-turbo")
        req = VideoGenerationRequest(
            prompt="x",
            output_path=video_output_path,
            start_image=start,
            aspect_ratio="9:16",
            duration_seconds=5,
        )
        endpoint, body = backend._build_request(req)

        assert endpoint == "/img2video"
        assert body["images"] == ["data:image/png;base64," + b64encode(image_bytes()).decode()]
        assert "aspect_ratio" not in body

    def test_reference2video_with_q3_turbo(self, tmp_path: Path, video_output_path: Path):
        ref1, ref2 = tmp_path / "r1.png", tmp_path / "r2.png"
        for p in (ref1, ref2):
            p.write_bytes(image_bytes())

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
        assert body["aspect_ratio"] == "9:16"
        assert body["duration"] == 5

    def test_non_q3_model_does_not_send_audio(self, video_output_path: Path):
        backend = _profile("vidu2.0")(api_key="test-key", model="vidu2.0")
        req = VideoGenerationRequest(prompt="x", output_path=video_output_path, duration_seconds=4)
        endpoint, body = backend._build_request(req)
        assert endpoint == "/text2video"
        assert "audio" not in body


class TestUnsendableInputsRejected:
    """端点发不出的输入在组装请求时拒绝，不静默丢弃。"""

    @staticmethod
    def _image(tmp_path: Path, name: str) -> Path:
        p = tmp_path / name
        p.write_bytes(image_bytes())
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
        assert exc.value.params == {"names": "r7.png", "model": "viduq3-turbo", "limit": 7, "count": 8}

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
    """prompt 超限一律拒绝，不再客户端静默截断。"""

    @pytest.mark.parametrize("model", ["viduq3", "proxy-x"])
    def test_reference_only_endpoint_declares_narrow_limit(self, model: str):
        """vidu-video-q3-reference 只跑 /reference2video，静态声明取该子路径的窄值，与型号名无关。"""
        assert VIDU_BACKENDS["vidu-video-q3-reference"].video_capabilities_for_model(model).max_prompt_chars == 2000

    @pytest.mark.parametrize("model", ["viduq3", "viduq3-turbo", "proxy-x"])
    def test_multi_path_endpoint_declares_wide_limit(self, model: str):
        """还能跑 t2v/i2v 的端点静态声明取宽值——否则会误拒其合法的长 prompt；走 r2v 时由请求构造兜底。"""
        assert VIDU_BACKENDS["vidu-video-q3-turbo"].video_capabilities_for_model(model).max_prompt_chars == 5000
        assert VIDU_BACKENDS["vidu-video-generic"].video_capabilities_for_model(model).max_prompt_chars == 5000

    def test_reference2video_over_limit_raises(self, video_output_path: Path):
        backend = _profile("viduq3-turbo")(api_key="k", model="viduq3-turbo")
        req = VideoGenerationRequest(
            prompt="字" * 5001,
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
            "limit": 5000,
            "count": 5001,
        }

    def test_reference2video_at_limit_passes_untruncated(self, video_output_path: Path):
        backend = _profile("viduq3-turbo")(api_key="k", model="viduq3-turbo")
        prompt = "字" * 5000
        ref = video_output_path.with_suffix(".png")
        ref.write_bytes(image_bytes())
        req = VideoGenerationRequest(
            prompt=prompt,
            output_path=video_output_path,
            duration_seconds=8,
            reference_images=[ref],
        )
        endpoint, body = backend._build_request(req)

        assert endpoint == "/reference2video"
        assert body["prompt"] == prompt

    def test_text2video_allows_longer_than_reference_limit(self, video_output_path: Path):
        """端点上限按端点各算：t2v 的 5000 不受 /reference2video 的 2000 牵连。"""
        backend = _profile("viduq2")(api_key="k", model="viduq2")
        req = VideoGenerationRequest(prompt="字" * 3000, output_path=video_output_path, duration_seconds=8)
        endpoint, body = backend._build_request(req)

        assert endpoint == "/text2video"
        assert len(body["prompt"]) == 3000


class TestRegistryBackendConsistency:
    """目录里 vidu 视频条目的模型事实与 backend 执行期表须无分歧（两侧同一份官方文档核实）。

    时长表是 docs/adr/0104 允许的端点级例外，两侧只要求子集。
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
        """8 秒档只出 720p——360p / 1080p 须声明仅 4 秒可选，UI 才不会给出无效的时长×分辨率组合。"""
        model_info = self._vidu2_model_info()
        assert model_info.duration_resolution_constraints == {"360p": [4], "1080p": [4]}

    @pytest.mark.parametrize("name", sorted(_VIDU_PROFILES))
    def test_durations_within_endpoint_rules(self, name: str):
        """目录时长须落在端点级时长表内，否则 _coerce_duration 会把用户选的时长静默改成最近值。

        端点级校正是 docs/adr/0104 允许的例外，两侧只要求子集，不要求相等。
        """
        model_info = catalog.get_model("vidu", name, media_type="video")
        if model_info is None:
            pytest.skip(f"目录未登记视频型号 {name}")
        non_reference = {
            d for (m, ep), ds in _DURATION_RULES.items() if m == name and ep != "/reference2video" for d in ds
        }
        reference = set(_DURATION_RULES.get((name, "/reference2video"), []))
        allowed = non_reference or reference
        extra = set(model_info.supported_durations) - allowed
        assert not extra, f"目录 vidu/{name} 的 supported_durations 含端点时长表外的 {sorted(extra)}"
        extra_ref = set(model_info.reference_image_durations or []) - reference
        assert not extra_ref, (
            f"目录 vidu/{name} 的 reference_image_durations 含 /reference2video 时长表外的 {sorted(extra_ref)}"
        )


class TestCreateTask413:
    """413 规整：_create_task 透出保留状态码的 httpx.HTTPStatusError（咽喉层据此降档）。"""

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
        assert client.post.call_count == 1


class TestCreateTaskAmbiguity:
    """create 阶段按「请求是否确定送达」收窄重试，避免重复建任务 + 重复计费。"""

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
        assert client.post.call_count == 3


class TestQ4Endpoint:
    """vidu-video-q4：参考图、提示词上限与参考音频由端点声明（官方 viduq4-preview 图生视频、参考生视频文档）。"""

    @staticmethod
    def _backend() -> ViduVideoBackend:
        return VIDU_BACKENDS["vidu-video-q4"](api_key="k", model="viduq4-preview")

    @staticmethod
    def _files(tmp_path: Path, suffix: str, count: int) -> list[Path]:
        paths = [tmp_path / f"f{i}{suffix}" for i in range(count)]
        for p in paths:
            p.write_bytes(image_bytes() if suffix == ".png" else b"audio")
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
    def test_other_endpoints_keep_seven_images(self, endpoint: str):
        caps = VIDU_BACKENDS[endpoint].video_capabilities_for_model("opaque-id")
        assert caps.max_reference_images == 7

    @pytest.mark.parametrize("endpoint", sorted(set(VIDU_BACKENDS) - {"vidu-video-q4", "vidu-video-q3-drama"}))
    def test_endpoints_without_sounds_take_no_reference_audio(self, endpoint: str):
        from arcreel_market_core.video_backend_contract import ReferenceAudioMode

        caps = VIDU_BACKENDS[endpoint].video_capabilities_for_model("opaque-id")
        assert caps.reference_audio_mode is ReferenceAudioMode.NONE

    @pytest.mark.parametrize(("requested", "sent"), [(1, 3), (3, 3), (16, 16), (20, 16)])
    def test_img2video_and_reference2video_durations_are_3_to_16(self, requested: int, sent: int):
        endpoint = VIDU_BACKENDS["vidu-video-q4"].endpoint
        assert _coerce_duration(endpoint, "/img2video", requested) == sent
        assert _coerce_duration(endpoint, "/reference2video", requested) == sent

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
        assert body["audio"] is True
        assert "sounds" not in body

    def test_reference2video_rejects_16_images(self, tmp_path: Path, video_output_path: Path):
        req = VideoGenerationRequest(
            prompt="x", output_path=video_output_path, reference_images=self._files(tmp_path, ".png", 16)
        )
        with pytest.raises(VideoCapabilityError) as exc:
            self._backend()._build_request(req)
        assert exc.value.code == "video_reference_images_exceeded"
        assert exc.value.params == {"names": "f15.png", "model": "viduq4-preview", "limit": 15, "count": 16}

    def test_rejects_prompt_over_20000(self, tmp_path: Path, video_output_path: Path):
        req = VideoGenerationRequest(
            prompt="字" * 20001, output_path=video_output_path, start_image=self._files(tmp_path, ".png", 1)[0]
        )
        with pytest.raises(VideoCapabilityError) as exc:
            self._backend()._build_request(req)
        assert exc.value.code == "video_prompt_too_long"
        assert exc.value.params["limit"] == 20000

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


_Q3_REFERENCE_ONLY = {
    "viduq3-drama": "vidu-video-q3-drama",
    "viduq3-mix": "vidu-video-q3-mix",
    "viduq3-ad": "vidu-video-q3-ad",
}


class TestQ3ReferenceOnlyModels:
    """viduq3-drama / viduq3-mix / viduq3-ad 的参考生视频契约，按官方参考生视频文档逐型号核对。

    三款都只走 /reference2video，提示词上限 20000、参考图 1～7 张、不收错峰；只有 Drama 带音轨开关和 sounds。
    """

    @staticmethod
    def _backend(model: str) -> ViduVideoBackend:
        return _profile(model)(api_key="k", model=model)

    @staticmethod
    def _files(tmp_path: Path, suffix: str, count: int) -> list[Path]:
        paths = [tmp_path / f"f{i}{suffix}" for i in range(count)]
        for p in paths:
            p.write_bytes(image_bytes() if suffix == ".png" else b"audio")
        return paths

    @pytest.mark.parametrize(("model", "endpoint"), sorted(_Q3_REFERENCE_ONLY.items()))
    def test_prefilled_by_name(self, model: str, endpoint: str):
        assert request_profile_endpoint_for_name("vidu", model) == endpoint

    @pytest.mark.parametrize(
        ("endpoint", "audio_track", "reference_audio"),
        [
            ("vidu-video-q3-drama", "controllable", 3),
            ("vidu-video-q3-mix", "always_on", 0),
            ("vidu-video-q3-ad", "always_off", 0),
        ],
    )
    def test_declares_limits_and_audio(self, endpoint: str, audio_track: str, reference_audio: int):
        caps = VIDU_BACKENDS[endpoint].video_capabilities_for_model("opaque-id")
        assert caps.max_reference_images == 7
        assert caps.max_prompt_chars == 20000
        assert caps.audio_track == audio_track
        assert caps.max_reference_audio_count == reference_audio
        assert caps.reference_audio_mode == ("direct" if reference_audio else "none")

    def test_drama_body_carries_audio_switch(self, tmp_path: Path, video_output_path: Path):
        req = VideoGenerationRequest(
            prompt="x",
            output_path=video_output_path,
            reference_images=self._files(tmp_path, ".png", 7),
            duration_seconds=8,
            resolution="1080p",
            aspect_ratio="9:16",
            generate_audio=False,
        )
        endpoint, body = self._backend("viduq3-drama")._build_request(req)
        assert endpoint == "/reference2video"
        assert body == {
            "model": "viduq3-drama",
            "prompt": "x",
            "duration": 8,
            "resolution": "1080p",
            "audio": False,
            "aspect_ratio": "9:16",
            "images": ["data:image/png;base64," + b64encode(image_bytes()).decode()] * 7,
        }

    @pytest.mark.parametrize("model", ["viduq3-mix", "viduq3-ad"])
    def test_mix_and_ad_bodies_omit_undeclared_fields(self, model: str, tmp_path: Path, video_output_path: Path):
        """文档没有为这两款声明 audio、sounds 与 off_peak，请求里不发。"""
        req = VideoGenerationRequest(
            prompt="x",
            output_path=video_output_path,
            reference_images=self._files(tmp_path, ".png", 2),
            duration_seconds=15,
            resolution="720p",
            aspect_ratio="4:3",
            generate_audio=True,
        )
        endpoint, body = self._backend(model)._build_request(req)
        assert endpoint == "/reference2video"
        assert body == {
            "model": model,
            "prompt": "x",
            "duration": 15,
            "resolution": "720p",
            "aspect_ratio": "4:3",
            "images": ["data:image/png;base64," + b64encode(image_bytes()).decode()] * 2,
        }

    def test_mix_rejects_muted_request_before_submit(self, tmp_path: Path, video_output_path: Path):
        """Mix 成片固定有声且无音轨开关：静音请求在提交前拒绝；Ad 本身无声，静音请求照常发且不带 audio。"""
        refs = self._files(tmp_path, ".png", 1)
        muted = VideoGenerationRequest(
            prompt="x",
            output_path=video_output_path,
            reference_images=refs,
            aspect_ratio="16:9",
            generate_audio=False,
        )
        with pytest.raises(VideoCapabilityError) as exc:
            self._backend("viduq3-mix")._build_request(muted)
        assert exc.value.code == "video_audio_switch_not_supported"
        assert exc.value.params == {"provider": "vidu", "model": "viduq3-mix"}

        _endpoint, ad_body = self._backend("viduq3-ad")._build_request(muted)
        assert "audio" not in ad_body

    @pytest.mark.parametrize(
        ("endpoint", "requested", "recorded"),
        [
            ("vidu-video-q3-drama", False, False),
            ("vidu-video-q3-drama", True, True),
            ("vidu-video-q3-mix", False, True),
            ("vidu-video-q3-mix", True, True),
            ("vidu-video-q3-ad", True, False),
        ],
    )
    def test_metered_audio_follows_endpoint_track(
        self, endpoint: str, requested: bool, recorded: bool, tmp_path: Path, video_output_path: Path
    ):
        req = VideoGenerationRequest(
            prompt="x",
            output_path=video_output_path,
            reference_images=self._files(tmp_path, ".png", 1),
            generate_audio=requested,
        )
        backend = VIDU_BACKENDS[endpoint]
        assert backend.endpoint.output_audio(requested) is recorded
        assert backend.metered_usage("opaque-id", req).dimensions["audio"] is recorded

    @pytest.mark.parametrize(
        ("endpoint", "requested", "sent"),
        [
            ("vidu-video-q3-drama", 1, 2),
            ("vidu-video-q3-drama", 2, 2),
            ("vidu-video-q3-drama", 16, 16),
            ("vidu-video-q3-drama", 20, 16),
            ("vidu-video-q3-drama", None, 8),
            ("vidu-video-q3-mix", 2, 3),
            ("vidu-video-q3-mix", 15, 15),
            ("vidu-video-q3-mix", 16, 15),
            ("vidu-video-q3-mix", None, 5),
            ("vidu-video-q3-ad", 2, 3),
            ("vidu-video-q3-ad", 16, 15),
            ("vidu-video-q3-ad", None, 5),
        ],
    )
    def test_reference2video_durations(self, endpoint: str, requested: int | None, sent: int):
        assert _coerce_duration(VIDU_BACKENDS[endpoint].endpoint, "/reference2video", requested) == sent

    @pytest.mark.parametrize(
        ("model", "aspect_ratio", "supported"),
        [
            ("viduq3-drama", "1:1", "16:9, 9:16, auto"),
            ("viduq3-drama", "4:3", "16:9, 9:16, auto"),
            ("viduq3-mix", "21:9", "16:9, 1:1, 3:4, 4:3, 9:16, auto"),
            ("viduq3-ad", "21:9", "16:9, 1:1, 3:4, 4:3, 9:16, auto"),
        ],
    )
    def test_rejects_undeclared_aspect_ratio_before_submit(
        self, model: str, aspect_ratio: str, supported: str, tmp_path: Path, video_output_path: Path
    ):
        req = VideoGenerationRequest(
            prompt="x",
            output_path=video_output_path,
            reference_images=self._files(tmp_path, ".png", 1),
            aspect_ratio=aspect_ratio,
        )
        with pytest.raises(VideoCapabilityError) as exc:
            self._backend(model)._build_request(req)
        assert exc.value.code == "video_aspect_ratio_not_supported"
        assert exc.value.params == {"model": model, "aspect_ratio": aspect_ratio, "supported": supported}

    def test_drama_sends_sounds_in_order(self, tmp_path: Path, video_output_path: Path):
        audio = [tmp_path / f"voice{i}.mp3" for i in range(3)]
        for i, p in enumerate(audio):
            p.write_bytes(bytes([i]))
        req = VideoGenerationRequest(
            prompt="x",
            output_path=video_output_path,
            reference_images=self._files(tmp_path, ".png", 1),
            reference_audio_files=audio,
            aspect_ratio="16:9",
        )
        _endpoint, body = self._backend("viduq3-drama")._build_request(req)
        assert body["sounds"] == [
            "data:video/mp3;base64,AA==",
            "data:video/mp3;base64,AQ==",
            "data:video/mp3;base64,Ag==",
        ]

    def test_drama_rejects_more_than_3_sounds(self, tmp_path: Path, video_output_path: Path):
        req = VideoGenerationRequest(
            prompt="x",
            output_path=video_output_path,
            reference_images=self._files(tmp_path, ".png", 1),
            reference_audio_files=self._files(tmp_path, ".mp3", 4),
        )
        with pytest.raises(VideoCapabilityError) as exc:
            self._backend("viduq3-drama")._build_request(req)
        assert exc.value.code == "video_reference_audio_exceeded"

    @pytest.mark.parametrize("model", ["viduq3-mix", "viduq3-ad"])
    def test_mix_and_ad_reject_reference_audio_instead_of_dropping_it(
        self, model: str, tmp_path: Path, video_output_path: Path
    ):
        req = VideoGenerationRequest(
            prompt="x",
            output_path=video_output_path,
            reference_images=self._files(tmp_path, ".png", 1),
            reference_audio_files=self._files(tmp_path, ".mp3", 1),
        )
        with pytest.raises(VideoCapabilityError) as exc:
            self._backend(model)._build_request(req)
        assert exc.value.code == "video_reference_audio_unsupported"

    @pytest.mark.parametrize("model", sorted(_Q3_REFERENCE_ONLY))
    def test_rejects_eighth_image(self, model: str, tmp_path: Path, video_output_path: Path):
        req = VideoGenerationRequest(
            prompt="x",
            output_path=video_output_path,
            reference_images=self._files(tmp_path, ".png", 8),
            aspect_ratio="16:9",
        )
        with pytest.raises(VideoCapabilityError) as exc:
            self._backend(model)._build_request(req)
        assert exc.value.code == "video_reference_images_exceeded"
        assert exc.value.params == {"names": "f7.png", "model": model, "limit": 7, "count": 8}

    @pytest.mark.parametrize("model", sorted(_Q3_REFERENCE_ONLY))
    def test_prompt_limit_is_20000(self, model: str, tmp_path: Path, video_output_path: Path):
        req = VideoGenerationRequest(
            prompt="字" * 20001,
            output_path=video_output_path,
            reference_images=self._files(tmp_path, ".png", 1),
            aspect_ratio="16:9",
        )
        with pytest.raises(VideoCapabilityError) as exc:
            self._backend(model)._build_request(req)
        assert exc.value.code == "video_prompt_too_long"
        assert exc.value.params["limit"] == 20000

    def test_existing_q3_reference_unchanged(self, tmp_path: Path, video_output_path: Path):
        """viduq3 仍走 vidu-video-q3-reference：带音轨开关，3～16 秒，提示词上限 2000，比例不在本地校验。"""
        assert request_profile_endpoint_for_name("vidu", "viduq3") == "vidu-video-q3-reference"
        req = VideoGenerationRequest(
            prompt="x",
            output_path=video_output_path,
            reference_images=self._files(tmp_path, ".png", 1),
            duration_seconds=16,
            aspect_ratio="21:9",
        )
        backend = self._backend("viduq3")
        _endpoint, body = backend._build_request(req)
        assert body["duration"] == 16
        assert body["audio"] is True
        assert body["aspect_ratio"] == "21:9"
        assert body["images"] == ["data:image/png;base64," + b64encode(image_bytes()).decode()]
        assert backend.video_capabilities.max_prompt_chars == 2000


class TestQ3ReferenceOnlyModelsInCatalog:
    """目录收录三款并声明端点后，内置供应商按声明的端点装配，两侧求值只开参考生视频，旧版本按端点起始版本拦截。"""

    @staticmethod
    def _catalog():
        from arcreel_catalog_core import (
            BuiltinProviderListing,
            BuiltinProviderModel,
            CatalogData,
            CatalogEntry,
            endpoint_version_floor,
        )
        from arcreel_catalog_core.model import DurationConstraints, VideoFacts

        def entry(name: str, durations: range, *, reference_audio: bool) -> CatalogEntry:
            values = tuple(durations)
            return CatalogEntry(
                maker="shengshu",
                name=name,
                media_type="video",
                display_name=name,
                facts=VideoFacts(
                    durations=values,
                    resolutions=("720p", "1080p"),
                    duration_constraints=DurationConstraints(with_reference_images=values),
                    max_reference_images=7,
                    text_to_video=False,
                    first_frame=False,
                    last_frame=False,
                    reference_audio=reference_audio,
                    max_reference_audio_count=3 if reference_audio else None,
                ),
            )

        entries = (
            entry("viduq3-drama", range(2, 17), reference_audio=True),
            entry("viduq3-mix", range(3, 16), reference_audio=False),
            entry("viduq3-ad", range(3, 16), reference_audio=False),
        )
        models = tuple(
            BuiltinProviderModel(
                api_model_name=e.name,
                entry=f"shengshu/{e.name}",
                endpoint=(ep := f"vidu-video-{e.name.removeprefix('vidu')}"),
                min_app_version=endpoint_version_floor("vidu", "video", ep),
            )
            for e in entries
        )
        return CatalogData(
            dataset_version="2026-01-01T00:00:00Z",
            entries=entries,
            builtin_providers={"vidu": BuiltinProviderListing(models=models)},
        )

    @pytest.mark.parametrize(
        ("model", "endpoint", "audio_track", "reference_audio"),
        [
            ("viduq3-drama", "vidu-video-q3-drama", "controllable", 3),
            ("viduq3-mix", "vidu-video-q3-mix", "always_on", 0),
            ("viduq3-ad", "vidu-video-q3-ad", "always_off", 0),
        ],
    )
    def test_only_reference_to_video_is_open(
        self, monkeypatch: pytest.MonkeyPatch, model: str, endpoint: str, audio_track: str, reference_audio: int
    ):
        from lib.backends.backend_assembly.specs import builtin_video_capabilities_for_model
        from lib.backends.builtin_endpoints import builtin_model_endpoint
        from tests.catalog_support import install_catalog

        install_catalog(monkeypatch, self._catalog(), app_version="0.37.1")
        assert builtin_model_endpoint("vidu", "video", model) == endpoint
        caps = builtin_video_capabilities_for_model("vidu", model)
        assert (caps.text_to_video, caps.first_frame, caps.last_frame) == (False, False, False)
        assert caps.max_reference_images == 7
        assert caps.max_prompt_chars == 20000
        assert caps.audio_track == audio_track
        assert (caps.max_reference_audio_count or 0) == reference_audio

    def test_gated_out_before_endpoint_start_version(self, monkeypatch: pytest.MonkeyPatch):
        from tests.catalog_support import install_catalog

        install_catalog(monkeypatch, self._catalog(), app_version="0.37.0")
        assert catalog.list_models("vidu") == {}
        gated = {
            view.model_id: (view.min_app_version, view.min_app_version_satisfied)
            for view in catalog.version_gated_models("vidu")
        }
        assert gated == {
            "viduq3-drama": ("0.37.1", False),
            "viduq3-mix": ("0.37.1", False),
            "viduq3-ad": ("0.37.1", False),
        }

    @pytest.mark.parametrize("model", ["viduq3-drama", "viduq3-mix", "viduq3-ad"])
    def test_catalog_durations_stay_within_endpoint_table(self, monkeypatch: pytest.MonkeyPatch, model: str):
        """目录时长落在端点时长表内，_coerce_duration 不会把用户选的时长改成别的值。"""
        from tests.catalog_support import install_catalog

        install_catalog(monkeypatch, self._catalog(), app_version="0.37.1")
        info = catalog.get_model("vidu", model, media_type="video")
        assert info is not None
        table = set(_profile(model).endpoint.durations["/reference2video"])
        assert set(info.supported_durations) <= table
        assert set(info.reference_image_durations or []) <= table


async def test_unreadable_start_frame_prevents_generation(tmp_path):
    from arcreel_market_core.video_backend_contract import VideoCapabilityError
    from tests.http_capture import capture_http

    backend = _profile("viduq3-pro")(api_key="test", model="viduq3-pro")
    with capture_http() as router, pytest.raises(VideoCapabilityError) as caught:
        await backend.generate(
            VideoGenerationRequest(prompt="p", output_path=tmp_path / "out.mp4", start_image=tmp_path / "missing.png")
        )
    assert caught.value.code == "video_start_image_unreadable"
    assert caught.value.params["name"] == "missing.png"
    assert not router.calls
