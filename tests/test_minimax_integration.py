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
    def test_minimax_registered_with_text_and_image(self):
        from lib.config.registry import PROVIDER_REGISTRY

        meta = PROVIDER_REGISTRY[PROVIDER_MINIMAX]
        assert meta.media_types == ["image", "text"]
        assert "api_key" in meta.required_keys
        assert "api_key" in meta.secret_keys
        assert "base_url" in meta.optional_keys
        assert meta.default_base_url == "https://api.minimaxi.com/v1"
        assert "MiniMax-M2.7" in meta.models
        assert meta.models["MiniMax-M2.7"].default is True
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
        """provider=minimax 经 text 工厂 → OpenAI 后端，base_url 派生 /v1，provider_name 透传。"""
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
            patch.object(factory, "create_backend", side_effect=_fake_create_backend),
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


class TestProviderConstantsDistinct:
    def test_minimax_not_openai(self):
        assert PROVIDER_MINIMAX == "minimax"
        assert PROVIDER_MINIMAX != PROVIDER_OPENAI
