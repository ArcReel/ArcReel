"""DashScopePixVerseVideoBackend 单元测试（respx 捕获出站请求，官方契约 + 计价同口径）。"""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest

from arcreel_catalog_core import CatalogEntry, Meter, PriceRow, PriceTable, VideoFacts
from arcreel_market_core.video_backend_contract import (
    ReferenceAudioMode,
    ResumeExpiredError,
    VideoAudioMode,
    VideoCapabilityError,
    VideoGenerationRequest,
)
from lib.backends.endpoint_prefill import infer_builtin_endpoint, infer_endpoint
from lib.backends.http_status_errors import AmbiguousGatewaySubmitError, AmbiguousSubmitError, ProviderRejectedError
from lib.backends.providers import PROVIDER_DASHSCOPE
from lib.backends.video_backends.dashscope import DashScopePixVerseVideoBackend
from lib.backends.video_capabilities import entry_video_facts, projected_video_capabilities, resolve_video_model
from lib.billing.metering import Priced, Unpriced, UnpricedReason, price_usage
from tests.factories import image_bytes
from tests.fakes import bounded_poll_clock, captured_provider_job_ids
from tests.http_capture import capture_http, only_request, request_json

_MAAS_HOST = "https://ws-bj.cn-beijing.maas.aliyuncs.com"
_SUBMIT_URL = f"{_MAAS_HOST}/api/v1/services/aigc/video-generation/video-synthesis"
_TASK_URL = f"{_MAAS_HOST}/api/v1/tasks/pix-task-1"
_VIDEO_URL = "https://media.pixverseai.cn/out.mp4"

_PIXVERSE_PRICE_TABLE = PriceTable(
    currency="CNY",
    meters=(
        Meter(
            meter="output_seconds",
            dims=("resolution", "audio"),
            rows=(
                PriceRow(when={"resolution": "360p", "audio": True}, price=0.27),
                PriceRow(when={"resolution": "540p", "audio": True}, price=0.33),
                PriceRow(when={"resolution": "720p", "audio": True}, price=0.44),
                PriceRow(when={"resolution": "1080p", "audio": True}, price=0.83),
                PriceRow(when={"resolution": "360p", "audio": False}, price=0.18),
                PriceRow(when={"resolution": "540p", "audio": False}, price=0.27),
                PriceRow(when={"resolution": "720p", "audio": False}, price=0.33),
                PriceRow(when={"resolution": "1080p", "audio": False}, price=0.65),
            ),
            defaults={"resolution": "720p", "audio": False},
        ),
    ),
)


def _pixverse_entry(*, text_to_video: bool, first_frame: bool, name: str) -> CatalogEntry:
    return CatalogEntry(
        maker="pixverse",
        name=name,
        media_type="video",
        display_name=name,
        facts=VideoFacts(
            durations=tuple(range(1, 16)),
            resolutions=("360p", "540p", "720p", "1080p"),
            max_reference_images=0,
            text_to_video=text_to_video,
            first_frame=first_frame,
            last_frame=False,
            reference_audio=False,
        ),
        price=_PIXVERSE_PRICE_TABLE,
    )


def _make_png(tmp_path: Path, name: str = "start.png") -> Path:
    path = tmp_path / name
    path.write_bytes(image_bytes())
    return path


def _submit_ok(task_id: str = "pix-task-1") -> dict:
    return {
        "output": {"task_status": "PENDING", "task_id": task_id},
        "request_id": "req-submit-1",
    }


def _poll_succeeded(*, duration: int = 5, audio: bool = True, resolution_sr: str = "720") -> dict:
    return {
        "request_id": "req-poll-1",
        "output": {
            "task_id": "pix-task-1",
            "task_status": "SUCCEEDED",
            "submit_time": "2026-11-28 10:34:41.630",
            "scheduled_time": "2026-11-28 10:34:41.655",
            "end_time": "2026-11-28 10:35:12.725",
            "orig_prompt": "赛博城市里的浣熊",
            "video_url": _VIDEO_URL,
        },
        "usage": {
            "duration": duration,
            "size": "1280*720",
            "fps": 24,
            "audio": audio,
            "SR": resolution_sr,
        },
    }


