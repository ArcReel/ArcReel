"""模型发现（discover_models / infer_endpoint smoke check）单元测试。"""

from __future__ import annotations

import asyncio
from unittest.mock import MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# infer_endpoint smoke check（主体已在 test_custom_provider_endpoints.py 覆盖）
# ---------------------------------------------------------------------------


class TestInferEndpointSmoke:
    """轻量 smoke check：确认 infer_endpoint 仍可从 endpoints 模块导入并返回合理值。"""

    def test_text_model(self):
        from lib.custom_provider.endpoints import infer_endpoint

        assert infer_endpoint("gpt-4o", "openai") == "openai-chat"

    def test_image_model(self):
        from lib.custom_provider.endpoints import infer_endpoint

        assert infer_endpoint("dall-e-3", "openai") == "openai-images"

    def test_video_model(self):
        from lib.custom_provider.endpoints import infer_endpoint

        assert infer_endpoint("kling-v2", "openai") == "openai-video"

    def test_google_text(self):
        from lib.custom_provider.endpoints import infer_endpoint

        assert infer_endpoint("gemini-3-flash", "google") == "gemini-generate"


# ---------------------------------------------------------------------------
# discover_models — OpenAI format
# ---------------------------------------------------------------------------


class TestDiscoverModelsOpenAI:
    @patch("lib.custom_provider.discovery.OpenAI")
    async def test_basic_discovery(self, mock_openai_cls):
        """基本模型发现流程，返回 endpoint 字段。"""
        mock_client = MagicMock()
        mock_openai_cls.return_value = mock_client

        model_a = MagicMock()
        model_a.id = "gpt-4o"
        model_b = MagicMock()
        model_b.id = "dall-e-3"
        model_c = MagicMock()
        model_c.id = "kling-v2"
        mock_client.models.list.return_value = [model_a, model_b, model_c]

        from lib.custom_provider.discovery import discover_models

        result = await discover_models(
            discovery_format="openai",
            base_url="https://api.example.com/v1",
            api_key="sk-test",
        )

        assert len(result) == 3
        # 按 id 排序
        ids = [m["model_id"] for m in result]
        assert ids == ["dall-e-3", "gpt-4o", "kling-v2"]
        # 每项都有 endpoint 字段
        for m in result:
            assert "endpoint" in m

    @patch("lib.custom_provider.discovery.OpenAI")
    async def test_default_marking(self, mock_openai_cls):
        """每种 media_type 的第一个模型（排序后）标为 is_default=True。"""
        mock_client = MagicMock()
        mock_openai_cls.return_value = mock_client

        model_a = MagicMock()
        model_a.id = "gpt-5.4"
        model_b = MagicMock()
        model_b.id = "gpt-5.4-mini"
        model_c = MagicMock()
        model_c.id = "dall-e-4"
        model_d = MagicMock()
        model_d.id = "gpt-image-1.5"
        mock_client.models.list.return_value = [model_a, model_b, model_c, model_d]

        from lib.custom_provider.discovery import discover_models

        result = await discover_models(
            discovery_format="openai",
            base_url="https://api.example.com/v1",
            api_key="sk-test",
        )

        # 按 id 排序: dall-e-4, gpt-5.4, gpt-5.4-mini, gpt-image-1.5
        # openai-images: dall-e-4 (default), gpt-image-1.5
        # openai-chat: gpt-5.4 (default), gpt-5.4-mini
        text_models = [m for m in result if m["endpoint"] == "openai-chat"]
        image_models = [m for m in result if m["endpoint"] == "openai-images"]

        assert text_models[0]["is_default"] is True
        assert text_models[1]["is_default"] is False
        assert image_models[0]["is_default"] is True
        assert image_models[1]["is_default"] is False

    @patch("lib.custom_provider.discovery.OpenAI")
    async def test_all_enabled(self, mock_openai_cls):
        """所有发现的模型都应标记为 is_enabled=True。"""
        mock_client = MagicMock()
        mock_openai_cls.return_value = mock_client

        model_a = MagicMock()
        model_a.id = "gpt-4o"
        mock_client.models.list.return_value = [model_a]

        from lib.custom_provider.discovery import discover_models

        result = await discover_models(
            discovery_format="openai",
            base_url="https://api.example.com/v1",
            api_key="sk-test",
        )

        assert all(m["is_enabled"] is True for m in result)

    @patch("lib.custom_provider.discovery.OpenAI")
    async def test_display_name_equals_model_id(self, mock_openai_cls):
        """display_name 应等于 model_id。"""
        mock_client = MagicMock()
        mock_openai_cls.return_value = mock_client

        model_a = MagicMock()
        model_a.id = "gpt-4o"
        mock_client.models.list.return_value = [model_a]

        from lib.custom_provider.discovery import discover_models

        result = await discover_models(
            discovery_format="openai",
            base_url="https://api.example.com/v1",
            api_key="sk-test",
        )

        assert result[0]["display_name"] == "gpt-4o"

    @patch("lib.custom_provider.discovery.OpenAI")
    async def test_api_unreachable(self, mock_openai_cls):
        """API 不可达时应抛出异常。"""
        mock_client = MagicMock()
        mock_openai_cls.return_value = mock_client
        mock_client.models.list.side_effect = Exception("Connection refused")

        from lib.custom_provider.discovery import discover_models

        with pytest.raises(Exception, match="Connection refused"):
            await discover_models(
                discovery_format="openai",
                base_url="https://unreachable.example.com/v1",
                api_key="sk-test",
            )

    @patch("lib.custom_provider.discovery.OpenAI")
    async def test_empty_model_list(self, mock_openai_cls):
        """返回空模型列表时结果为空。"""
        mock_client = MagicMock()
        mock_openai_cls.return_value = mock_client
        mock_client.models.list.return_value = []

        from lib.custom_provider.discovery import discover_models

        result = await discover_models(
            discovery_format="openai",
            base_url="https://api.example.com/v1",
            api_key="sk-test",
        )

        assert result == []


