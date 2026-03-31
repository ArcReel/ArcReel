"""模型发现（infer_media_type / discover_models）单元测试。"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from lib.custom_provider.discovery import discover_models, infer_media_type

# ---------------------------------------------------------------------------
# infer_media_type
# ---------------------------------------------------------------------------


class TestInferMediaType:
    """关键字推断 media_type。"""

    @pytest.mark.parametrize(
        "model_id, expected",
        [
            # image 关键字
            ("dall-e-4", "image"),
            ("DALL-E-3", "image"),
            ("gpt-image-1.5", "image"),
            ("flux-img-v2", "image"),
            ("stable-image-core", "image"),
            # video 关键字
            ("sora-2", "video"),
            ("kling-v2-master", "video"),
            ("wan-2.1-pro", "video"),
            ("seedance-1.0", "video"),
            ("cogvideox", "video"),
            ("mochi-preview", "video"),
            ("veo-3", "video"),
            ("pika-2.2", "video"),
            ("Video-Generator", "video"),
            # text (默认)
            ("gpt-5.4", "text"),
            ("gpt-5.4-mini", "text"),
            ("gemini-3-flash", "text"),
            ("deepseek-v3", "text"),
            ("qwen3-32b", "text"),
            ("claude-4-sonnet", "text"),
        ],
    )
    def test_keyword_matching(self, model_id: str, expected: str):
        assert infer_media_type(model_id) == expected

    def test_case_insensitive(self):
        assert infer_media_type("DALL-E-4") == "image"
        assert infer_media_type("Sora-2") == "video"
        assert infer_media_type("GPT-5.4") == "text"


# ---------------------------------------------------------------------------
# discover_models — OpenAI format
# ---------------------------------------------------------------------------


class TestDiscoverModelsOpenAI:
    @patch("lib.custom_provider.discovery.OpenAI")
    async def test_basic_discovery(self, mock_openai_cls):
        """基本模型发现流程。"""
        mock_client = MagicMock()
        mock_openai_cls.return_value = mock_client

        # 模拟 models.list() 返回
        model_a = MagicMock()
        model_a.id = "gpt-5.4"
        model_b = MagicMock()
        model_b.id = "dall-e-4"
        model_c = MagicMock()
        model_c.id = "sora-2"
        mock_client.models.list.return_value = [model_a, model_b, model_c]

        result = await discover_models("openai", "https://api.example.com/v1", "sk-test")

        assert len(result) == 3
        # 按 id 排序
        ids = [m["model_id"] for m in result]
        assert ids == ["dall-e-4", "gpt-5.4", "sora-2"]

        mock_openai_cls.assert_called_once_with(api_key="sk-test", base_url="https://api.example.com/v1")

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

        result = await discover_models("openai", "https://api.example.com/v1", "sk-test")

        # 按 id 排序: dall-e-4, gpt-5.4, gpt-5.4-mini, gpt-image-1.5
        # text: gpt-5.4 (default), gpt-5.4-mini
        # image: dall-e-4 (default), gpt-image-1.5
        text_models = [m for m in result if m["media_type"] == "text"]
        image_models = [m for m in result if m["media_type"] == "image"]

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
        model_a.id = "gpt-5.4"
        mock_client.models.list.return_value = [model_a]

        result = await discover_models("openai", "https://api.example.com/v1", "sk-test")

        assert all(m["is_enabled"] is True for m in result)

    @patch("lib.custom_provider.discovery.OpenAI")
    async def test_display_name_equals_model_id(self, mock_openai_cls):
        """display_name 应等于 model_id。"""
        mock_client = MagicMock()
        mock_openai_cls.return_value = mock_client

        model_a = MagicMock()
        model_a.id = "gpt-5.4"
        mock_client.models.list.return_value = [model_a]

        result = await discover_models("openai", "https://api.example.com/v1", "sk-test")

        assert result[0]["display_name"] == "gpt-5.4"

    @patch("lib.custom_provider.discovery.OpenAI")
    async def test_api_unreachable(self, mock_openai_cls):
        """API 不可达时应抛出异常。"""
        mock_client = MagicMock()
        mock_openai_cls.return_value = mock_client
        mock_client.models.list.side_effect = Exception("Connection refused")

        with pytest.raises(Exception, match="Connection refused"):
            await discover_models("openai", "https://unreachable.example.com/v1", "sk-test")

    @patch("lib.custom_provider.discovery.OpenAI")
    async def test_empty_model_list(self, mock_openai_cls):
        """返回空模型列表时结果为空。"""
        mock_client = MagicMock()
        mock_openai_cls.return_value = mock_client
        mock_client.models.list.return_value = []

        result = await discover_models("openai", "https://api.example.com/v1", "sk-test")

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
        model_a.supported_generation_methods = ["generateContent"]
        model_b = MagicMock()
        model_b.name = "models/veo-3"
        model_b.supported_generation_methods = ["generateVideo"]
        mock_client.models.list.return_value = [model_a, model_b]

        result = await discover_models("google", "https://generativelanguage.googleapis.com/", "test-key")

        assert len(result) == 2

    @patch("lib.custom_provider.discovery.genai")
    async def test_infer_from_generation_methods(self, mock_genai):
        """从 supported_generation_methods 推断 media_type。"""
        mock_client = MagicMock()
        mock_genai.Client.return_value = mock_client

        model_text = MagicMock()
        model_text.name = "models/gemini-3-flash"
        model_text.supported_generation_methods = ["generateContent"]

        model_image = MagicMock()
        model_image.name = "models/gemini-3-flash-image-preview"
        model_image.supported_generation_methods = ["generateContent", "generateImages"]

        model_video = MagicMock()
        model_video.name = "models/veo-3"
        model_video.supported_generation_methods = ["generateVideo"]

        mock_client.models.list.return_value = [model_text, model_image, model_video]

        result = await discover_models("google", None, "test-key")

        by_id = {m["model_id"]: m for m in result}
        assert by_id["gemini-3-flash"]["media_type"] == "text"
        assert by_id["gemini-3-flash-image-preview"]["media_type"] == "image"
        assert by_id["veo-3"]["media_type"] == "video"

    @patch("lib.custom_provider.discovery.genai")
    async def test_fallback_to_keyword_matching(self, mock_genai):
        """当 supported_generation_methods 不可用时，回退到关键字推断。"""
        mock_client = MagicMock()
        mock_genai.Client.return_value = mock_client

        model = MagicMock()
        model.name = "models/dall-e-custom"
        model.supported_generation_methods = None
        mock_client.models.list.return_value = [model]

        result = await discover_models("google", None, "test-key")

        assert result[0]["media_type"] == "image"

    @patch("lib.custom_provider.discovery.genai")
    async def test_default_marking_google(self, mock_genai):
        """Google 格式的 default 标记逻辑。"""
        mock_client = MagicMock()
        mock_genai.Client.return_value = mock_client

        model_a = MagicMock()
        model_a.name = "models/gemini-3-flash"
        model_a.supported_generation_methods = ["generateContent"]
        model_b = MagicMock()
        model_b.name = "models/gemini-3-pro"
        model_b.supported_generation_methods = ["generateContent"]
        mock_client.models.list.return_value = [model_a, model_b]

        result = await discover_models("google", None, "test-key")

        text_models = [m for m in result if m["media_type"] == "text"]
        assert text_models[0]["is_default"] is True
        assert text_models[1]["is_default"] is False

    @patch("lib.custom_provider.discovery.genai")
    async def test_strips_models_prefix(self, mock_genai):
        """Google API 返回的模型名带 'models/' 前缀，应去除。"""
        mock_client = MagicMock()
        mock_genai.Client.return_value = mock_client

        model = MagicMock()
        model.name = "models/gemini-3-flash"
        model.supported_generation_methods = ["generateContent"]
        mock_client.models.list.return_value = [model]

        result = await discover_models("google", None, "test-key")

        assert result[0]["model_id"] == "gemini-3-flash"
        assert result[0]["display_name"] == "gemini-3-flash"

    @patch("lib.custom_provider.discovery.genai")
    async def test_no_base_url(self, mock_genai):
        """base_url 为 None 时不传 http_options。"""
        mock_client = MagicMock()
        mock_genai.Client.return_value = mock_client
        mock_client.models.list.return_value = []

        await discover_models("google", None, "test-key")

        mock_genai.Client.assert_called_once_with(api_key="test-key")

    @patch("lib.custom_provider.discovery.genai")
    async def test_with_base_url(self, mock_genai):
        """base_url 不为空时传 http_options。"""
        mock_client = MagicMock()
        mock_genai.Client.return_value = mock_client
        mock_client.models.list.return_value = []

        await discover_models("google", "https://custom-endpoint.com/", "test-key")

        mock_genai.Client.assert_called_once_with(
            api_key="test-key",
            http_options={"base_url": "https://custom-endpoint.com/"},
        )


# ---------------------------------------------------------------------------
# Unknown format
# ---------------------------------------------------------------------------


class TestUnknownFormat:
    async def test_unknown_api_format(self):
        with pytest.raises(ValueError, match="api_format"):
            await discover_models("anthropic", "https://api.example.com", "sk-test")