class TestCapabilitiesAndZeroModelBranching:
    @pytest.mark.parametrize("model", ["pixverse/pixverse-v6-max-t2v", "pixverse/pixverse-v6-max-it2v", "opaque-video"])
    def test_endpoint_declares_only_supported_modalities_regardless_of_model_name(self, model: str):
        caps = DashScopePixVerseVideoBackend.video_capabilities_for_model(model, None)
        assert caps.text_to_video is True
        assert caps.first_frame is True
        assert caps.last_frame is False
        assert caps.max_reference_images == 0
        assert caps.reference_audio_mode is ReferenceAudioMode.NONE
        assert caps.audio_track is VideoAudioMode.CONTROLLABLE
        assert caps.max_prompt_chars == 5000

    def test_t2v_and_it2v_modalities_narrowed_purely_by_catalog_entry(self):
        t2v_entry = _pixverse_entry(text_to_video=True, first_frame=False, name="pixverse-v6-max-t2v")
        it2v_entry = _pixverse_entry(text_to_video=False, first_frame=True, name="pixverse-v6-max-it2v")

        t2v_caps = projected_video_capabilities(
            resolve_video_model(
                DashScopePixVerseVideoBackend.describe_video_endpoint("pixverse/pixverse-v6-max-t2v", t2v_entry),
                entry_video_facts(t2v_entry),
                provider_id=PROVIDER_DASHSCOPE,
                model_id="pixverse/pixverse-v6-max-t2v",
                official=True,
            )
        )
        it2v_caps = projected_video_capabilities(
            resolve_video_model(
                DashScopePixVerseVideoBackend.describe_video_endpoint("pixverse/pixverse-v6-max-it2v", it2v_entry),
                entry_video_facts(it2v_entry),
                provider_id=PROVIDER_DASHSCOPE,
                model_id="pixverse/pixverse-v6-max-it2v",
                official=True,
            )
        )

        assert (t2v_caps.text_to_video, t2v_caps.first_frame, t2v_caps.last_frame, t2v_caps.max_reference_images) == (
            True,
            False,
            False,
            0,
        )
        assert (
            it2v_caps.text_to_video,
            it2v_caps.first_frame,
            it2v_caps.last_frame,
            it2v_caps.max_reference_images,
        ) == (
            False,
            True,
            False,
            0,
        )