# ---------------------------------------------------------------------------
# discover_models — Google format
# ---------------------------------------------------------------------------


class TestDiscoverModelsGoogle:
    @patch("lib.custom_provider.discovery.genai")
    async def test_basic_discovery(self, mock_genai):
        """Google 格式基本模型发现。"""
        mock_client = MagicMock()
        mock_genai.Client.return_value = mock_client

        model_a = MagicMock()
        model_a.name = "models/gemini-3-flash"
        model_b = MagicMock()
        model_b.name = "models/veo-3"
        mock_client.models.list.return_value = [model_a, model_b]

        from lib.custom_provider.discovery import discover_models

        result = await discover_models(
            discovery_format="google",
            base_url="https://generativelanguage.googleapis.com/",
            api_key="test-key",
        )

        assert len(result) == 2
        for m in result:
            assert "endpoint" in m

    @patch("lib.custom_provider.discovery.genai")
    async def test_infer_from_model_id(self, mock_genai):
        """通过 model_id 关键字推断 endpoint。"""
        mock_client = MagicMock()
        mock_genai.Client.return_value = mock_client

        model_text = MagicMock()
        model_text.name = "models/gemini-3-flash"

        model_image = MagicMock()
        model_image.name = "models/gemini-3-flash-image-preview"

        model_video = MagicMock()
        model_video.name = "models/veo-3"

        mock_client.models.list.return_value = [model_text, model_image, model_video]

        from lib.custom_provider.discovery import discover_models

        result = await discover_models(
            discovery_format="google",
            base_url=None,
            api_key="test-key",
        )

        by_id = {m["model_id"]: m for m in result}
        assert by_id["gemini-3-flash"]["endpoint"] == "gemini-generate"
        assert by_id["gemini-3-flash-image-preview"]["endpoint"] == "gemini-image"
        assert by_id["veo-3"]["endpoint"] == "openai-video"

    @patch("lib.custom_provider.discovery.genai")
    async def test_default_marking_google(self, mock_genai):
        """Google 格式的 default 标记逻辑。"""
        mock_client = MagicMock()
        mock_genai.Client.return_value = mock_client

        model_a = MagicMock()
        model_a.name = "models/gemini-3-flash"
        model_b = MagicMock()
        model_b.name = "models/gemini-3-pro"
        mock_client.models.list.return_value = [model_a, model_b]

        from lib.custom_provider.discovery import discover_models

        result = await discover_models(
            discovery_format="google",
            base_url=None,
            api_key="test-key",
        )

        text_models = [m for m in result if m["endpoint"] == "gemini-generate"]
        assert text_models[0]["is_default"] is True
        assert text_models[1]["is_default"] is False

    @patch("lib.custom_provider.discovery.genai")
    async def test_strips_models_prefix(self, mock_genai):
        """Google API 返回的模型名带 'models/' 前缀，应去除。"""
        mock_client = MagicMock()
        mock_genai.Client.return_value = mock_client

        model = MagicMock()
        model.name = "models/gemini-3-flash"
        mock_client.models.list.return_value = [model]

        from lib.custom_provider.discovery import discover_models

        result = await discover_models(
            discovery_format="google",
            base_url=None,
            api_key="test-key",
        )

        assert result[0]["model_id"] == "gemini-3-flash"
        assert result[0]["display_name"] == "gemini-3-flash"

    @patch("lib.custom_provider.discovery.genai")
    async def test_no_base_url(self, mock_genai):
        """base_url 为 None 时不传 http_options。"""
        mock_client = MagicMock()
        mock_genai.Client.return_value = mock_client
        mock_client.models.list.return_value = []

        from lib.custom_provider.discovery import discover_models

        await discover_models(
            discovery_format="google",
            base_url=None,
            api_key="test-key",
        )

        mock_genai.Client.assert_called_once_with(api_key="test-key")

    @patch("lib.custom_provider.discovery.genai")
    async def test_with_base_url(self, mock_genai):
        """base_url 不为空时传 http_options。"""
        mock_client = MagicMock()
        mock_genai.Client.return_value = mock_client
        mock_client.models.list.return_value = []

        from lib.custom_provider.discovery import discover_models

        await discover_models(
            discovery_format="google",
            base_url="https://custom-endpoint.com/",
            api_key="test-key",
        )

        mock_genai.Client.assert_called_once_with(
            api_key="test-key",
            http_options={"base_url": "https://custom-endpoint.com/"},
        )


