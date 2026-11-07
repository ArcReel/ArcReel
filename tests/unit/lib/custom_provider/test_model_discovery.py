"""Unit tests for model discovery (discover_models / infer_endpoint smoke checks)."""

from __future__ import annotations

import asyncio
from collections.abc import Generator
from contextlib import contextmanager
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock, patch

import httpx
import pytest

from tests.http_capture import capture_http, only_request


@contextmanager
def _recorded_genai_clients(models: list[Any] | None = None) -> Generator[list[dict[str, Any]]]:
    """genai client constructor recorder: retain client arguments and return a substitute listing the supplied models.

    The auth/base_url passthrough contract is the constructor arguments themselves; assert recorded arguments, not substitute call targets.
    """
    created: list[dict[str, Any]] = []
    listed = list(models or [])
    client = SimpleNamespace(models=SimpleNamespace(list=lambda: listed))

    def _create(**kwargs: Any) -> Any:
        created.append(kwargs)
        return client

    with patch("lib.custom_provider.discovery.genai.Client", _create):
        yield created


# ---------------------------------------------------------------------------
# infer_endpoint smoke checks; main coverage lives in test_custom_provider_endpoints.py.
# ---------------------------------------------------------------------------


class TestInferEndpointSmoke:
    """Lightweight smoke check: infer_endpoint remains importable from endpoints and returns reasonable values."""

    def test_text_model(self):
        from lib.custom_provider.endpoints import infer_endpoint

        assert infer_endpoint("gpt-4o", "openai") == "openai-chat"

    def test_image_model(self):
        from lib.custom_provider.endpoints import infer_endpoint

        assert infer_endpoint("dall-e-3", "openai") == "openai-images"

    def test_video_model(self):
        from lib.custom_provider.endpoints import infer_endpoint

        # Kling video selects native kling-video instead of defaulting video families containing kling to openai-video.
        assert infer_endpoint("kling-v2", "openai") == "kling-video-silent"
        assert infer_endpoint("sora-2", "openai") == "openai-video"

    def test_google_text(self):
        from lib.custom_provider.endpoints import infer_endpoint

        assert infer_endpoint("gemini-3-flash", "google") == "gemini-generate"


# ---------------------------------------------------------------------------
# discover_models — OpenAI format
# ---------------------------------------------------------------------------


