"""OpenAI 连接测试 (_test_openai) 单元测试。"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from server.routers.providers import _test_openai


def _make_model(model_id: str) -> MagicMock:
    m = MagicMock()
    m.id = model_id
    return m


class TestTestOpenAI:
    def test_success_filters_relevant_models(self):
        """应只返回匹配关键词的模型。"""
        mock_models = MagicMock()
        mock_models.data = [
            _make_model("gpt-5.4"),
            _make_model("gpt-5.4-mini"),
            _make_model("sora-2"),
            _make_model("dall-e-3"),
            _make_model("text-embedding-ada-002"),
            _make_model("whisper-1"),
            _make_model("tts-1"),
        ]

        mock_client = MagicMock()
        mock_client.models.list.return_value = mock_models

        with patch("openai.OpenAI", return_value=mock_client):
            result = _test_openai({"api_key": "sk-test"})

        assert result.success is True
        assert result.message == "连接成功"
        assert "gpt-5.4" in result.available_models
        assert "sora-2" in result.available_models
        assert "dall-e-3" in result.available_models
        assert "text-embedding-ada-002" not in result.available_models
        assert "whisper-1" not in result.available_models

    def test_empty_relevant_models(self):
        """所有模型都不匹配关键词时，返回空列表但仍成功。"""
        mock_models = MagicMock()
        mock_models.data = [
            _make_model("text-embedding-3-large"),
            _make_model("whisper-1"),
        ]

        mock_client = MagicMock()
        mock_client.models.list.return_value = mock_models

        with patch("openai.OpenAI", return_value=mock_client):
            result = _test_openai({"api_key": "sk-test"})

        assert result.success is True
        assert result.available_models == []

    def test_models_sorted(self):
        """返回的模型列表应按字母序排列。"""
        mock_models = MagicMock()
        mock_models.data = [
            _make_model("sora-2"),
            _make_model("gpt-5.4"),
            _make_model("dall-e-3"),
        ]

        mock_client = MagicMock()
        mock_client.models.list.return_value = mock_models

        with patch("openai.OpenAI", return_value=mock_client):
            result = _test_openai({"api_key": "sk-test"})

        assert result.available_models == ["dall-e-3", "gpt-5.4", "sora-2"]

    def test_custom_base_url(self):
        """传入 base_url 时应转发到 OpenAI 客户端。"""
        mock_models = MagicMock()
        mock_models.data = [_make_model("gpt-5.4")]

        mock_client = MagicMock()
        mock_client.models.list.return_value = mock_models
        mock_openai_cls = MagicMock(return_value=mock_client)

        with patch("openai.OpenAI", mock_openai_cls):
            _test_openai({"api_key": "sk-test", "base_url": "https://custom.api.com/v1"})

        mock_openai_cls.assert_called_once_with(
            api_key="sk-test",
            base_url="https://custom.api.com/v1",
        )

    def test_api_error_propagates(self):
        """API 异常应向上传播（由调用方 test_provider_connection 统一捕获）。"""
        from openai import AuthenticationError

        mock_client = MagicMock()
        mock_client.models.list.side_effect = AuthenticationError(
            message="Invalid API key",
            response=MagicMock(status_code=401),
            body=None,
        )

        with patch("openai.OpenAI", return_value=mock_client):
            try:
                _test_openai({"api_key": "sk-invalid"})
                assert False, "应抛出异常"
            except AuthenticationError:
                pass  # 预期行为
