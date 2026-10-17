"""视频调用通道按用量契约上报的计量（docs/adr/0100）：计量项、选择维度与方舟视频 token 估算。"""

from __future__ import annotations

from pathlib import Path

import pytest

from arcreel_market_core.video_backend_contract import VideoGenerationRequest, VideoGenerationResult
from lib.backends.backend_assembly.specs import builtin_video_metered_usage
from lib.backends.video_backends.ark import ArkVideoBackend
from lib.backends.video_backends.kling import KlingVideoBackend
from lib.backends.video_backends.metering import video_usage
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
    @pytest.mark.parametrize(
        ("model", "resolution", "ratio", "seconds", "tokens"),
        [
            ("doubao-seedance-2-0-260128", "720p", "16:9", 5, 5 * 1280 * 720 * 24 / 1024),
            ("doubao-seedance-2-0-260128", "1080p", "9:16", 8, 8 * 1080 * 1920 * 24 / 1024),
            ("doubao-seedance-2-0-260128", "4k", "16:9", 5, 5 * 3840 * 2160 * 24 / 1024),
            # 不发送分辨率时方舟按 720p 生成
            ("doubao-seedance-2-0-fast-260128", None, "1:1", 4, 4 * 960 * 960 * 24 / 1024),
            # 2.5 的 480p 横竖屏像素与 2.0 不同
            ("doubao-seedance-2-5-260628", "480p", "16:9", 5, 5 * 854 * 480 * 24 / 1024),
            ("doubao-seedance-2-5-260628", "480p", "9:16", 5, 5 * 480 * 854 * 24 / 1024),
        ],
    )
    def test_official_formula(
        self, model: str, resolution: str | None, ratio: str, seconds: int, tokens: float
    ) -> None:
        assert ArkVideoBackend.estimate_video_tokens(
            model, resolution=resolution, ratio=ratio, seconds=seconds
        ) == pytest.approx(tokens)

    @pytest.mark.parametrize(
        ("model", "resolution", "ratio"),
        [
            # 宽高比交给上游自适应，像素数不可知
            ("doubao-seedance-2-0-260128", "720p", "adaptive"),
            # 2.5 没有 4k 档
            ("doubao-seedance-2-5-260628", "4k", "16:9"),
            # 不在像素表里的型号
            ("doubao-seedance-1-0-pro-250528", "720p", "16:9"),
        ],
    )
    def test_no_estimate_when_pixels_are_unknown(self, model: str, resolution: str, ratio: str) -> None:
        assert ArkVideoBackend.estimate_video_tokens(model, resolution=resolution, ratio=ratio, seconds=5) is None

    def test_settlement_takes_reported_tokens_and_never_the_estimate(self) -> None:
        request = _request(duration_seconds=5, resolution="720p")
        model = "doubao-seedance-2-0-260128"

        assert (
            ArkVideoBackend.metered_usage(model, request, _result(usage_tokens=99_000)).quantities["video_tokens"]
            == 99_000
        )
        assert "video_tokens" not in ArkVideoBackend.metered_usage(model, request, _result()).quantities

    def test_service_tier_reported_only_where_the_request_carries_it(self) -> None:
        request = _request(duration_seconds=5, resolution="720p", service_tier="default")

        seedance_2 = ArkVideoBackend.metered_usage("doubao-seedance-2-0-260128", request)
        seedance_1 = ArkVideoBackend.metered_usage("doubao-seedance-1-0-pro-250528", request)

        assert "service_tier" not in seedance_2.dimensions
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


class TestViduMetering:
    def test_reference_to_video_is_its_own_price_tier(self) -> None:
        references = [Path(f"ref-{index}.png") for index in range(9)]
        reference = ViduVideoBackend.metered_usage(
            "viduq3-turbo", _request(duration_seconds=5, resolution="720p", reference_images=references)
        )
        first_frame = ViduVideoBackend.metered_usage(
            "viduq3-turbo", _request(duration_seconds=5, resolution="720p", start_image=Path("first.png"))
        )

        assert reference.dimensions["reference_to_video"] is True
        # Vidu 参考生视频最多发 7 张，多余的截掉不发
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
