"""OpenAI 连接测试 (_test_openai) 单元测试。

替身落在出站 HTTP 上：``openai.OpenAI`` 自带的 httpx 客户端同样被 respx 拦截，URL 拼接与
鉴权 header 因而都在断言范围内（见 ``tests/http_capture``）。
"""

from __future__ import annotations

import pytest

from server.routers.providers import _test_openai
from tests.factories import make_translator
from tests.http_capture import capture_http, only_request

_t = make_translator()

_DEFAULT_MODELS_URL = "https://api.openai.com/v1/models"


def _models_page(*model_ids: str) -> dict[str, object]:
    """OpenAI ``GET /models`` 的响应体形状。"""
    return {"object": "list", "data": [{"id": model_id, "object": "model"} for model_id in model_ids]}


class TestTestOpenAI:
    def test_success_filters_relevant_models(self):
        """应只返回匹配关键词的模型。"""
        with capture_http(assert_all_called=True) as http:
            http.get(_DEFAULT_MODELS_URL).respond(
                json=_models_page(
                    "gpt-5.4",
                    "gpt-5.4-mini",
                    "sora-2",
                    "dall-e-3",
                    "text-embedding-ada-002",
                    "whisper-1",
                    "tts-1",
                )
            )
            result = _test_openai({"api_key": "sk-test"}, _t)

        assert result.success is True
        assert result.message == "连接成功"
        assert "gpt-5.4" in result.available_models
        assert "sora-2" in result.available_models
        assert "dall-e-3" in result.available_models
        assert "text-embedding-ada-002" not in result.available_models
        assert "whisper-1" not in result.available_models

    def test_empty_relevant_models(self):
        """所有模型都不匹配关键词时，返回空列表但仍成功。"""
        with capture_http(assert_all_called=True) as http:
            http.get(_DEFAULT_MODELS_URL).respond(json=_models_page("text-embedding-3-large", "whisper-1"))
            result = _test_openai({"api_key": "sk-test"}, _t)

        assert result.success is True
        assert result.available_models == []

    def test_models_sorted(self):
        """返回的模型列表应按字母序排列。"""
        with capture_http(assert_all_called=True) as http:
            http.get(_DEFAULT_MODELS_URL).respond(json=_models_page("sora-2", "gpt-5.4", "dall-e-3"))
            result = _test_openai({"api_key": "sk-test"}, _t)

        assert result.available_models == ["dall-e-3", "gpt-5.4", "sora-2"]

    def test_custom_base_url(self):
        """传入 base_url 时探测请求应打到该地址，且带上传入的 API Key。"""
        with capture_http(assert_all_called=True) as http:
            route = http.get("https://custom.api.com/v1/models").respond(json=_models_page("gpt-5.4"))
            _test_openai({"api_key": "sk-test", "base_url": "https://custom.api.com/v1"}, _t)

        assert only_request(route).headers["authorization"] == "Bearer sk-test"

    def test_api_error_propagates(self):
        """API 异常应向上传播（由调用方 test_provider_connection 统一捕获）。"""
        from openai import AuthenticationError

        with capture_http(assert_all_called=True) as http:
            http.get(_DEFAULT_MODELS_URL).respond(status_code=401, json={"error": {"message": "Invalid API key"}})
            with pytest.raises(AuthenticationError):
                _test_openai({"api_key": "sk-invalid"}, _t)
