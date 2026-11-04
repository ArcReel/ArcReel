"""视频调用通道按用量契约上报的计量（docs/adr/0100）：计量项、选择维度与方舟视频 token 估算。"""

from __future__ import annotations

from pathlib import Path

import pytest

from arcreel_market_core.video_backend_contract import VideoGenerationRequest, VideoGenerationResult
from lib.backends.backend_assembly.specs import builtin_video_metered_usage
from lib.backends.video_backends.agnes import AgnesVideoBackend
from lib.backends.video_backends.ark import (
    ArkVideoBackend,
    Seedance1VideoBackend,
    Seedance2VideoBackend,
    Seedance25VideoBackend,
)
from lib.backends.video_backends.dashscope import DashScopeVideoBackend
from lib.backends.video_backends.kling import KlingVideoBackend
from lib.backends.video_backends.metering import video_usage
from lib.backends.video_backends.openai import OpenAIVideoBackend
from lib.backends.video_backends.vidu import ViduVideoBackend
from lib.billing.catalog_pricing import price_builtin_call
from lib.billing.metering import Priced, Unpriced, UnpricedReason
from lib.catalog import query as catalog
from lib.db.repositories.usage_repo import MAX_BILLED_DURATION_SECONDS


def _request(**fields: object) -> VideoGenerationRequest:
    base: dict[str, object] = {"prompt": "p", "output_path": Path("out.mp4"), "aspect_ratio": "16:9"}
    base.update(fields)
    return VideoGenerationRequest(**base)


def _result(**fields: object) -> VideoGenerationResult:
    base: dict[str, object] = {"video_path": Path("out.mp4"), "provider": "p", "model": "m", "duration_seconds": 8}
    base.update(fields)
    return VideoGenerationResult(**base)


class TestVideoUsage:
    def test_estimate_reports_sent_fields(self) -> None:
        usage = video_usage(
            _request(duration_seconds=6, reference_images=[Path("a.png"), Path("b.png")]),
            None,
            seconds=6,
            resolution="1080p",
            audio=True,
        )

        assert usage.quantities == {"output_seconds": 6, "input_images": 2, "input_video_seconds": 0, "requests": 1}
        assert usage.dimensions == {
            "duration": 6,
            "input_video": False,
            "reference_to_video": True,
            "resolution": "1080p",
            "audio": True,
        }

    def test_unsent_dimensions_are_left_to_table_defaults(self) -> None:
        usage = video_usage(_request(duration_seconds=8), None, seconds=8, resolution=None, audio=None)

        assert "resolution" not in usage.dimensions
        assert "audio" not in usage.dimensions

    @pytest.mark.parametrize(
        ("reported", "billed"),
        [(5, 5), (0, 8), (MAX_BILLED_DURATION_SECONDS + 1, 8)],
        ids=["reported", "non-positive", "over-limit"],
    )
    def test_settlement_takes_reported_seconds_within_bounds(self, reported: int, billed: int) -> None:
        usage = video_usage(
            _request(duration_seconds=8), _result(duration_seconds=reported), seconds=8, resolution="720p", audio=True
        )

        assert usage.quantities["output_seconds"] == billed
        # 时长档按发出的请求取，不随回报的秒数变
        assert usage.dimensions["duration"] == 8

    def test_settlement_takes_reported_audio_flag(self) -> None:
        usage = video_usage(
            _request(duration_seconds=8), _result(generate_audio=False), seconds=8, resolution="720p", audio=True
        )

        assert usage.dimensions["audio"] is False


