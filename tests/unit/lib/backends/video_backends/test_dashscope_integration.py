"""DashScope 跨层集成测试：文本记账 provider、目录计价、自定义 endpoint 派发、能力 fallthrough。"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from lib.backends.providers import PROVIDER_DASHSCOPE, PROVIDER_OPENAI
from tests.catalog_support import builtin_price
from tests.fakes import captured_openai_clients


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
        with captured_openai_clients():
            from lib.backends.text_backends.openai import OpenAITextBackend

            b = OpenAITextBackend(api_key="k", model="qwen-plus", provider_name="dashscope")
            assert b.name == "dashscope"

    async def test_result_provider_is_dashscope(self):
        mock_client = AsyncMock()
        mock_client.chat.completions.create = AsyncMock(return_value=_text_response("hi"))
        with captured_openai_clients(mock_client):
            from lib.backends.text_backends.base import TextGenerationRequest
            from lib.backends.text_backends.openai import OpenAITextBackend

            b = OpenAITextBackend(api_key="k", model="qwen-plus", provider_name="dashscope")
            result = await b.generate(TextGenerationRequest(prompt="x"))
        assert result.provider == "dashscope"
        assert result.model == "qwen-plus"

    def test_default_provider_still_openai(self):
        with captured_openai_clients():
            from lib.backends.text_backends.openai import OpenAITextBackend

            assert OpenAITextBackend(api_key="k").name == PROVIDER_OPENAI


class TestCatalogPricing:
    def test_text_cny(self):
        # qwen-plus 按单次请求的输入量分档，10 万输入落在首档：输入 0.8、输出 2 元/百万 token。
        amount, cur = builtin_price(
            PROVIDER_DASHSCOPE,
            "qwen-plus",
            "text",
            {"uncached_input_tokens": 100_000, "output_tokens": 100_000},
        )
        assert cur == "CNY"
        assert amount == pytest.approx(0.28)

    def test_image_cny(self):
        amount, cur = builtin_price(PROVIDER_DASHSCOPE, "qwen-image-2.0", "image", {"output_images": 1})
        assert cur == "CNY"
        assert amount == pytest.approx(0.2)

    def test_video_resolution_matrix(self):
        a720, _ = builtin_price(
            PROVIDER_DASHSCOPE, "happyhorse-1.0-i2v", "video", {"output_seconds": 5}, {"resolution": "720p"}
        )
        a1080, _ = builtin_price(
            PROVIDER_DASHSCOPE, "happyhorse-1.0-i2v", "video", {"output_seconds": 5}, {"resolution": "1080p"}
        )
        assert a720 == pytest.approx(4.5)
        assert a1080 == pytest.approx(8.0)

    @pytest.mark.parametrize("model", ["happyhorse-1.1-t2v", "happyhorse-1.1-i2v", "happyhorse-1.1-r2v"])
    def test_happyhorse_11_pricing_covers_three_resolutions(self, model: str):
        """三档分辨率都要有价：任一档缺价会让该档的调用记为未定价。"""
        for resolution, expected in (("480p", 4.5), ("720p", 9.0), ("1080p", 12.0)):
            amount, cur = builtin_price(
                PROVIDER_DASHSCOPE, model, "video", {"output_seconds": 10}, {"resolution": resolution}
            )
            assert cur == "CNY"
            assert amount == pytest.approx(expected)

    def test_wan_video_cny(self):
        amount, cur = builtin_price(
            PROVIDER_DASHSCOPE, "wan2.7-r2v", "video", {"output_seconds": 10}, {"resolution": "1080p"}
        )
        assert cur == "CNY"
        assert amount == pytest.approx(10.0)


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
            api_key="sk-test", base_url="https://dashscope.aliyuncs.com", model="qwen-image-2.0", transport_layer=None
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
            api_key="sk-test",
            base_url="https://dashscope.aliyuncs.com",
            model="happyhorse-1.0-r2v",
            transport_layer=None,
        )


class TestEndpointFallthrough:
    """endpoint 不声明 video_max_reference_images → resolver fallthrough 到 backend caps（#677）。"""

    def test_async_video_endpoint_declares_none(self):
        from lib.custom_provider.endpoints import get_endpoint_spec

        assert get_endpoint_spec("dashscope-async-video").video_max_reference_images is None

    def test_backend_caps_supply_real_limit(self):
        from lib.backends.video_backends.dashscope import DashScopeVideoBackend

        assert (
            DashScopeVideoBackend(api_key="sk", model="happyhorse-1.0-r2v").video_capabilities.max_reference_images == 9
        )
        assert DashScopeVideoBackend(api_key="sk", model="wan2.7-r2v").video_capabilities.max_reference_images == 5


class TestInferEndpoint:
    @pytest.mark.parametrize(
        ("model", "expected"),
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
