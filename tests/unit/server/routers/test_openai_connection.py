"""OpenAI connection-test (_check_openai) unit tests.

Fake outbound HTTP: respx also intercepts the httpx client inside ``openai.OpenAI``, covering URL construction and
authorization headers in assertions (see ``tests/http_capture``).
"""

from __future__ import annotations

import pytest

from server.routers.providers import _check_openai
from tests.factories import make_translator
from tests.http_capture import capture_http, only_request

_t = make_translator()

_DEFAULT_MODELS_URL = "https://api.openai.com/v1/models"


def _models_page(*model_ids: str) -> dict[str, object]:
    """OpenAI ``GET /models`` response-body shape."""
    return {"object": "list", "data": [{"id": model_id, "object": "model"} for model_id in model_ids]}


class TestTestOpenAI:
    def test_success_filters_relevant_models(self):
        """Return keyword-matching models only."""
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
            result = _check_openai({"api_key": "sk-test"}, _t)

        assert result.success is True
        assert result.message == "连通正常"
        assert "gpt-5.4" in result.available_models
        assert "sora-2" in result.available_models
        assert "dall-e-3" in result.available_models
        assert "text-embedding-ada-002" not in result.available_models
        assert "whisper-1" not in result.available_models

    def test_empty_relevant_models(self):
        """No keyword matches returns empty lists but still succeeds."""
        with capture_http(assert_all_called=True) as http:
            http.get(_DEFAULT_MODELS_URL).respond(json=_models_page("text-embedding-3-large", "whisper-1"))
            result = _check_openai({"api_key": "sk-test"}, _t)

        assert result.success is True
        assert result.available_models == []

    def test_models_sorted(self):
        """Returned model lists must be alphabetical."""
        with capture_http(assert_all_called=True) as http:
            http.get(_DEFAULT_MODELS_URL).respond(json=_models_page("sora-2", "gpt-5.4", "dall-e-3"))
            result = _check_openai({"api_key": "sk-test"}, _t)

        assert result.available_models == ["dall-e-3", "gpt-5.4", "sora-2"]

    def test_custom_base_url(self):
        """Supplied base_url receives probes carrying supplied API Keys."""
        with capture_http(assert_all_called=True) as http:
            route = http.get("https://custom.api.com/v1/models").respond(json=_models_page("gpt-5.4"))
            _check_openai({"api_key": "sk-test", "base_url": "https://custom.api.com/v1"}, _t)

        assert only_request(route).headers["authorization"] == "Bearer sk-test"

    def test_api_error_propagates(self):
        """API exceptions propagate upward, uniformly caught by check_provider_connectivity callers."""
        from openai import AuthenticationError

        with capture_http(assert_all_called=True) as http:
            http.get(_DEFAULT_MODELS_URL).respond(status_code=401, json={"error": {"message": "Invalid API key"}})
            with pytest.raises(AuthenticationError):
                _check_openai({"api_key": "sk-invalid"}, _t)
