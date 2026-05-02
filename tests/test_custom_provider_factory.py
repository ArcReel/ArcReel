"""create_custom_backend(provider, model_id, endpoint) 单元测试。"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from lib.custom_provider.backends import CustomImageBackend, CustomTextBackend, CustomVideoBackend
from lib.custom_provider.factory import create_custom_backend


def _make_provider(*, base_url: str = "https://api.example.com/v1", api_key: str = "sk-test") -> MagicMock:
    p = MagicMock()
    p.base_url = base_url
    p.api_key = api_key
    p.provider_id = "custom-42"
    return p


class TestEndpointDispatch:
    @patch("lib.custom_provider.endpoints.OpenAITextBackend")
    def test_openai_chat(self, mock_cls):
        provider = _make_provider()
        result = create_custom_backend(provider=provider, model_id="gpt-4o", endpoint="openai-chat")
        assert isinstance(result, CustomTextBackend)
        assert result.model == "gpt-4o"
        mock_cls.assert_called_once_with(api_key="sk-test", base_url="https://api.example.com/v1", model="gpt-4o")

    @patch("lib.custom_provider.endpoints.GeminiTextBackend")
    def test_gemini_generate(self, mock_cls):
        provider = _make_provider(base_url="https://generativelanguage.googleapis.com")
        create_custom_backend(provider=provider, model_id="gemini-2.5-flash", endpoint="gemini-generate")
        mock_cls.assert_called_once_with(
            api_key="sk-test",
            base_url="https://generativelanguage.googleapis.com/",
            model="gemini-2.5-flash",
        )

    @patch("lib.custom_provider.endpoints.OpenAIImageBackend")
    def test_openai_images(self, mock_cls):
        provider = _make_provider()
        result = create_custom_backend(provider=provider, model_id="dall-e-3", endpoint="openai-images")
        assert isinstance(result, CustomImageBackend)
        mock_cls.assert_called_once_with(api_key="sk-test", base_url="https://api.example.com/v1", model="dall-e-3")

    @patch("lib.custom_provider.endpoints.GeminiImageBackend")
    def test_gemini_image(self, mock_cls):
        provider = _make_provider(base_url="https://generativelanguage.googleapis.com")
        create_custom_backend(provider=provider, model_id="imagen-4", endpoint="gemini-image")
        mock_cls.assert_called_once_with(
            api_key="sk-test",
            base_url="https://generativelanguage.googleapis.com/",
            image_model="imagen-4",
        )

    @patch("lib.custom_provider.endpoints.OpenAIVideoBackend")
    def test_openai_video(self, mock_cls):
        provider = _make_provider()
        result = create_custom_backend(provider=provider, model_id="sora-2", endpoint="openai-video")
        assert isinstance(result, CustomVideoBackend)
        mock_cls.assert_called_once_with(api_key="sk-test", base_url="https://api.example.com/v1", model="sora-2")

    @patch("lib.custom_provider.endpoints.NewAPIVideoBackend")
    def test_newapi_video(self, mock_cls):
        provider = _make_provider()
        create_custom_backend(provider=provider, model_id="kling-v2", endpoint="newapi-video")
        mock_cls.assert_called_once_with(api_key="sk-test", base_url="https://api.example.com/v1", model="kling-v2")

    @patch("lib.custom_provider.endpoints.OpenAIImageBackend")
    def test_openai_images_generations(self, mock_cls):
        provider = _make_provider()
        result = create_custom_backend(provider=provider, model_id="dall-e-3", endpoint="openai-images-generations")
        assert isinstance(result, CustomImageBackend)
        mock_cls.assert_called_once_with(
            api_key="sk-test",
            base_url="https://api.example.com/v1",
            model="dall-e-3",
            mode="generations_only",
        )

    @patch("lib.custom_provider.endpoints.OpenAIImageBackend")
    def test_openai_images_edits(self, mock_cls):
        provider = _make_provider()
        result = create_custom_backend(provider=provider, model_id="dall-e-3", endpoint="openai-images-edits")
        assert isinstance(result, CustomImageBackend)
        mock_cls.assert_called_once_with(
            api_key="sk-test",
            base_url="https://api.example.com/v1",
            model="dall-e-3",
            mode="edits_only",
        )


class TestUrlNormalization:
    @patch("lib.custom_provider.endpoints.OpenAITextBackend")
    def test_openai_appends_v1(self, mock_cls):
        provider = _make_provider(base_url="https://api.example.com")
        create_custom_backend(provider=provider, model_id="gpt-4o", endpoint="openai-chat")
        mock_cls.assert_called_once_with(api_key="sk-test", base_url="https://api.example.com/v1", model="gpt-4o")

    @patch("lib.custom_provider.endpoints.GeminiTextBackend")
    def test_google_strips_v1beta(self, mock_cls):
        provider = _make_provider(base_url="https://generativelanguage.googleapis.com/v1beta")
        create_custom_backend(provider=provider, model_id="gemini-2.5", endpoint="gemini-generate")
        mock_cls.assert_called_once_with(
            api_key="sk-test",
            base_url="https://generativelanguage.googleapis.com/",
            model="gemini-2.5",
        )

    @patch("lib.custom_provider.endpoints.GeminiTextBackend")
    def test_google_empty_base_url(self, mock_cls):
        provider = _make_provider(base_url="")
        create_custom_backend(provider=provider, model_id="gemini-2.5", endpoint="gemini-generate")
        mock_cls.assert_called_once_with(api_key="sk-test", base_url=None, model="gemini-2.5")


class TestErrors:
    def test_unknown_endpoint(self):
        provider = _make_provider()
        with pytest.raises(ValueError, match="unknown endpoint"):
            create_custom_backend(provider=provider, model_id="claude-4", endpoint="anthropic-messages")
