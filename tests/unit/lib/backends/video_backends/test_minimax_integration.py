"""MiniMax 跨层集成测试：内置 provider 注册、文本记账 provider、目录计价、env keys。"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from lib.backends.providers import PROVIDER_MINIMAX, PROVIDER_OPENAI
from lib.catalog import query as catalog
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


class TestRegistry:
    def test_minimax_registered_with_all_media_types(self):
        from lib.config.registry import PROVIDER_REGISTRY

        meta = PROVIDER_REGISTRY[PROVIDER_MINIMAX]
        assert catalog.provider_media_types(PROVIDER_MINIMAX) == ["audio", "image", "text", "video"]
        assert "api_key" in meta.required_keys
        assert "api_key" in meta.secret_keys
        assert "base_url" in meta.optional_keys
        assert meta.default_base_url == "https://api.minimaxi.com/v1"
        models = catalog.list_models(PROVIDER_MINIMAX)
        assert models["MiniMax-M3"].default is True
        assert "vision" in models["MiniMax-M3"].capabilities
        assert models["MiniMax-M2.7"].default is False
        # image-01：默认图像模型，T2I + I2I
        image = models["image-01"]
        assert image.media_type == "image"
        assert image.default is True
        assert image.capabilities == ["text_to_image", "image_to_image"]

    def test_env_keys_registered(self):
        from lib.config.env_keys import OTHER_PROVIDER_ENV_KEYS, PROVIDER_SECRET_KEYS

        assert "MINIMAX_API_KEY" in OTHER_PROVIDER_ENV_KEYS
        assert "MINIMAX_API_KEY" in PROVIDER_SECRET_KEYS


class TestTextProviderBilling:
    """文本复用 OpenAI 后端必须以 'minimax' 记账，否则计费命中 USD。"""

    def test_provider_name_override(self):
        with captured_openai_clients():
            from lib.backends.text_backends.openai import OpenAITextBackend

            b = OpenAITextBackend(api_key="k", model="MiniMax-M2.7", provider_name=PROVIDER_MINIMAX)
            assert b.name == "minimax"

    async def test_result_provider_is_minimax(self):
        mock_client = AsyncMock()
        mock_client.chat.completions.create = AsyncMock(return_value=_text_response("hi"))
        with captured_openai_clients(mock_client):
            from lib.backends.text_backends.base import TextGenerationRequest
            from lib.backends.text_backends.openai import OpenAITextBackend

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
        import lib.backends.text_backends.registry as text_registry
        from lib.backends.text_backends import factory

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


class TestCatalogPricing:
    def test_text_cny_per_token(self):
        amount, cur = builtin_price(
            PROVIDER_MINIMAX,
            "MiniMax-M2.7",
            "text",
            {"uncached_input_tokens": 1_000_000, "output_tokens": 1_000_000},
        )
        assert cur == "CNY"
        # ¥2.1 入 + ¥8.4 出
        assert amount == pytest.approx(10.5)

    @pytest.mark.parametrize(
        ("model", "resolution", "duration", "expected"),
        [
            ("MiniMax-Hailuo-2.3", "768p", 6, 2.0),
            ("MiniMax-Hailuo-2.3", "768p", 10, 4.0),
            ("MiniMax-Hailuo-2.3", "1080p", 6, 3.5),
            ("MiniMax-Hailuo-2.3-Fast", "768p", 6, 1.35),
            ("MiniMax-Hailuo-2.3-Fast", "768p", 10, 2.25),
            ("MiniMax-Hailuo-2.3-Fast", "1080p", 6, 2.31),
        ],
    )
    def test_video_per_video_bucket(self, model, resolution, duration, expected):
        amount, cur = builtin_price(
            PROVIDER_MINIMAX, model, "video", {"requests": 1}, {"resolution": resolution, "duration": duration}
        )
        assert cur == "CNY"
        assert amount == pytest.approx(expected)

    @pytest.mark.parametrize(
        ("resolution", "duration", "expected"),
        [("768p", 4, 2.0), ("768p", 15, 7.5), ("2k", 5, 4.0), ("2k", 15, 12.0)],
    )
    def test_h3_per_second_by_resolution(self, resolution, duration, expected):
        # H3 按秒 × 分辨率计价（768P 0.50、2K 0.80 元/秒），两档都钉死。
        amount, cur = builtin_price(
            PROVIDER_MINIMAX, "MiniMax-H3", "video", {"output_seconds": duration}, {"resolution": resolution}
        )
        assert cur == "CNY"
        assert amount == pytest.approx(expected)


class TestVideoRegistry:
    def test_video_models_listed(self):
        models = catalog.list_models(PROVIDER_MINIMAX)
        h3 = models["MiniMax-H3"]
        assert h3.media_type == "video"
        # H3 是 minimax 视频默认，海螺 2.3 仍可选回。
        assert h3.default is True
        assert models["MiniMax-Hailuo-2.3"].default is False
        assert h3.supported_durations == list(range(4, 16))
        assert h3.resolutions == ["768p", "2k"]
        # 两档分辨率共用同一段时长，无需 duration_resolution_constraints 门控。
        assert h3.duration_resolution_constraints == {}

        hailuo = models["MiniMax-Hailuo-2.3"]
        assert hailuo.media_type == "video"
        assert hailuo.supported_durations == [6, 10]
        assert hailuo.duration_resolution_constraints == {"1080p": [6]}

        # 目录不声明视频输入模式，minimax 两个型号均无其他特性 token，故 capabilities 为空
        # ——t2v / i2v 判定读 backend。
        assert hailuo.capabilities == []
        assert models["MiniMax-Hailuo-2.3-Fast"].capabilities == []

    def test_video_backend_uses_declarative_provider_spec(self):
        from lib.backends.backend_assembly.specs import get_provider_spec

        assert get_provider_spec(PROVIDER_MINIMAX, "video").registry_backend == "declarative"


class TestProviderConstantsDistinct:
    def test_minimax_not_openai(self):
        assert PROVIDER_MINIMAX == "minimax"
        assert PROVIDER_MINIMAX != PROVIDER_OPENAI