class TestPixVerseRequestAndPollContract:
    @pytest.mark.parametrize("model", ["pixverse/pixverse-v6-max-t2v", "opaque-pixverse-alias"])
    @pytest.mark.parametrize("generate_audio", [True, False])
    async def test_t2v_submit_poll_and_download_contract(self, tmp_path: Path, model: str, generate_audio: bool):
        backend = DashScopePixVerseVideoBackend(
            api_key="sk-bj",
            model=model,
            base_url="https://dashscope.aliyuncs.com",
            wan3_base_url=_MAAS_HOST,
        )
        req = VideoGenerationRequest(
            prompt="下着雨的赛博城市",
            output_path=tmp_path / "out.mp4",
            resolution="720p",
            aspect_ratio="16:9",
            duration_seconds=8,
            generate_audio=generate_audio,
            seed=42,
            task_id="db-task-pix-t2v",
        )

        with capture_http() as router, bounded_poll_clock(), captured_provider_job_ids() as persisted:
            submit = router.post(_SUBMIT_URL).respond(200, json=_submit_ok())
            poll = router.get(_TASK_URL).mock(
                side_effect=[
                    httpx.Response(200, json={"output": {"task_id": "pix-task-1", "task_status": "RUNNING"}}),
                    httpx.Response(200, json=_poll_succeeded(duration=8, audio=generate_audio)),
                ]
            )
            router.get(_VIDEO_URL).respond(200, content=b"mp4-bytes")

            result = await backend.generate(req)

        sent = only_request(submit)
        assert sent.headers["Authorization"] == "Bearer sk-bj"
        assert sent.headers["X-DashScope-Async"] == "enable"
        body = request_json(sent)
        assert body == {
            "model": model,
            "input": {"prompt": "下着雨的赛博城市"},
            "parameters": {
                "resolution": "720P",
                "duration": 8,
                "watermark": False,
                "aspect_ratio": "16:9",
                "seed": 42,
                "audio": generate_audio,
            },
        }
        assert "ratio" not in body["parameters"]
        assert poll.call_count == 2
        assert result.video_path.read_bytes() == b"mp4-bytes"
        assert result.duration_seconds == 8
        assert result.generate_audio is generate_audio
        assert result.task_id == "pix-task-1"
        assert persisted == [
            {
                "task_id": "db-task-pix-t2v",
                "job_id": "pix-task-1",
                "provider": PROVIDER_DASHSCOPE,
                "endpoint": None,
                "base_url": f"{_MAAS_HOST}/api/v1",
            }
        ]

    async def test_it2v_sends_image_url_media_and_omits_aspect_ratio(self, tmp_path: Path):
        start_img = _make_png(tmp_path)
        backend = DashScopePixVerseVideoBackend(
            api_key="sk-bj",
            model="pixverse/pixverse-v6-max-it2v",
            wan3_base_url=f"{_MAAS_HOST}/api/v1/",
        )
        req = VideoGenerationRequest(
            prompt="镜头缓慢上移",
            output_path=tmp_path / "it2v.mp4",
            start_image=start_img,
            resolution="1080p",
            aspect_ratio="9:16",
            duration_seconds=15,
            generate_audio=True,
        )

        with capture_http() as router, bounded_poll_clock():
            submit = router.post(_SUBMIT_URL).respond(200, json=_submit_ok())
            router.get(_TASK_URL).respond(200, json=_poll_succeeded(duration=15, audio=True, resolution_sr="1080"))
            router.get(_VIDEO_URL).respond(200, content=b"mp4-it2v")

            result = await backend.generate(req)

        body = request_json(only_request(submit))
        assert body["input"]["media"][0]["type"] == "image_url"
        assert body["input"]["media"][0]["url"].startswith("data:image/png;base64,")
        assert body["parameters"] == {
            "resolution": "1080P",
            "duration": 15,
            "watermark": False,
            "audio": True,
        }
        assert "aspect_ratio" not in body["parameters"]
        assert "ratio" not in body["parameters"]
        assert result.duration_seconds == 15
        assert result.video_path.read_bytes() == b"mp4-it2v"

    def test_falls_back_to_base_url_when_wan3_base_url_unset(self):
        backend = DashScopePixVerseVideoBackend(
            api_key="sk-bj",
            model="pixverse/pixverse-v6-max-t2v",
            base_url=_MAAS_HOST,
        )
        assert backend._request_base_url == f"{_MAAS_HOST}/api/v1"


