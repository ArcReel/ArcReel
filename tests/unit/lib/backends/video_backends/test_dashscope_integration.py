"""DashScope cross-layer text attribution/catalog pricing/custom-endpoint dispatch/capability fallthrough."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

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
    """Risk 3 regression: DashScope text reusing OpenAI backends must account as dashscope, never USD pricing."""

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
        # qwen-plus tiers per-request input; 100000 uses first-tier input 0.8/output 2 CNY per million.
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
        """All three resolutions need prices; omissions make those calls unpriced."""
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

    def test_dashscope_async_video(self):
        from lib.custom_provider.backends import CustomVideoBackend
        from lib.custom_provider.factory import create_custom_backend

        result = create_custom_backend(
            provider=self._provider(), model_id="happyhorse-1.0-r2v", endpoint="dashscope-reference-video"
        )
        assert isinstance(result, CustomVideoBackend)
        assert result.model == "happyhorse-1.0-r2v"
        assert result._delegate.video_capabilities.max_reference_images is None


class TestEndpointFallthrough:
    """Endpoints omitting video_max_reference_images fall through to backend caps (#677)."""

    def test_async_video_endpoint_declares_none(self):
        from lib.custom_provider.endpoints import get_endpoint_spec

        assert get_endpoint_spec("dashscope-video").video_max_reference_images is None

    def test_entry_supplies_real_limit(self):
        """Channels declare unlimited references; custom-provider name-matched entries supply actual joint-evaluated caps (docs/adr/0104)."""
        from lib.custom_provider.capabilities import two_sided_video_capabilities

        def limit(model: str) -> int | None:
            return two_sided_video_capabilities(endpoint="dashscope-video", model_id=model).max_reference_images

        assert limit("happyhorse-1.0-r2v") == 9
        assert limit("wan2.7-r2v") == 5


class TestInferEndpoint:
    @pytest.mark.parametrize(
        ("model", "expected"),
        [
            ("happyhorse-1.0-r2v", "dashscope-reference-video"),
            ("wan2.7-i2v", "dashscope-always-on-video"),
            ("wan2.7-image", "openai-images"),
            ("qwen-image-2.0", "openai-images"),
            ("doubao-seedance-1.5-pro", "ark-seedance-1"),
            ("sora-2", "openai-video"),
            ("gpt-5.4", "openai-chat"),
        ],
    )
    def test_routing(self, model, expected):
        from lib.custom_provider.endpoints import infer_endpoint

        assert infer_endpoint(model, "openai") == expected