# ---------------------------------------------------------------------------
# Unknown format
# ---------------------------------------------------------------------------


class TestUnknownFormat:
    async def test_unknown_discovery_format(self):
        from lib.custom_provider.discovery import discover_models

        with pytest.raises(ValueError, match="discovery_format"):
            await discover_models(
                discovery_format="anthropic",
                base_url="https://api.example.com",
                api_key="sk-test",
            )


# ---------------------------------------------------------------------------
# Task spec 示例 fixture
# ---------------------------------------------------------------------------


def test_discover_openai_returns_endpoints(monkeypatch):
    """discover_models(discovery_format='openai', ...) 返回项含 endpoint 字段。"""
    from lib.custom_provider import discovery

    fake_models = [MagicMock(id="gpt-4o"), MagicMock(id="kling-v2"), MagicMock(id="dall-e-3")]
    fake_client = MagicMock()
    fake_client.models.list.return_value = fake_models
    monkeypatch.setattr(discovery, "OpenAI", lambda **kw: fake_client)

    result = asyncio.run(
        discovery.discover_models(
            discovery_format="openai",
            base_url="https://x",
            api_key="k",
        )
    )
    by_id = {m["model_id"]: m for m in result}
    assert by_id["gpt-4o"]["endpoint"] == "openai-chat"
    assert by_id["kling-v2"]["endpoint"] == "openai-video"
    assert by_id["dall-e-3"]["endpoint"] == "openai-images"
    # 每种 media_type 仅一个 default
    defaults = [m for m in result if m["is_default"]]
    assert {m["endpoint"] for m in defaults} == {"openai-chat", "openai-video", "openai-images"}