class TestValidationAndAdmission:
    @pytest.mark.parametrize("resolution", ["360p", "540P", "720p", "1080P"])
    def test_accepts_all_official_resolutions(self, tmp_path: Path, resolution: str):
        backend = DashScopePixVerseVideoBackend(api_key="sk", model="pixverse/pixverse-v6-max-t2v")
        payload = backend._build_payload(
            VideoGenerationRequest(
                prompt="p", output_path=tmp_path / "o.mp4", resolution=resolution, duration_seconds=1
            )
        )
        assert payload["parameters"]["resolution"] == resolution.upper()

    @pytest.mark.parametrize("bad_resolution", ["480p", "2K", "4K"])
    def test_rejects_unsupported_resolution_before_submit(self, tmp_path: Path, bad_resolution: str):
        backend = DashScopePixVerseVideoBackend(api_key="sk", model="pixverse/pixverse-v6-max-t2v")
        with pytest.raises(VideoCapabilityError) as exc:
            backend._build_payload(
                VideoGenerationRequest(prompt="p", output_path=tmp_path / "o.mp4", resolution=bad_resolution)
            )
        assert exc.value.code == "video_resolution_not_supported"

    @pytest.mark.parametrize("duration", [1, 5, 15])
    def test_accepts_1_to_15_second_durations(self, tmp_path: Path, duration: int):
        backend = DashScopePixVerseVideoBackend(api_key="sk", model="pixverse/pixverse-v6-max-t2v")
        payload = backend._build_payload(
            VideoGenerationRequest(prompt="p", output_path=tmp_path / "o.mp4", duration_seconds=duration)
        )
        assert payload["parameters"]["duration"] == duration

    @pytest.mark.parametrize("bad_duration", [0, 16, 30])
    def test_rejects_out_of_range_duration(self, tmp_path: Path, bad_duration: int):
        backend = DashScopePixVerseVideoBackend(api_key="sk", model="pixverse/pixverse-v6-max-t2v")
        with pytest.raises(VideoCapabilityError) as exc:
            backend._build_payload(
                VideoGenerationRequest(prompt="p", output_path=tmp_path / "o.mp4", duration_seconds=bad_duration)
            )
        assert exc.value.code == "video_duration_not_supported"

    @pytest.mark.parametrize("ratio", ["16:9", "4:3", "1:1", "3:4", "9:16", "3:2", "2:3", "21:9"])
    def test_accepts_all_official_t2v_aspect_ratios(self, tmp_path: Path, ratio: str):
        backend = DashScopePixVerseVideoBackend(api_key="sk", model="pixverse/pixverse-v6-max-t2v")
        payload = backend._build_payload(
            VideoGenerationRequest(prompt="p", output_path=tmp_path / "o.mp4", aspect_ratio=ratio)
        )
        assert payload["parameters"]["aspect_ratio"] == ratio

    def test_rejects_unsupported_t2v_aspect_ratio(self, tmp_path: Path):
        backend = DashScopePixVerseVideoBackend(api_key="sk", model="pixverse/pixverse-v6-max-t2v")
        with pytest.raises(VideoCapabilityError) as exc:
            backend._build_payload(
                VideoGenerationRequest(prompt="p", output_path=tmp_path / "o.mp4", aspect_ratio="1:4")
            )
        assert exc.value.code == "video_aspect_ratio_not_supported"

    def test_rejects_unsupported_modalities_last_frame_refs_and_audio(self, tmp_path: Path):
        backend = DashScopePixVerseVideoBackend(api_key="sk", model="pixverse/pixverse-v6-max-it2v")
        img = _make_png(tmp_path)

        with pytest.raises(VideoCapabilityError) as exc_last:
            backend._build_payload(
                VideoGenerationRequest(prompt="p", output_path=tmp_path / "o.mp4", start_image=img, end_image=img)
            )
        assert exc_last.value.code == "video_last_frame_unsupported"

        with pytest.raises(VideoCapabilityError) as exc_ref:
            backend._build_payload(
                VideoGenerationRequest(prompt="p", output_path=tmp_path / "o.mp4", reference_images=[img])
            )
        assert exc_ref.value.code == "video_reference_images_unsupported"

        voice = tmp_path / "v.wav"
        voice.write_bytes(b"RIFF")
        with pytest.raises(VideoCapabilityError) as exc_audio:
            backend._build_payload(
                VideoGenerationRequest(
                    prompt="p", output_path=tmp_path / "o.mp4", start_image=img, reference_audio_files=[voice]
                )
            )
        assert exc_audio.value.code == "video_reference_audio_unsupported"

    def test_rejects_t2v_when_effective_capabilities_disable_t2v(self, tmp_path: Path):
        from dataclasses import replace

        backend = DashScopePixVerseVideoBackend(api_key="sk", model="pixverse/pixverse-v6-max-it2v")
        it2v_caps = replace(backend.video_capabilities, text_to_video=False)
        with pytest.raises(VideoCapabilityError) as exc:
            backend._build_payload(
                VideoGenerationRequest(prompt="p", output_path=tmp_path / "o.mp4", video_capabilities=it2v_caps)
            )
        assert exc.value.code == "video_capability_missing_t2v"


