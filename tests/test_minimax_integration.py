"""MiniMax 跨层集成测试：内置 provider 注册、文本记账 provider、定价查表、env keys。"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from lib.pricing.lookup import lookup_pricing
from lib.pricing.strategies import PricingParams, calculate_pricing
from lib.providers import PROVIDER_MINIMAX, PROVIDER_OPENAI


def _text_response(content: str = "ok", in_tok: int = 10, out_tok: int = 5) -> MagicMock:
    usage = MagicMock()
    usage.prompt_tokens = in_tok
    usage.completion_tokens = out_tok
    message = MagicMock()
    message.content = content
    choice = MagicMock()
    choice.message = message
    choice.finish_reason = "stop"
    response = MagicMock()
    response.choices = [choice]
    response.usage = usage
    return response


class TestRegistry:
    def test_minimax_registered_with_text_image_and_video(self):
        from lib.config.registry import PROVIDER_REGISTRY

        meta = PROVIDER_REGISTRY[PROVIDER_MINIMAX]
        assert meta.media_types == ["image", "text", "video"]
        assert "api_key" in meta.required_keys
        assert "api_key" in meta.secret_keys
        assert "base_url" in meta.optional_keys
        assert meta.default_base_url == "https://api.minimaxi.com/v1"
        assert "MiniMax-M3" in meta.models
        assert meta.models["MiniMax-M3"].default is True
        assert "MiniMax-M2.7" in meta.models
        assert meta.models["MiniMax-M2.7"].default is False
        # image-01：默认图像模型，T2I + I2I，单脸参考
        image = meta.models["image-01"]
        assert image.media_type == "image"
        assert image.default is True
        assert image.capabilities == ["text_to_image", "image_to_image"]
        assert image.max_reference_images == 1

    def test_env_keys_registered(self):
        from lib.config.env_keys import OTHER_PROVIDER_ENV_KEYS, PROVIDER_SECRET_KEYS

        assert "MINIMAX_API_KEY" in OTHER_PROVIDER_ENV_KEYS
        assert "MINIMAX_API_KEY" in PROVIDER_SECRET_KEYS


class TestTextProviderBilling:
    """文本复用 OpenAI 后端必须以 'minimax' 记账，否则计费命中 USD。"""

    def test_provider_name_override(self):
        with patch("lib.openai_shared.AsyncOpenAI"):
            from lib.text_backends.openai import OpenAITextBackend

            b = OpenAITextBackend(api_key="k", model="MiniMax-M2.7", provider_name=PROVIDER_MINIMAX)
            assert b.name == "minimax"

    async def test_result_provider_is_minimax(self):
        mock_client = AsyncMock()
        mock_client.chat.completions.create = AsyncMock(return_value=_text_response("hi"))
        with patch("lib.openai_shared.AsyncOpenAI", return_value=mock_client):
            from lib.text_backends.base import TextGenerationRequest
            from lib.text_backends.openai import OpenAITextBackend

            b = OpenAITextBackend(api_key="k", model="MiniMax-M2.7", provider_name=PROVIDER_MINIMAX)
            result = await b.generate(TextGenerationRequest(prompt="x"))
        assert result.provider == "minimax"
        assert result.model == "MiniMax-M2.7"
        # token 数须透传，否则「实际」费用汇总按 0 token 算不出 MiniMax CNY 费率
        assert result.input_tokens == 10
        assert result.output_tokens == 5


class TestFactoryWiring:
    async def test_text_factory_uses_openai_backend_with_minimax_provider(self):
        """provider=minimax 经 text 工厂 → assemble_backend → OpenAI 后端，base_url 派生 /v1，provider_name 透传。"""
        import lib.text_backends.registry as text_registry
        from lib.text_backends import factory

        resolver = MagicMock()
        session_cm = MagicMock()
        session_cm.text_backend_for_task = AsyncMock(return_value=(PROVIDER_MINIMAX, "MiniMax-M2.7"))
        session_cm.provider_config = AsyncMock(return_value={"api_key": "sk-mm", "base_url": None})
        resolver.session.return_value.__aenter__ = AsyncMock(return_value=session_cm)
        resolver.session.return_value.__aexit__ = AsyncMock(return_value=False)

        captured: dict = {}

        def _fake_create_backend(backend_name: str, **kwargs):
            captured["backend_name"] = backend_name
            captured.update(kwargs)
            return MagicMock()

        with (
            patch.object(factory, "ConfigResolver", return_value=resolver),
            patch.object(text_registry, "create_backend", side_effect=_fake_create_backend),
        ):
            await factory.create_text_backend_for_task("script")

        assert captured["backend_name"] == "openai"
        assert captured["provider_name"] == PROVIDER_MINIMAX
        assert captured["base_url"] == "https://api.minimaxi.com/v1"
        assert captured["model"] == "MiniMax-M2.7"


class TestLookupPricing:
    def test_text_cny_per_token(self):
        p = lookup_pricing(PROVIDER_MINIMAX, "MiniMax-M2.7", "text")
        amount, cur = calculate_pricing(
            p,
            PricingParams(call_type="text", model="MiniMax-M2.7", input_tokens=1_000_000, output_tokens=1_000_000),
        )
        assert cur == "CNY"
        # ¥2.1 入 + ¥8.4 出
        assert amount == pytest.approx(10.5)

    def test_unknown_model_falls_back_to_minimax_cny(self):
        # 未知 minimax model 回落自身默认 CNY，而非 Gemini USD
        p = lookup_pricing(PROVIDER_MINIMAX, "minimax-unknown-xyz", "text")
        _, cur = calculate_pricing(
            p, PricingParams(call_type="text", model="minimax-unknown-xyz", input_tokens=1000, output_tokens=0)
        )
        assert cur == "CNY"

    @pytest.mark.parametrize(
        ("model", "resolution", "duration", "expected"),
        [
            ("MiniMax-Hailuo-2.3", "768p", 6, 2.0),
            ("MiniMax-Hailuo-2.3", "768p", 10, 4.0),
            ("MiniMax-Hailuo-2.3", "1080p", 6, 3.5),
            ("MiniMax-Hailuo-2.3-Fast", "768p", 6, 1.35),
            ("MiniMax-Hailuo-2.3-Fast", "768p", 10, 2.25),
            ("MiniMax-Hailuo-2.3-Fast", "1080p", 6, 2.31),
            # S2V-01 单档定价（约 ¥3，半核实）：固定 768P/6s 档命中。
            ("S2V-01", "768p", 6, 3.0),
        ],
    )
    def test_video_per_video_bucket(self, model, resolution, duration, expected):
        p = lookup_pricing(PROVIDER_MINIMAX, model, "video")
        amount, cur = calculate_pricing(
            p, PricingParams(call_type="video", model=model, resolution=resolution, duration_seconds=duration)
        )
        assert cur == "CNY"
        assert amount == pytest.approx(expected)

    def test_video_unmet_bucket_falls_back_nearest_cny(self):
        # 1080p 仅 6s 档；请求 1080p 10s 未命中 → 同分辨率最近档 (1080p,6)=3.5
        p = lookup_pricing(PROVIDER_MINIMAX, "MiniMax-Hailuo-2.3", "video")
        amount, cur = calculate_pricing(
            p, PricingParams(call_type="video", model="MiniMax-Hailuo-2.3", resolution="1080p", duration_seconds=10)
        )
        assert cur == "CNY"
        assert amount == pytest.approx(3.5)

    def test_s2v01_single_bucket_resolves_for_any_resolution(self):
        # S2V-01 仅单档，固定输出；任意分辨率经最近档回落到唯一档，价格恒为 ¥3。
        p = lookup_pricing(PROVIDER_MINIMAX, "S2V-01", "video")
        amount, cur = calculate_pricing(
            p, PricingParams(call_type="video", model="S2V-01", resolution="1080p", duration_seconds=6)
        )
        assert cur == "CNY"
        assert amount == pytest.approx(3.0)


class TestVideoRegistry:
    def test_video_models_registered(self):
        from lib.config.registry import PROVIDER_REGISTRY

        models = PROVIDER_REGISTRY[PROVIDER_MINIMAX].models
        hailuo = models["MiniMax-Hailuo-2.3"]
        assert hailuo.media_type == "video"
        assert "text_to_video" in hailuo.capabilities
        assert "image_to_video" in hailuo.capabilities
        assert hailuo.supported_durations == [6, 10]
        assert hailuo.duration_resolution_constraints == {"1080p": [6]}

        fast = models["MiniMax-Hailuo-2.3-Fast"]
        assert fast.capabilities == ["image_to_video"]

    def test_s2v01_registered_with_single_reference_cap(self):
        from lib.config.registry import PROVIDER_REGISTRY

        s2v = PROVIDER_REGISTRY[PROVIDER_MINIMAX].models["S2V-01"]
        assert s2v.media_type == "video"
        # 单脸参考生视频：编排层据 registry max_reference_images 只取 1 张。
        assert s2v.max_reference_images == 1
        # 固定 6s 输出。
        assert s2v.supported_durations == [6]

    def test_video_backend_registered(self):
        from lib.video_backends import get_registered_backends

        assert PROVIDER_MINIMAX in get_registered_backends()


class TestProviderConstantsDistinct:
    def test_minimax_not_openai(self):
        assert PROVIDER_MINIMAX == "minimax"
        assert PROVIDER_MINIMAX != PROVIDER_OPENAI