class TestDiscoverModelsOpenAI:
    @patch("lib.custom_provider.discovery.OpenAI")
    async def test_basic_discovery(self, mock_openai_cls):
        """Basic model-discovery flow returns endpoint fields."""
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

        # Pass auth and base_url unchanged to SDK client constructor arguments.
        mock_openai_cls.assert_called_once_with(
            api_key="sk-test",
            base_url="https://api.example.com/v1",
        )
        assert len(result) == 3
        # Sort by ID.
        ids = [m["model_id"] for m in result]
        assert ids == ["dall-e-3", "gpt-4o", "kling-v2"]
        # Every item has endpoint.
        for m in result:
            assert "endpoint" in m

    @patch("lib.custom_provider.discovery.OpenAI")
    async def test_each_model_reports_the_entry_it_maps_to(self, mock_openai_cls):
        """Discovery includes name-matched entries or None, showing which new models will be unpriced before creation."""
        mock_client = MagicMock()
        mock_openai_cls.return_value = mock_client
        known = MagicMock()
        known.id = "openai/GPT-5.4"
        unknown = MagicMock()
        unknown.id = "my-private-llm"
        mock_client.models.list.return_value = [known, unknown]

        from lib.custom_provider.discovery import discover_models

        result = await discover_models(discovery_format="openai", base_url="https://api.example.com/v1", api_key="k")

        matched = {m["model_id"]: m["matched_entry"] for m in result}
        assert matched["my-private-llm"] is None
        assert (matched["openai/GPT-5.4"]["maker"], matched["openai/GPT-5.4"]["name"]) == ("openai", "gpt-5.4")

    @patch("lib.custom_provider.discovery.OpenAI")
    async def test_amphibious_model_maps_by_name_inferred_media_type(self, mock_openai_cls):
        """Dual-modality models cannot match without media type; use name-inferred media type to select an entry and choose an endpoint within its media type."""
        mock_client = MagicMock()
        mock_openai_cls.return_value = mock_client
        omni = MagicMock()
        omni.id = "kling-v3-omni"
        mock_client.models.list.return_value = [omni]

        from lib.custom_provider.discovery import discover_models

        [result] = await discover_models(discovery_format="openai", base_url="https://api.example.com/v1", api_key="k")

        assert (result["recognized"], result["media_type"], result["endpoint"]) == (True, "video", "kling-video-audio")
        assert result["matched_entry"]["media_type"] == "video"
        assert result["endpoint_by_media_type"]["image"] == "kling-image"

    @patch("lib.custom_provider.discovery.OpenAI")
    async def test_default_marking(self, mock_openai_cls):
        """Mark each media_type's first model after sorting is_default=True."""
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

        # Sort by ID: dall-e-4, gpt-5.4, gpt-5.4-mini, gpt-image-1.5.
        # openai-images: dall-e-4 (default), gpt-image-1.5
        # openai-chat: gpt-5.4 (default), gpt-5.4-mini
        text_models = [m for m in result if m["endpoint"] == "openai-chat"]
        image_models = [m for m in result if m["endpoint"] == "openai-images"]

        assert text_models[0]["is_default"] is True
        assert text_models[1]["is_default"] is False
        assert image_models[0]["is_default"] is True
        assert image_models[1]["is_default"] is False

    @patch("lib.custom_provider.discovery.OpenAI")
    async def test_tts_model_derives_audio_media_type(self, mock_openai_cls):
        """TTS models infer openai-tts; audio has an independent media_type default group, without competing with text defaults."""
        mock_client = MagicMock()
        mock_openai_cls.return_value = mock_client

        model_a = MagicMock()
        model_a.id = "gpt-4o"
        model_b = MagicMock()
        model_b.id = "tts-1"
        model_c = MagicMock()
        model_c.id = "tts-1-hd"
        mock_client.models.list.return_value = [model_a, model_b, model_c]

        from lib.custom_provider.discovery import discover_models

        result = await discover_models(
            discovery_format="openai",
            base_url="https://api.example.com/v1",
            api_key="sk-test",
        )

        audio_models = [m for m in result if m["endpoint"] == "openai-tts"]
        text_models = [m for m in result if m["endpoint"] == "openai-chat"]
        assert [m["model_id"] for m in audio_models] == ["tts-1", "tts-1-hd"]
        # The first audio model is default without affecting the text group's own default.
        assert audio_models[0]["is_default"] is True
        assert audio_models[1]["is_default"] is False
        assert text_models[0]["is_default"] is True

    @patch("lib.custom_provider.discovery.OpenAI")
    async def test_all_enabled(self, mock_openai_cls):
        """Mark every discovered model is_enabled=True."""
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
        """display_name equals model_id."""
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
        """Raise when the API is unreachable."""
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
        """An empty model list yields empty results."""
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
        """Basic Google-format model discovery."""
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
        """Infer endpoint through model_id keywords."""
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
        assert by_id["veo-3"]["endpoint"] == "gemini-video"  # Google-discovered Veo selects native endpoints.

    @patch("lib.custom_provider.discovery.genai")
    async def test_default_marking_google(self, mock_genai):
        """Default marking for Google format."""
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
        """Remove the 'models/' prefix from Google API model names."""
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

    async def test_text_models_prefill_output_token_limit(self):
        """Prefill maximum text output length from outputTokenLimit; do not prefill for other media or models lacking it."""
        from lib.custom_provider.discovery import discover_models

        models = [
            SimpleNamespace(name="models/gemini-3-flash", output_token_limit=65536),
            SimpleNamespace(name="models/gemini-3-pro", output_token_limit=None),
            SimpleNamespace(name="models/gemini-3-flash-image-preview", output_token_limit=32768),
        ]
        with _recorded_genai_clients(models):
            result = await discover_models(discovery_format="google", base_url=None, api_key="test-key")

        assert {m["model_id"]: m["max_output_tokens"] for m in result} == {
            "gemini-3-flash": 65536,
            "gemini-3-pro": None,
            "gemini-3-flash-image-preview": None,
        }

    async def test_no_base_url(self):
        """Omit http_options when base_url is None."""
        from lib.custom_provider.discovery import discover_models

        with _recorded_genai_clients() as created:
            await discover_models(
                discovery_format="google",
                base_url=None,
                api_key="test-key",
            )

        assert created == [{"api_key": "test-key"}]

    async def test_with_base_url(self):
        """Pass http_options when base_url is nonempty."""
        from lib.custom_provider.discovery import discover_models

        with _recorded_genai_clients() as created:
            await discover_models(
                discovery_format="google",
                base_url="https://custom-endpoint.com/",
                api_key="test-key",
            )

        assert created == [{"api_key": "test-key", "http_options": {"base_url": "https://custom-endpoint.com/"}}]


# ---------------------------------------------------------------------------
# Unknown format
# ---------------------------------------------------------------------------


class TestUnknownFormat:
    async def test_unknown_discovery_format(self):
        from lib.custom_provider.discovery import discover_models

        with pytest.raises(ValueError, match="discovery_format"):
            await discover_models(
                discovery_format="bogus",
                base_url="https://api.example.com",
                api_key="sk-test",
            )


# ---------------------------------------------------------------------------
# discover_models — Anthropic format
# ---------------------------------------------------------------------------


