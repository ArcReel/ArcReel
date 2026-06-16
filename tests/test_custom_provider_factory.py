"""create_custom_backend(provider, model_id, endpoint) 单元测试。"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from lib.custom_provider.backends import (
    CustomAudioBackend,
    CustomImageBackend,
    CustomTextBackend,
    CustomVideoBackend,
)
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

    @patch("lib.custom_provider.endpoints.V2VideoGenerationsBackend")
    def test_v2_video_generations(self, mock_cls):
        provider = _make_provider(base_url="https://api.aimlapi.com")
        result = create_custom_backend(
            provider=provider, model_id="bytedance/seedance-1-0-lite-i2v", endpoint="v2-video-generations"
        )
        assert isinstance(result, CustomVideoBackend)
        # base_url 原样下传，归一化由 V2VideoGenerationsBackend 内部处理
        mock_cls.assert_called_once_with(
            api_key="sk-test", base_url="https://api.aimlapi.com", model="bytedance/seedance-1-0-lite-i2v"
        )

    @patch("lib.custom_provider.endpoints.ArkVideoBackend")
    def test_ark_seedance(self, mock_cls):
        provider = _make_provider(base_url="https://relay.example.com")
        result = create_custom_backend(provider=provider, model_id="doubao-seedance-2-0", endpoint="ark-seedance")
        assert isinstance(result, CustomVideoBackend)
        # 仅 host → 补全 ark 协议挂载路径 /api/v3
        mock_cls.assert_called_once_with(
            api_key="sk-test", base_url="https://relay.example.com/api/v3", model="doubao-seedance-2-0"
        )

    @patch("lib.custom_provider.endpoints.ViduVideoBackend")
    def test_vidu_video(self, mock_cls):
        provider = _make_provider(base_url="https://relay.example.com")
        result = create_custom_backend(provider=provider, model_id="viduq3-turbo", endpoint="vidu-video")
        assert isinstance(result, CustomVideoBackend)
        # 仅 host → 补全 vidu 协议挂载路径 /ent/v2
        mock_cls.assert_called_once_with(
            api_key="sk-test", base_url="https://relay.example.com/ent/v2", model="viduq3-turbo"
        )

    @patch("lib.custom_provider.endpoints.OpenAIAudioBackend")
    def test_openai_tts(self, mock_cls):
        provider = _make_provider()
        result = create_custom_backend(provider=provider, model_id="tts-1", endpoint="openai-tts")
        assert isinstance(result, CustomAudioBackend)
        assert result.name == "custom-42"
        assert result.model == "tts-1"
        # provider_name 让 delegate 记账/日志归因到真实 provider 而非内置 openai
        mock_cls.assert_called_once_with(
            api_key="sk-test",
            base_url="https://api.example.com/v1",
            model="tts-1",
            provider_name="custom-42",
        )

    @patch("lib.custom_provider.endpoints.OpenAIAudioBackend")
    def test_openai_tts_appends_v1(self, mock_cls):
        provider = _make_provider(base_url="https://relay.example.com")
        create_custom_backend(provider=provider, model_id="speech-1.5", endpoint="openai-tts")
        mock_cls.assert_called_once_with(
            api_key="sk-test",
            base_url="https://relay.example.com/v1",
            model="speech-1.5",
            provider_name="custom-42",
        )

    @patch("lib.custom_provider.endpoints.MiniMaxImageBackend")
    def test_minimax_image(self, mock_cls):
        provider = _make_provider(base_url="https://api.minimaxi.com/v1")
        result = create_custom_backend(provider=provider, model_id="image-01", endpoint="minimax-image")
        assert isinstance(result, CustomImageBackend)
        assert result.model == "image-01"
        # base_url 原样下传，归一化（host→{host}/v1）由 MiniMaxImageBackend 内部处理
        mock_cls.assert_called_once_with(api_key="sk-test", base_url="https://api.minimaxi.com/v1", model="image-01")

    @patch("lib.custom_provider.endpoints.MiniMaxVideoBackend")
    def test_minimax_video(self, mock_cls):
        provider = _make_provider(base_url="https://api.minimaxi.com/v1")
        result = create_custom_backend(provider=provider, model_id="MiniMax-Hailuo-2.3", endpoint="minimax-video")
        assert isinstance(result, CustomVideoBackend)
        assert result.model == "MiniMax-Hailuo-2.3"
        mock_cls.assert_called_once_with(
            api_key="sk-test", base_url="https://api.minimaxi.com/v1", model="MiniMax-Hailuo-2.3"
        )

    @patch("lib.custom_provider.endpoints.KlingImageBackend")
    def test_kling_image(self, mock_cls):
        provider = _make_provider(base_url="https://relay.example.com/v1")
        result = create_custom_backend(provider=provider, model_id="kling-image-o1", endpoint="kling-image")
        assert isinstance(result, CustomImageBackend)
        assert result.model == "kling-image-o1"
        # bearer 模式：静态 api_key 旁路 JWT；显式 /v1 路径原样信任；原生 model_name 透传
        mock_cls.assert_called_once_with(
            auth_mode="bearer",
            api_key="sk-test",
            base_url="https://relay.example.com/v1",
            model="kling-image-o1",
        )

    @patch("lib.custom_provider.endpoints.KlingImageBackend")
    def test_kling_image_host_only_mounts_v1(self, mock_cls):
        provider = _make_provider(base_url="https://relay.example.com")
        create_custom_backend(provider=provider, model_id="kling-image-o1", endpoint="kling-image")
        # 仅 host → 补全可灵协议挂载路径 /v1
        mock_cls.assert_called_once_with(
            auth_mode="bearer",
            api_key="sk-test",
            base_url="https://relay.example.com/v1",
            model="kling-image-o1",
        )

    @patch("lib.custom_provider.endpoints.KlingVideoBackend")
    def test_kling_video(self, mock_cls):
        provider = _make_provider(base_url="https://relay.example.com/v1")
        result = create_custom_backend(provider=provider, model_id="kling-v2-5-turbo", endpoint="kling-video")
        assert isinstance(result, CustomVideoBackend)
        assert result.model == "kling-v2-5-turbo"
        mock_cls.assert_called_once_with(
            auth_mode="bearer",
            api_key="sk-test",
            base_url="https://relay.example.com/v1",
            model="kling-v2-5-turbo",
        )

    @patch("lib.custom_provider.endpoints.KlingVideoBackend")
    def test_kling_video_host_only_mounts_v1(self, mock_cls):
        provider = _make_provider(base_url="relay.example.com")
        create_custom_backend(provider=provider, model_id="kling-v3", endpoint="kling-video")
        # 纯域名（无 scheme）→ 补 https:// 再挂载 /v1
        mock_cls.assert_called_once_with(
            auth_mode="bearer",
            api_key="sk-test",
            base_url="https://relay.example.com/v1",
            model="kling-v3",
        )

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

    @patch("lib.custom_provider.endpoints.ArkVideoBackend")
    def test_ark_explicit_path_passthrough(self, mock_cls):
        """已带显式路径（/api/v3）→ 原样信任，不重复叠加。"""
        provider = _make_provider(base_url="https://relay.example.com/api/v3")
        create_custom_backend(provider=provider, model_id="doubao-seedance-2-0", endpoint="ark-seedance")
        mock_cls.assert_called_once_with(
            api_key="sk-test", base_url="https://relay.example.com/api/v3", model="doubao-seedance-2-0"
        )

    @patch("lib.custom_provider.endpoints.ViduVideoBackend")
    def test_vidu_explicit_path_passthrough(self, mock_cls):
        provider = _make_provider(base_url="https://api.vidu.cn/ent/v2")
        create_custom_backend(provider=provider, model_id="viduq3-turbo", endpoint="vidu-video")
        mock_cls.assert_called_once_with(api_key="sk-test", base_url="https://api.vidu.cn/ent/v2", model="viduq3-turbo")

    @patch("lib.custom_provider.endpoints.ArkVideoBackend")
    def test_ark_host_only_no_scheme(self, mock_cls):
        """纯域名（无 scheme）→ 补 https:// 再挂载 /api/v3。"""
        provider = _make_provider(base_url="relay.example.com")
        create_custom_backend(provider=provider, model_id="doubao-seedance-2-0", endpoint="ark-seedance")
        mock_cls.assert_called_once_with(
            api_key="sk-test", base_url="https://relay.example.com/api/v3", model="doubao-seedance-2-0"
        )

    @patch("lib.custom_provider.endpoints.ViduVideoBackend")
    def test_vidu_host_only_no_scheme(self, mock_cls):
        provider = _make_provider(base_url="relay.example.com")
        create_custom_backend(provider=provider, model_id="viduq3-turbo", endpoint="vidu-video")
        mock_cls.assert_called_once_with(
            api_key="sk-test", base_url="https://relay.example.com/ent/v2", model="viduq3-turbo"
        )

    @patch("lib.custom_provider.endpoints.ArkVideoBackend")
    def test_ark_empty_base_url_normalizes_to_none(self, mock_cls):
        """空 base_url → _ensure_url_path_suffix 归一化为 None 下传（不强行补挂载路径）。"""
        provider = _make_provider(base_url="")
        create_custom_backend(provider=provider, model_id="doubao-seedance-2-0", endpoint="ark-seedance")
        mock_cls.assert_called_once_with(api_key="sk-test", base_url=None, model="doubao-seedance-2-0")

    @patch("lib.custom_provider.endpoints.ViduVideoBackend")
    def test_vidu_empty_base_url_normalizes_to_none(self, mock_cls):
        provider = _make_provider(base_url="")
        create_custom_backend(provider=provider, model_id="viduq3-turbo", endpoint="vidu-video")
        mock_cls.assert_called_once_with(api_key="sk-test", base_url=None, model="viduq3-turbo")


class TestErrors:
    def test_unknown_endpoint(self):
        provider = _make_provider()
        with pytest.raises(ValueError, match="unknown endpoint"):
            create_custom_backend(provider=provider, model_id="claude-4", endpoint="anthropic-messages")

    def test_v2_empty_base_url_raises(self):
        """v2-video-generations 强制要求 base_url（无默认 host），空值 fail-loud。"""
        provider = _make_provider(base_url="")
        with pytest.raises(ValueError, match="需要 base_url"):
            create_custom_backend(provider=provider, model_id="some-model", endpoint="v2-video-generations")