class TestArkVideoTokens:
    """视频 token 估算与服务档随 Seedance 端点声明，与型号名无关。"""

    @pytest.mark.parametrize(
        ("backend", "resolution", "ratio", "seconds", "tokens"),
        [
            (Seedance2VideoBackend, "720p", "16:9", 5, 5 * 1280 * 720 * 24 / 1024),
            (Seedance2VideoBackend, "1080p", "9:16", 8, 8 * 1080 * 1920 * 24 / 1024),
            (Seedance2VideoBackend, "4k", "16:9", 5, 5 * 3840 * 2160 * 24 / 1024),
            # 不发送分辨率时方舟按 720p 生成
            (Seedance2VideoBackend, None, "1:1", 4, 4 * 960 * 960 * 24 / 1024),
            # 2.5 的 480p 横竖屏像素与 2.0 不同
            (Seedance25VideoBackend, "480p", "16:9", 5, 5 * 854 * 480 * 24 / 1024),
            (Seedance25VideoBackend, "480p", "9:16", 5, 5 * 480 * 854 * 24 / 1024),
        ],
    )
    def test_official_formula(
        self, backend: type[ArkVideoBackend], resolution: str | None, ratio: str, seconds: int, tokens: float
    ) -> None:
        assert backend.estimate_video_tokens(resolution=resolution, ratio=ratio, seconds=seconds) == pytest.approx(
            tokens
        )

    @pytest.mark.parametrize(
        ("backend", "resolution", "ratio"),
        [
            # 宽高比交给上游自适应，像素数不可知
            (Seedance2VideoBackend, "720p", "adaptive"),
            # 2.5 没有 4k 档
            (Seedance25VideoBackend, "4k", "16:9"),
            # 1.x 端点不按视频 token 计量
            (Seedance1VideoBackend, "720p", "16:9"),
        ],
    )
    def test_no_estimate_when_pixels_are_unknown(
        self, backend: type[ArkVideoBackend], resolution: str, ratio: str
    ) -> None:
        assert backend.estimate_video_tokens(resolution=resolution, ratio=ratio, seconds=5) is None

    def test_settlement_takes_reported_tokens_and_never_the_estimate(self) -> None:
        request = _request(duration_seconds=5, resolution="720p")
        model = "doubao-seedance-2-0-260128"

        assert (
            Seedance2VideoBackend.metered_usage(model, request, _result(usage_tokens=99_000)).quantities["video_tokens"]
            == 99_000
        )
        assert "video_tokens" not in Seedance2VideoBackend.metered_usage(model, request, _result()).quantities

    def test_service_tier_reported_only_where_the_request_carries_it(self) -> None:
        request = _request(duration_seconds=5, resolution="720p", service_tier="default")

        seedance_2 = Seedance2VideoBackend.metered_usage("doubao-seedance-2-0-260128", request)
        seedance_2_5 = Seedance25VideoBackend.metered_usage("doubao-seedance-2-5-260628", request)
        seedance_1 = Seedance1VideoBackend.metered_usage("doubao-seedance-1-0-pro-250528", request)

        assert "service_tier" not in seedance_2.dimensions
        assert "service_tier" not in seedance_2_5.dimensions
        assert seedance_1.dimensions["service_tier"] == "standard"

    def test_estimate_without_tokens_is_unpriced(self) -> None:
        usage = builtin_video_metered_usage(
            "ark",
            "doubao-seedance-2-0-260128",
            _request(duration_seconds=5, resolution="720p", aspect_ratio="adaptive"),
        )

        cost = price_builtin_call(
            provider="ark",
            model="doubao-seedance-2-0-260128",
            media_type="video",
            requests=(usage,),
            provider_multiplier=1.0,
            estimate=True,
        )
        assert cost == Unpriced(UnpricedReason.NO_USAGE_ESTIMATE)


class TestSeedanceSelectableResolutions:
    """方舟《创建视频生成任务 API》：Seedance 2.0 可选 480p/720p/1080p/4k，2.5 可选 480p/720p/1080p。"""

    @pytest.mark.parametrize(
        ("provider", "model", "resolutions"),
        [
            ("ark", "doubao-seedance-2-0-260128", ["480p", "720p", "1080p", "4k"]),
            ("ark", "doubao-seedance-2-5-260628", ["480p", "720p", "1080p"]),
            # agent plan 共用同一条目，套餐未纳入 2.5
            ("ark-agent-plan", "doubao-seedance-2.0", ["480p", "720p", "1080p", "4k"]),
        ],
    )
    def test_every_selectable_resolution_is_priced(self, provider: str, model: str, resolutions: list[str]) -> None:
        catalog_model = catalog.get_model(provider, model)
        assert catalog_model is not None
        assert catalog_model.resolutions == resolutions

        for resolution in resolutions:
            usage = builtin_video_metered_usage(
                provider, model, _request(duration_seconds=5, resolution=resolution, aspect_ratio="16:9")
            )
            cost = price_builtin_call(
                provider=provider,
                model=model,
                media_type="video",
                requests=(usage,),
                provider_multiplier=1.0,
                estimate=True,
            )
            assert isinstance(cost, Priced), f"{provider}/{model} 的 {resolution} 算不出价格"
            assert cost.amount > 0


class TestKlingMetering:
    @pytest.mark.parametrize(
        ("resolution", "service_tier", "dimension"),
        [(None, "default", "720p"), (None, "pro", "1080p"), ("4k", "pro", "4k")],
    )
    def test_resolution_dimension_follows_mode(self, resolution: str | None, service_tier: str, dimension: str) -> None:
        usage = KlingVideoBackend.metered_usage(
            "kling-v3", _request(duration_seconds=5, resolution=resolution, service_tier=service_tier)
        )

        assert usage.dimensions["resolution"] == dimension

    def test_estimate_follows_user_relaxed_audio_track(self) -> None:
        """用户添加的型号把音轨放宽为可控：预估与执行同一份叠加覆盖后的能力，按有声计。"""
        catalog.install_user_added_models(
            [
                catalog.UserAddedModel(
                    "kling", "kling-user-added", "video", capability_overrides={"audio_track": "controllable"}
                )
            ]
        )
        try:
            usage = builtin_video_metered_usage(
                "kling", "kling-user-added", _request(duration_seconds=5, generate_audio=True)
            )
        finally:
            catalog.install_user_added_models(())

        assert usage.dimensions["audio"] is True


