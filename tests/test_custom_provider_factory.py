"""create_custom_backend 工厂函数单元测试。"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from lib.custom_provider.backends import CustomImageBackend, CustomTextBackend, CustomVideoBackend
from lib.custom_provider.factory import create_custom_backend


def _make_provider(
    *, api_format: str = "openai", base_url: str = "https://api.example.com/v1", api_key: str = "sk-test"
) -> MagicMock:
    """创建模拟的 CustomProvider 对象。"""
    provider = MagicMock()
    provider.api_format = api_format
    provider.base_url = base_url
    provider.api_key = api_key
    provider.provider_id = "custom-42"
    return provider


# ---------------------------------------------------------------------------
# OpenAI format
# ---------------------------------------------------------------------------


class TestOpenAIFormat:
    @patch("lib.custom_provider.factory.OpenAITextBackend")
    def test_text_backend(self, mock_cls):
        provider = _make_provider(api_format="openai")
        result = create_custom_backend(provider=provider, model_id="gpt-5.4", media_type="text")

        assert isinstance(result, CustomTextBackend)
        assert result.name == "custom-42"
        assert result.model == "gpt-5.4"
        mock_cls.assert_called_once_with(api_key="sk-test", base_url="https://api.example.com/v1", model="gpt-5.4")

    @patch("lib.custom_provider.factory.OpenAIImageBackend")
    def test_image_backend(self, mock_cls):
        provider = _make_provider(api_format="openai")
        result = create_custom_backend(provider=provider, model_id="dall-e-4", media_type="image")

        assert isinstance(result, CustomImageBackend)
        assert result.name == "custom-42"
        assert result.model == "dall-e-4"
        mock_cls.assert_called_once_with(api_key="sk-test", base_url="https://api.example.com/v1", model="dall-e-4")

    @patch("lib.custom_provider.factory.OpenAIVideoBackend")
    def test_video_backend(self, mock_cls):
        provider = _make_provider(api_format="openai")
        result = create_custom_backend(provider=provider, model_id="sora-2", media_type="video")

        assert isinstance(result, CustomVideoBackend)
        assert result.name == "custom-42"
        assert result.model == "sora-2"
        mock_cls.assert_called_once_with(api_key="sk-test", base_url="https://api.example.com/v1", model="sora-2")


# ---------------------------------------------------------------------------
# Google format
# ---------------------------------------------------------------------------


class TestGoogleFormat:
    @patch("lib.custom_provider.factory.GeminiTextBackend")
    def test_text_backend(self, mock_cls):
        provider = _make_provider(api_format="google", base_url="https://generativelanguage.googleapis.com")
        result = create_custom_backend(provider=provider, model_id="gemini-3-flash", media_type="text")

        assert isinstance(result, CustomTextBackend)
        assert result.name == "custom-42"
        assert result.model == "gemini-3-flash"
        # base_url 经过 normalize_base_url 处理后会加 /
        mock_cls.assert_called_once_with(
            api_key="sk-test",
            base_url="https://generativelanguage.googleapis.com/",
            model="gemini-3-flash",
        )

    @patch("lib.custom_provider.factory.GeminiImageBackend")
    def test_image_backend(self, mock_cls):
        provider = _make_provider(api_format="google", base_url="https://generativelanguage.googleapis.com")
        result = create_custom_backend(provider=provider, model_id="gemini-3-flash-image", media_type="image")

        assert isinstance(result, CustomImageBackend)
        assert result.name == "custom-42"
        assert result.model == "gemini-3-flash-image"
        mock_cls.assert_called_once_with(
            api_key="sk-test",
            base_url="https://generativelanguage.googleapis.com/",
            image_model="gemini-3-flash-image",
        )

    @patch("lib.custom_provider.factory.GeminiVideoBackend")
    def test_video_backend(self, mock_cls):
        provider = _make_provider(api_format="google", base_url="https://generativelanguage.googleapis.com")
        result = create_custom_backend(provider=provider, model_id="veo-3", media_type="video")

        assert isinstance(result, CustomVideoBackend)
        assert result.name == "custom-42"
        assert result.model == "veo-3"
        mock_cls.assert_called_once_with(
            api_key="sk-test",
            base_url="https://generativelanguage.googleapis.com/",
            video_model="veo-3",
            use_content_api=True,
        )

    @patch("lib.custom_provider.factory.GeminiTextBackend")
    def test_empty_base_url_passes_none(self, mock_cls):
        """当 base_url 经 ensure_google 后为空时，传 None 给 backend。"""
        provider = _make_provider(api_format="google", base_url="")
        result = create_custom_backend(provider=provider, model_id="gemini-3-flash", media_type="text")

        assert isinstance(result, CustomTextBackend)
        mock_cls.assert_called_once_with(api_key="sk-test", base_url=None, model="gemini-3-flash")

    @patch("lib.custom_provider.factory.GeminiTextBackend")
    def test_strips_v1beta_from_base_url(self, mock_cls):
        """用户误填 /v1beta 时应自动剥离，防止 SDK 重复拼接。"""
        provider = _make_provider(api_format="google", base_url="https://generativelanguage.googleapis.com/v1beta")
        create_custom_backend(provider=provider, model_id="gemini-3-flash", media_type="text")

        mock_cls.assert_called_once_with(
            api_key="sk-test",
            base_url="https://generativelanguage.googleapis.com/",
            model="gemini-3-flash",
        )


# ---------------------------------------------------------------------------
# OpenAI URL auto-completion
# ---------------------------------------------------------------------------


class TestOpenAIUrlAutoCompletion:
    """factory 应自动为 OpenAI 格式的 base_url 追加 /v1。"""

    @patch("lib.custom_provider.factory.OpenAITextBackend")
    def test_appends_v1_when_missing(self, mock_cls):
        provider = _make_provider(api_format="openai", base_url="https://api.example.com")
        create_custom_backend(provider=provider, model_id="gpt-5.4", media_type="text")

        mock_cls.assert_called_once_with(
            api_key="sk-test",
            base_url="https://api.example.com/v1",
            model="gpt-5.4",
        )

    @patch("lib.custom_provider.factory.OpenAITextBackend")
    def test_preserves_existing_v1(self, mock_cls):
        provider = _make_provider(api_format="openai", base_url="https://api.example.com/v1")
        create_custom_backend(provider=provider, model_id="gpt-5.4", media_type="text")

        mock_cls.assert_called_once_with(
            api_key="sk-test",
            base_url="https://api.example.com/v1",
            model="gpt-5.4",
        )

    @patch("lib.custom_provider.factory.OpenAITextBackend")
    def test_strips_trailing_slash_and_appends_v1(self, mock_cls):
        provider = _make_provider(api_format="openai", base_url="https://api.example.com/")
        create_custom_backend(provider=provider, model_id="gpt-5.4", media_type="text")

        mock_cls.assert_called_once_with(
            api_key="sk-test",
            base_url="https://api.example.com/v1",
            model="gpt-5.4",
        )

    @patch("lib.custom_provider.factory.OpenAIVideoBackend")
    def test_applies_to_all_media_types(self, mock_cls):
        """video/image 后端同样受 URL 补全影响。"""
        provider = _make_provider(api_format="openai", base_url="https://api.example.com")
        create_custom_backend(provider=provider, model_id="sora-2", media_type="video")

        mock_cls.assert_called_once_with(
            api_key="sk-test",
            base_url="https://api.example.com/v1",
            model="sora-2",
        )


# ---------------------------------------------------------------------------
# Error cases
# ---------------------------------------------------------------------------


class TestErrors:
    def test_unknown_api_format(self):
        provider = _make_provider(api_format="anthropic")
        with pytest.raises(ValueError, match="api_format"):
            create_custom_backend(provider=provider, model_id="claude-4", media_type="text")

    def test_unknown_media_type(self):
        provider = _make_provider(api_format="openai")
        with pytest.raises(ValueError, match="media_type"):
            create_custom_backend(provider=provider, model_id="gpt-5.4", media_type="audio")