class TestFailuresResumeAndRetryBoundary:
    @pytest.mark.parametrize("status_code", [502, 504])
    async def test_submit_502_and_504_never_retry_adr_0123(self, tmp_path: Path, status_code: int):
        backend = DashScopePixVerseVideoBackend(
            api_key="sk-bj", model="pixverse/pixverse-v6-max-t2v", wan3_base_url=_MAAS_HOST
        )
        req = VideoGenerationRequest(prompt="p", output_path=tmp_path / "o.mp4", duration_seconds=5)

        with capture_http() as router, bounded_poll_clock():
            submit = router.post(_SUBMIT_URL).respond(status_code, text="gateway error")
            with pytest.raises(AmbiguousGatewaySubmitError):
                await backend.generate(req)

        assert submit.call_count == 1

    async def test_submit_read_timeout_is_ambiguous_and_not_retried(self, tmp_path: Path):
        backend = DashScopePixVerseVideoBackend(
            api_key="sk-bj", model="pixverse/pixverse-v6-max-t2v", wan3_base_url=_MAAS_HOST
        )
        req = VideoGenerationRequest(prompt="p", output_path=tmp_path / "o.mp4", duration_seconds=5)

        with capture_http() as router, bounded_poll_clock():
            submit = router.post(_SUBMIT_URL).mock(side_effect=httpx.ReadTimeout("slow"))
            with pytest.raises(AmbiguousSubmitError):
                await backend.generate(req)

        assert submit.call_count == 1

    async def test_submit_400_fails_fast_without_retry(self, tmp_path: Path):
        backend = DashScopePixVerseVideoBackend(
            api_key="sk-bj", model="pixverse/pixverse-v6-max-t2v", wan3_base_url=_MAAS_HOST
        )
        req = VideoGenerationRequest(prompt="p", output_path=tmp_path / "o.mp4", duration_seconds=5)

        with capture_http() as router, bounded_poll_clock():
            submit = router.post(_SUBMIT_URL).respond(
                400, json={"code": "InvalidParameter", "message": "bad param", "request_id": "r1"}
            )
            with pytest.raises(ProviderRejectedError):
                await backend.generate(req)

        assert submit.call_count == 1

    async def test_poll_failed_surfaces_error(self, tmp_path: Path):
        backend = DashScopePixVerseVideoBackend(
            api_key="sk-bj", model="pixverse/pixverse-v6-max-t2v", wan3_base_url=_MAAS_HOST
        )
        req = VideoGenerationRequest(prompt="p", output_path=tmp_path / "o.mp4", duration_seconds=5)

        with capture_http() as router, bounded_poll_clock():
            router.post(_SUBMIT_URL).respond(200, json=_submit_ok())
            router.get(_TASK_URL).respond(
                200,
                json={
                    "request_id": "r-fail",
                    "output": {
                        "task_id": "pix-task-1",
                        "task_status": "FAILED",
                        "code": "InvalidParameter",
                        "message": "The resolution is invalid",
                    },
                },
            )
            with pytest.raises(RuntimeError, match="InvalidParameter"):
                await backend.generate(req)

    async def test_resume_polls_submitted_base_url_and_handles_expired(self, tmp_path: Path):
        backend = DashScopePixVerseVideoBackend(
            api_key="sk-bj",
            model="pixverse/pixverse-v6-max-t2v",
            wan3_base_url="https://ws-new.cn-beijing.maas.aliyuncs.com",
        )
        req = VideoGenerationRequest(
            prompt="p",
            output_path=tmp_path / "resumed.mp4",
            resolution="720p",
            duration_seconds=5,
            generate_audio=False,
            submitted_base_url=f"{_MAAS_HOST}/api/v1",
        )

        with capture_http() as router, bounded_poll_clock():
            poll = router.get(_TASK_URL).respond(200, json=_poll_succeeded(duration=5, audio=False))
            router.get(_VIDEO_URL).respond(200, content=b"resumed-mp4")
            result = await backend.resume_video("pix-task-1", req)

        assert poll.call_count == 1
        assert result.video_path.read_bytes() == b"resumed-mp4"
        assert result.generate_audio is False

        with capture_http() as router, bounded_poll_clock():
            router.get(_TASK_URL).respond(
                200,
                json={"request_id": "r-exp", "output": {"task_id": "pix-task-1", "task_status": "UNKNOWN"}},
            )
            with pytest.raises(ResumeExpiredError):
                await backend.resume_video("pix-task-1", req)