class TestDiscoverModelsAnthropic:
    async def test_basic_discovery(self):
        """Sort Anthropic models by ID; return the same item shape as OpenAI/Google with an empty endpoint."""
        from lib.custom_provider.discovery import discover_models

        with capture_http() as http:
            # Normalize to the call root, then append /v1/models to the path while preserving subpaths.
            route = http.get("https://example.com/anthropic/v1/models").respond(
                json={
                    "data": [
                        {"id": "claude-opus-4-7", "display_name": "Opus 4.7"},
                        {"id": "claude-haiku-4-5", "display_name": "Haiku 4.5"},
                    ]
                }
            )
            result = await discover_models(
                discovery_format="anthropic",
                base_url="https://example.com/anthropic/v1/messages",  # Full endpoint URL verifies normalization.
                api_key="sk-ant-test",
            )

        assert result == [
            {
                "model_id": "claude-haiku-4-5",
                "display_name": "Haiku 4.5",
                "endpoint": "",
                "is_default": False,
                "is_enabled": True,
            },
            {
                "model_id": "claude-opus-4-7",
                "display_name": "Opus 4.7",
                "endpoint": "",
                "is_default": False,
                "is_enabled": True,
            },
        ]
        request = only_request(route)
        assert request.headers["x-api-key"] == "sk-ant-test"
        assert request.headers["anthropic-version"] == "2023-06-01"

    async def test_default_base_url_when_none(self):
        """Absent base_url uses official https://api.anthropic.com."""
        from lib.custom_provider.discovery import discover_models

        with capture_http() as http:
            route = http.get("https://api.anthropic.com/v1/models").respond(json={"data": []})
            await discover_models(discovery_format="anthropic", base_url=None, api_key="key")

        assert route.call_count == 1

    async def test_skips_entries_without_id(self):
        """Skip data entries lacking id; use id as the display name when display_name is absent."""
        from lib.custom_provider.discovery import discover_models

        with capture_http() as http:
            http.get("https://api.anthropic.com/v1/models").respond(
                json={"data": [{"id": "claude-x"}, {"display_name": "no id"}]}
            )
            result = await discover_models(discovery_format="anthropic", base_url=None, api_key="k")

        assert [m["model_id"] for m in result] == ["claude-x"]
        assert result[0]["display_name"] == "claude-x"

    @pytest.mark.parametrize(
        "base_url",
        [
            "https://ant-user:sk-leaked-userinfo@relay.example.com/anthropic",
            "https://relay.example.com/anthropic?api_key=sk-leaked-query",
            "https://relay.example.com/anthropic#frag",
            "relay.example.com/anthropic",
        ],
    )
    async def test_rejects_unsupported_base_url(self, base_url: str):
        """Reject query / fragment / userinfo or missing schemes before sending requests, without echoing input in messages."""
        from lib.config.url_utils import InvalidAnthropicBaseUrlError
        from lib.custom_provider.discovery import discover_models

        with pytest.raises(InvalidAnthropicBaseUrlError) as exc_info:
            await discover_models(discovery_format="anthropic", base_url=base_url, api_key="sk-ant")
        assert "sk-leaked" not in str(exc_info.value)

    async def test_status_error_message_drops_request_url(self):
        """4xx raises redacted HTTPStatusError: retain only status and request URL, excluding the response body."""
        base_url = "https://relay.example.com/anthropic"
        from lib.custom_provider.discovery import discover_models

        with capture_http() as http:
            http.get(host="relay.example.com").respond(status_code=401, text="unauthorized sk-body-secret")
            with pytest.raises(httpx.HTTPStatusError) as exc_info:
                await discover_models(discovery_format="anthropic", base_url=base_url, api_key="sk-ant")

        assert str(exc_info.value) == "401 response for https://relay.example.com/anthropic/v1/models"
        assert "sk-body-secret" not in str(exc_info.value)
        assert exc_info.value.response.status_code == 401

    async def test_unknown_format_raises(self):
        """anthropic remains a known format; unknown formats raise ValueError mentioning anthropic."""
        from lib.custom_provider.discovery import discover_models

        with pytest.raises(ValueError, match="anthropic"):
            await discover_models(discovery_format="bogus", base_url=None, api_key="k")


# ---------------------------------------------------------------------------
# Task-spec example fixture.
# ---------------------------------------------------------------------------


def test_discover_openai_returns_endpoints(monkeypatch):
    """discover_models(discovery_format='openai', ...) returns items containing endpoint."""
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
    assert by_id["kling-v2"]["endpoint"] == "kling-video-silent"
    assert by_id["dall-e-3"]["endpoint"] == "openai-images"
    # Only one default per media_type.
    defaults = [m for m in result if m["is_default"]]
    assert {m["endpoint"] for m in defaults} == {"openai-chat", "kling-video-silent", "openai-images"}