class TestViduMetering:
    def test_reference_to_video_is_its_own_price_tier(self) -> None:
        references = [Path(f"ref-{index}.png") for index in range(7)]
        reference = ViduVideoBackend.metered_usage(
            "viduq3-turbo", _request(duration_seconds=5, resolution="720p", reference_images=references)
        )
        first_frame = ViduVideoBackend.metered_usage(
            "viduq3-turbo", _request(duration_seconds=5, resolution="720p", start_image=Path("first.png"))
        )

        assert reference.dimensions["reference_to_video"] is True
        assert reference.quantities["input_images"] == 7
        assert first_frame.dimensions["reference_to_video"] is False
        assert first_frame.quantities["input_images"] == 0

    @pytest.mark.parametrize(
        ("fields", "per_second"),
        [
            ({"start_image": Path("first.png")}, 0.375),
            ({"reference_images": [Path("ref.png")]}, 0.3125),
        ],
        ids=["image-to-video", "reference-to-video"],
    )
    def test_reference_to_video_priced_by_catalog(self, fields: dict[str, object], per_second: float) -> None:
        usage = ViduVideoBackend.metered_usage(
            "viduq3-turbo", _request(duration_seconds=5, resolution="720p", **fields)
        )

        cost = price_builtin_call(
            provider="vidu",
            model="viduq3-turbo",
            media_type="video",
            requests=(usage,),
            provider_multiplier=1.0,
            estimate=True,
        )
        assert isinstance(cost, Priced)
        assert (cost.amount, cost.currency) == (pytest.approx(5 * per_second), "CNY")

    def test_audio_only_reported_true_for_q3_models(self) -> None:
        request = _request(duration_seconds=4, resolution="720p", generate_audio=True)

        assert ViduVideoBackend.metered_usage("viduq3-turbo", request).dimensions["audio"] is True
        assert ViduVideoBackend.metered_usage("vidu2.0", request).dimensions["audio"] is False


class TestEntryBoundMetering:
    """不透明 API 模型名带上对应的条目时，各调用通道的计量与同条目的官方模型相同（docs/adr/0105）。

    覆盖按模型名派生计量的全部读点：方舟各端点的视频 token 与服务档（与条目无关，同样相等）、可灵的有声约束、Vidu 的时长校正与音轨、Agnes 的
    契约有声、OpenAI 的 ``size``、百炼的音轨与参考图上限。
    """

    _OPAQUE = "relay-opaque-video"

    @pytest.mark.parametrize(
        ("backend", "official"),
        [
            (Seedance2VideoBackend, "doubao-seedance-2-0-260128"),
            (Seedance25VideoBackend, "doubao-seedance-2-5-260628"),
            (KlingVideoBackend, "kling-v2-6"),
            (KlingVideoBackend, "kling-v3"),
            (ViduVideoBackend, "viduq3-turbo"),
            (ViduVideoBackend, "vidu2.0"),
            (AgnesVideoBackend, "agnes-video-2.5"),
            (OpenAIVideoBackend, "sora-2-pro"),
            (DashScopeVideoBackend, "wan3.0-video"),
            (DashScopeVideoBackend, "wan2.7-r2v"),
        ],
    )
    @pytest.mark.parametrize(
        "fields",
        [
            {"duration_seconds": 5, "resolution": "720p", "generate_audio": True},
            {"duration_seconds": 10, "resolution": "1080p", "generate_audio": False, "aspect_ratio": "9:16"},
            {"duration_seconds": 5, "resolution": "480p", "start_image": Path("first.png"), "service_tier": "pro"},
            {"duration_seconds": 8, "reference_images": [Path("a.png"), Path("b.png")], "generate_audio": True},
        ],
    )
    def test_opaque_model_with_entry_meters_like_the_official_model(
        self, backend: type, official: str, fields: dict[str, object]
    ) -> None:
        entry = catalog.find_entry(official, media_type="video")
        assert entry is not None
        request = _request(**fields)

        assert backend.metered_usage(self._OPAQUE, request, entry=entry) == backend.metered_usage(official, request)
        settled = _result(duration_seconds=7, usage_tokens=50_000)
        assert backend.metered_usage(self._OPAQUE, request, settled, entry=entry) == backend.metered_usage(
            official, request, settled
        )

    @pytest.mark.parametrize(("official", "generates_audio"), [("kling-v3", True), ("kling-v2-6", False)])
    def test_kling_default_tier_audio_follows_the_entry(self, official: str, generates_audio: bool) -> None:
        entry = catalog.find_entry(official, media_type="video")

        assert KlingVideoBackend.effective_generate_audio_for_model(self._OPAQUE) is False
        assert KlingVideoBackend.effective_generate_audio_for_model(self._OPAQUE, entry) is generates_audio