class TestPricingAndPrefillConsistency:
    @pytest.mark.parametrize(
        ("resolution", "audio", "unit_price"),
        [
            ("360p", True, 0.27),
            ("540p", True, 0.33),
            ("720p", True, 0.44),
            ("1080p", True, 0.83),
            ("360p", False, 0.18),
            ("540p", False, 0.27),
            ("720p", False, 0.33),
            ("1080p", False, 0.65),
        ],
    )
    async def test_estimate_and_settlement_match_official_pixverse_prices(
        self, tmp_path: Path, resolution: str, audio: bool, unit_price: float
    ):
        backend = DashScopePixVerseVideoBackend(
            api_key="sk-bj", model="pixverse/pixverse-v6-max-t2v", wan3_base_url=_MAAS_HOST
        )
        req = VideoGenerationRequest(
            prompt="p",
            output_path=tmp_path / "o.mp4",
            resolution=resolution,
            duration_seconds=5,
            generate_audio=audio,
        )

        est_usage = DashScopePixVerseVideoBackend.metered_usage(backend.model, req)
        est_cost = price_usage(_PIXVERSE_PRICE_TABLE, est_usage, multiplier=1.0, estimate=True)
        assert isinstance(est_cost, Priced)
        assert est_cost.currency == "CNY"
        assert est_cost.amount == pytest.approx(5 * unit_price)

        with capture_http() as router, bounded_poll_clock():
            router.post(_SUBMIT_URL).respond(200, json=_submit_ok())
            router.get(_TASK_URL).respond(200, json=_poll_succeeded(duration=5, audio=audio))
            router.get(_VIDEO_URL).respond(200, content=b"mp4")
            result = await backend.generate(req)

        settled_usage = DashScopePixVerseVideoBackend.metered_usage(backend.model, req, result)
        settled_cost = price_usage(_PIXVERSE_PRICE_TABLE, settled_usage, multiplier=1.0)
        assert settled_cost == est_cost

    def test_unsupported_resolution_is_unpriced(self, tmp_path: Path):
        req = VideoGenerationRequest(
            prompt="p", output_path=tmp_path / "o.mp4", resolution="4K", duration_seconds=5, generate_audio=True
        )
        usage = DashScopePixVerseVideoBackend.metered_usage("pixverse/pixverse-v6-max-t2v", req)
        assert price_usage(_PIXVERSE_PRICE_TABLE, usage, multiplier=1.0, estimate=True) == Unpriced(
            UnpricedReason.MISSING_RATE
        )

    @pytest.mark.parametrize("model_id", ["pixverse/pixverse-v6-max-t2v", "pixverse/pixverse-v6-max-it2v"])
    def test_prefill_maps_pixverse_models_to_pixverse_endpoint(self, model_id: str):
        assert infer_endpoint(model_id, "openai") == "dashscope-pixverse-video"
        assert infer_builtin_endpoint("dashscope", "video", model_id) == "dashscope-pixverse-video"
