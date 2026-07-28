"""DashScope 跨层集成测试：文本记账 provider、定价查表、自定义 endpoint 派发、能力 fallthrough。"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from lib.pricing.lookup import lookup_pricing
from lib.pricing.strategies import PricingParams, calculate_pricing
from lib.providers import PROVIDER_DASHSCOPE, PROVIDER_OPENAI


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


class TestTextProviderBilling:
    """隐患 3 回归：dashscope 文本复用 OpenAI 后端必须以 'dashscope' 记账，否则计费命中 USD。"""

    def test_provider_name_override(self):
        with patch("lib.openai_shared.AsyncOpenAI"):
            from lib.text_backends.openai import OpenAITextBackend

            b = OpenAITextBackend(api_key="k", model="qwen-plus", provider_name="dashscope")
            assert b.name == "dashscope"

    async def test_result_provider_is_dashscope(self):
        mock_client = AsyncMock()
        mock_client.chat.completions.create = AsyncMock(return_value=_text_response("hi"))
        with patch("lib.openai_shared.AsyncOpenAI", return_value=mock_client):
            from lib.text_backends.base import TextGenerationRequest
            from lib.text_backends.openai import OpenAITextBackend

            b = OpenAITextBackend(api_key="k", model="qwen-plus", provider_name="dashscope")
            result = await b.generate(TextGenerationRequest(prompt="x"))
        assert result.provider == "dashscope"
        assert result.model == "qwen-plus"

    def test_default_provider_still_openai(self):
        with patch("lib.openai_shared.AsyncOpenAI"):
            from lib.text_backends.openai import OpenAITextBackend

            assert OpenAITextBackend(api_key="k").name == PROVIDER_OPENAI


class TestLookupPricing:
    def test_text_cny(self):
        p = lookup_pricing(PROVIDER_DASHSCOPE, "qwen-plus", "text")
        amount, cur = calculate_pricing(
            p, PricingParams(call_type="text", model="qwen-plus", input_tokens=1_000_000, output_tokens=1_000_000)
        )
        assert cur == "CNY"
        assert amount == pytest.approx(2.8)

    def test_image_cny(self):
        p = lookup_pricing(PROVIDER_DASHSCOPE, "qwen-image-2.0", "image")
        amount, cur = calculate_pricing(p, PricingParams(call_type="image", model="qwen-image-2.0", n=1))
        assert cur == "CNY"
        assert amount == pytest.approx(0.2)

    def test_video_resolution_matrix(self):
        p = lookup_pricing(PROVIDER_DASHSCOPE, "happyhorse-1.0-i2v", "video")
        a720, _ = calculate_pricing(
            p, PricingParams(call_type="video", model="happyhorse-1.0-i2v", resolution="720p", duration_seconds=5)
        )
        a1080, _ = calculate_pricing(
            p, PricingParams(call_type="video", model="happyhorse-1.0-i2v", resolution="1080p", duration_seconds=5)
        )
        assert a720 == pytest.approx(4.5)
        assert a1080 == pytest.approx(8.0)

    def test_wan_video_cny(self):
        p = lookup_pricing(PROVIDER_DASHSCOPE, "wan2.7-r2v", "video")
        amount, cur = calculate_pricing(
            p, PricingParams(call_type="video", model="wan2.7-r2v", resolution="1080p", duration_seconds=10)
        )
        assert cur == "CNY"
        assert amount == pytest.approx(10.0)

    def test_unknown_model_falls_back_to_dashscope_cny(self):
        # 隐患 2 配套：未知 dashscope model 回落自身默认 CNY，而非 Gemini USD
        p = lookup_pricing(PROVIDER_DASHSCOPE, "qwen-unknown-xyz", "text")
        _, cur = calculate_pricing(
            p, PricingParams(call_type="text", model="qwen-unknown-xyz", input_tokens=1000, output_tokens=0)
        )
        assert cur == "CNY"


class TestCustomEndpointDispatch:
    @staticmethod
    def _provider(base_url: str = "https://dashscope.aliyuncs.com") -> MagicMock:
        p = MagicMock()
        p.base_url = base_url
        p.api_key = "sk-test"
        p.provider_id = "custom-9"
        return p

    @patch("lib.custom_provider.endpoints.DashScopeImageBackend")
    def test_dashscope_image(self, mock_cls):
        from lib.custom_provider.backends import CustomImageBackend
        from lib.custom_provider.factory import create_custom_backend

        result = create_custom_backend(provider=self._provider(), model_id="qwen-image-2.0", endpoint="dashscope-image")
        assert isinstance(result, CustomImageBackend)
        # builder 透传原始 base_url，由 backend 内部派生 /api/v1（不重复归一化）
        mock_cls.assert_called_once_with(
            api_key="sk-test", base_url="https://dashscope.aliyuncs.com", model="qwen-image-2.0"
        )

    @patch("lib.custom_provider.endpoints.DashScopeVideoBackend")
    def test_dashscope_async_video(self, mock_cls):
        from lib.custom_provider.backends import CustomVideoBackend
        from lib.custom_provider.factory import create_custom_backend

        result = create_custom_backend(
            provider=self._provider(), model_id="happyhorse-1.0-r2v", endpoint="dashscope-async-video"
        )
        assert isinstance(result, CustomVideoBackend)
        # builder 透传原始 base_url，由 backend 内部派生 /api/v1（不重复归一化）
        mock_cls.assert_called_once_with(
            api_key="sk-test", base_url="https://dashscope.aliyuncs.com", model="happyhorse-1.0-r2v"
        )


class TestEndpointFallthrough:
    """endpoint 不声明 video_max_reference_images → resolver fallthrough 到 backend caps（#677）。"""

    def test_async_video_endpoint_declares_none(self):
        from lib.custom_provider.endpoints import get_endpoint_spec

        assert get_endpoint_spec("dashscope-async-video").video_max_reference_images is None

    def test_backend_caps_supply_real_limit(self):
        from lib.video_backends.dashscope import DashScopeVideoBackend

        assert (
            DashScopeVideoBackend(api_key="sk", model="happyhorse-1.0-r2v").video_capabilities.max_reference_images == 9
        )
        assert DashScopeVideoBackend(api_key="sk", model="wan2.7-r2v").video_capabilities.max_reference_images == 5


class TestInferEndpoint:
    @pytest.mark.parametrize(
        "model,expected",
        [
            ("happyhorse-1.0-r2v", "dashscope-async-video"),
            ("wan2.7-i2v", "dashscope-async-video"),
            ("wan2.7-image", "openai-images"),
            ("qwen-image-2.0", "openai-images"),
            ("doubao-seedance-1.5-pro", "ark-seedance"),
            ("sora-2", "openai-video"),
            ("gpt-5.4", "openai-chat"),
        ],
    )
    def test_routing(self, model, expected):
        from lib.custom_provider.endpoints import infer_endpoint

        assert infer_endpoint(model, "openai") == expected
