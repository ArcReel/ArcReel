"""Unit tests for the MiniMax voice design operation."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from lib.audio_backends import (
    MiniMaxVoiceDesignClient,
    VoiceDesignRequest,
    extract_voice_design_id,
)
from lib.providers import PROVIDER_MINIMAX

pytestmark = pytest.mark.unit


def _response(payload: object) -> MagicMock:
    response = MagicMock()
    response.status_code = 200
    response.json.return_value = payload
    return response


def _client(response: MagicMock) -> AsyncMock:
    client = AsyncMock()
    client.post = AsyncMock(return_value=response)
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=None)
    return client


class TestVoiceDesignResponse:
    def test_extracts_voice_id(self):
        assert extract_voice_design_id({"voice_id": "narrator-01", "base_resp": {"status_code": 0}}) == "narrator-01"

    def test_surfaces_business_error(self):
        with pytest.raises(RuntimeError, match="status_code=1004.*invalid voice"):
            extract_voice_design_id({"voice_id": "", "base_resp": {"status_code": 1004, "status_msg": "invalid voice"}})

    @pytest.mark.parametrize("payload", [None, [], {}, {"voice_id": "   "}])
    def test_rejects_malformed_response(self, payload):
        with pytest.raises(RuntimeError):
            extract_voice_design_id(payload)


class TestMiniMaxVoiceDesignClient:
    async def test_posts_prompt_and_voice_id_to_domestic_endpoint(self):
        client = _client(_response({"voice_id": "narrator-01", "base_resp": {"status_code": 0}}))
        with patch("httpx.AsyncClient", return_value=client):
            result = await MiniMaxVoiceDesignClient(api_key="sk-test").design_voice(
                VoiceDesignRequest(prompt="A calm documentary narrator", voice_id="narrator-01")
            )

        assert client.post.call_args.args[0] == "https://api.minimaxi.com/v1/voice_design"
        assert client.post.call_args.kwargs["json"] == {
            "prompt": "A calm documentary narrator",
            "voice_id": "narrator-01",
        }
        assert client.post.call_args.kwargs["headers"]["Authorization"] == "Bearer sk-test"
        assert result.provider == PROVIDER_MINIMAX
        assert result.voice_id == "narrator-01"

    async def test_uses_configured_international_endpoint(self):
        client = _client(_response({"voice_id": "voice-02", "base_resp": {"status_code": 0}}))
        with patch("httpx.AsyncClient", return_value=client):
            await MiniMaxVoiceDesignClient(
                api_key="sk-test",
                base_url="https://api.minimax.io/v1",
            ).design_voice(VoiceDesignRequest(prompt="Bright and energetic", voice_id="voice-02"))

        assert client.post.call_args.args[0] == "https://api.minimax.io/v1/voice_design"

    @pytest.mark.parametrize(
        "design_request",
        [
            VoiceDesignRequest(prompt="", voice_id="voice-01"),
            VoiceDesignRequest(prompt="A narrator", voice_id=" "),
        ],
    )
    async def test_rejects_blank_required_fields_before_http(self, design_request):
        with patch("httpx.AsyncClient") as async_client:
            with pytest.raises(ValueError, match="must not be blank"):
                await MiniMaxVoiceDesignClient(api_key="sk-test").design_voice(design_request)
        async_client.assert_not_called()
