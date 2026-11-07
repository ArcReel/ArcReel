"""Custom-provider management API tests.

Test CRUD, model management, discovery and connection endpoints through
TestClient + dependency_overrides, using in-memory SQLite databases.
"""

from __future__ import annotations

import json
from collections.abc import Generator
from dataclasses import replace
from pathlib import Path
from typing import Any, ClassVar
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession

from lib.config.service import ConfigService
from lib.custom_provider.endpoints import (
    ENDPOINT_REGISTRY,
    EndpointSpec,
    declarative_endpoint_spec,
    get_endpoint_spec,
)
from tests.factories import custom_endpoint_definition
from tests.fakes import use_project_manager
from tests.http_capture import capture_http, only_request

_EXAMPLE_TEMPLATE_PATH = (
    Path(__file__).resolve().parents[4] / "frontend" / "src" / "data" / "example-templates" / "generic-submit-poll.json"
)


def _example_template_definition() -> dict[str, Any]:
    """Bundled example templates, used here as valid declarative definitions."""
    return json.loads(_EXAMPLE_TEMPLATE_PATH.read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def custom_providers_client(custom_providers_app) -> Generator[TestClient, None, None]:
    with TestClient(custom_providers_app) as c:
        yield c


# ---------------------------------------------------------------------------
# Provider CRUD
# ---------------------------------------------------------------------------


class TestCreateProvider:
    def test_returns_201(self, custom_providers_client: TestClient):
        resp = custom_providers_client.post(
            "/api/v1/custom-providers",
            json={
                "display_name": "Test Provider",
                "discovery_format": "openai",
                "base_url": "https://api.example.com/v1",
                "api_key": "sk-test-key-12345678",
                "models": [
                    {
                        "model_id": "gpt-4",
                        "display_name": "GPT-4",
                        "endpoint": "openai-chat",
                    }
                ],
            },
        )
        assert resp.status_code == 201

    @pytest.mark.parametrize("model_id", [".", "..", "a/../b", "a/./b"])
    def test_rejects_model_id_with_dot_segment(self, custom_providers_client: TestClient, model_id: str):
        resp = custom_providers_client.post(
            "/api/v1/custom-providers",
            json={
                "display_name": "Test Provider",
                "discovery_format": "openai",
                "base_url": "https://api.example.com/v1",
                "api_key": "sk-test-key-12345678",
                "models": [{"model_id": model_id, "display_name": "x", "endpoint": "openai-chat"}],
            },
        )
        assert resp.status_code == 422

    def test_response_structure(self, custom_providers_client: TestClient):
        resp = custom_providers_client.post(
            "/api/v1/custom-providers",
            json={
                "display_name": "Test Provider",
                "discovery_format": "openai",
                "base_url": "https://api.example.com/v1",
                "api_key": "sk-test-key-12345678",
                "models": [
                    {
                        "model_id": "gpt-4",
                        "display_name": "GPT-4",
                        "endpoint": "openai-chat",
                    }
                ],
            },
        )
        body = resp.json()
        assert body["display_name"] == "Test Provider"
        assert body["discovery_format"] == "openai"
        assert body["base_url"] == "https://api.example.com/v1"
        # api_key must be masked
        assert "sk-test-key-12345678" not in body["api_key_masked"]
        assert body["api_key_masked"].startswith("sk-t")
        assert len(body["models"]) == 1
        assert body["models"][0]["model_id"] == "gpt-4"
        assert "created_at" in body

    def test_create_without_models(self, custom_providers_client: TestClient):
        resp = custom_providers_client.post(
            "/api/v1/custom-providers",
            json={
                "display_name": "Empty Provider",
                "discovery_format": "google",
                "base_url": "https://api.example.com",
                "api_key": "AIza-test-12345678",
            },
        )
        assert resp.status_code == 201
        assert resp.json()["models"] == []

    def test_create_openai_discovery_format_provider(self, custom_providers_client: TestClient):
        """POST /custom-providers accepts discovery_format=openai and persists correct fields."""
        resp = custom_providers_client.post(
            "/api/v1/custom-providers",
            json={
                "display_name": "OpenAI Gateway",
                "discovery_format": "openai",
                "base_url": "https://openai.example.com/v1",
                "api_key": "sk-openai-test-12345",
                "models": [
                    {
                        "model_id": "kling-v1",
                        "display_name": "Kling v1",
                        "endpoint": "newapi-video",
                        "is_default": True,
                        "is_enabled": True,
                    },
                ],
            },
        )
        assert resp.status_code == 201
        body = resp.json()
        assert body["discovery_format"] == "openai"
        assert body["base_url"] == "https://openai.example.com/v1"
        assert len(body["models"]) == 1
        assert body["models"][0]["model_id"] == "kling-v1"


class TestListProviders:
    def test_empty_list(self, custom_providers_client: TestClient):
        resp = custom_providers_client.get("/api/v1/custom-providers")
        assert resp.status_code == 200
        assert resp.json() == {"providers": []}

    def test_lists_created_providers(self, custom_providers_client: TestClient):
        # Create two providers
        custom_providers_client.post(
            "/api/v1/custom-providers",
            json={
                "display_name": "Provider A",
                "discovery_format": "openai",
                "base_url": "https://a.example.com/v1",
                "api_key": "sk-aaaa-key-12345678",
            },
        )
        custom_providers_client.post(
            "/api/v1/custom-providers",
            json={
                "display_name": "Provider B",
                "discovery_format": "google",
                "base_url": "https://b.example.com",
                "api_key": "AIza-bbbb-12345678",
            },
        )
        resp = custom_providers_client.get("/api/v1/custom-providers")
        assert resp.status_code == 200
        body = resp.json()["providers"]
        assert len(body) == 2
        assert body[0]["display_name"] == "Provider A"
        assert body[1]["display_name"] == "Provider B"


class TestEndpointCatalog:
    """GET /endpoints exposes ENDPOINT_REGISTRY as frontend's sole truth."""

    def test_lists_all_endpoints(self, custom_providers_client: TestClient):
        resp = custom_providers_client.get("/api/v1/custom-providers/endpoints")
        assert resp.status_code == 200
        body = resp.json()
        keys = {e["key"] for e in body["endpoints"]}
        assert keys == {
            "openai-chat",
            "openai-responses",
            "gemini-generate",
            "anthropic-messages",
            "openai-images",
            "openai-images-generations",
            "openai-images-edits",
            "openai-chat-image",
            "openrouter-images",
            "gemini-image",
            "openai-video",
            "newapi-video",
            "v2-video-generations",
            "ark-seedance-1",
            "ark-seedance-2",
            "ark-seedance-2-5",
            "ark-seedream-3",
            "ark-seedream-4",
            "vidu-video-q3-turbo",
            "vidu-video-generic",
            "vidu-video-q3-pro",
            "vidu-video-q3-fast",
            "vidu-video-q3-reference",
            "vidu-video-q2",
            "vidu-video-q2-pro",
            "vidu-video-q2-fast",
            "vidu-video-q1",
            "vidu-video-q1-classic",
            "vidu-video-2",
            "vidu-video-q4",
            "openrouter-video",
            "xai-video",
            "dashscope-qwen-image",
            "dashscope-wan-image",
            "dashscope-qwen-image-edit",
            "dashscope-video",
            "dashscope-always-on-video",
            "dashscope-reference-video",
            "dashscope-reference-voice-video",
            "dashscope-wan3-video",
            "minimax-image",
            "minimax-hailuo-v1",
            "minimax-hailuo-v1-fast",
            "minimax-s2v-01",
            "minimax-h3",
            "kling-image",
            "kling-image-single",
            "kling-video-pro-silent",
            "kling-video-pro-audio",
            "kling-video-audio",
            "kling-video-silent",
            "kling-video-model-path",
            "gemini-video",
            "gemini-interactions-video",
            "openai-tts",
            "dashscope-tts",
            "dashscope-speech-synthesizer",
            "minimax-tts",
            "gemini-tts",
        }

    def test_descriptor_shape(self, custom_providers_client: TestClient):
        resp = custom_providers_client.get("/api/v1/custom-providers/endpoints")
        assert resp.status_code == 200
        for entry in resp.json()["endpoints"]:
            assert set(entry.keys()) == {
                "key",
                "media_type",
                "family",
                "kind",
                "display_name_key",
                "display_name",
                "source",
                "request_method",
                "request_path_template",
                "image_capabilities",
                "end_image_capable",
                "size_fixed",
                "duration_fixed",
                "duration_frame_rate_missing",
                "duration_tier_empty",
                "native_resolution",
                "requests",
            }
            assert entry["request_method"] == "POST"
            assert entry["request_path_template"].startswith("/")

    def test_endpoints_expose_kind_and_display_name(self, custom_providers_client: TestClient):
        """kind distinguishes implementations; frontend uses it to expose Copy to mine / View definition.

        Declarative display names use definition meta.name; built-in Python retains display_name_key localization.
        """
        resp = custom_providers_client.get("/api/v1/custom-providers/endpoints")
        assert resp.status_code == 200
        by_key = {e["key"]: e for e in resp.json()["endpoints"]}
        for key, spec in ENDPOINT_REGISTRY.items():
            entry = by_key[key]
            assert entry["kind"] == spec.kind
            assert entry["display_name"] == spec.display_name
            if spec.kind == "declarative":
                assert entry["display_name_key"] == ""
            else:
                assert entry["display_name_key"]

    def test_endpoints_expose_end_image_capable(self, custom_providers_client: TestClient):
        """Catalogs include end_image_capable so frontend disables last_frame force-on for endpoints not forwarding ending frames,
        without users encountering write-time 422s first. Always False for nonvideo types."""
        resp = custom_providers_client.get("/api/v1/custom-providers/endpoints")
        assert resp.status_code == 200
        by_key = {e["key"]: e for e in resp.json()["endpoints"]}
        assert by_key["kling-video-pro-silent"]["end_image_capable"] is True
        assert by_key["openai-chat"]["end_image_capable"] is False
        assert by_key["openai-images"]["end_image_capable"] is False
        # Share registry truth; never introduce separate response-layer decisions.
        for key, spec in ENDPOINT_REGISTRY.items():
            assert by_key[key]["end_image_capable"] is spec.end_image_capable

    def test_endpoints_expose_image_capabilities(self, custom_providers_client: TestClient):
        """Each entry returns image_capabilities: capability arrays for images, None otherwise."""
        resp = custom_providers_client.get("/api/v1/custom-providers/endpoints")
        assert resp.status_code == 200
        by_key = {e["key"]: e for e in resp.json()["endpoints"]}
        assert by_key["openai-chat"]["image_capabilities"] is None
        assert sorted(by_key["openai-images"]["image_capabilities"]) == ["image_to_image", "text_to_image"]
        assert by_key["openai-images-generations"]["image_capabilities"] == ["text_to_image"]
        assert by_key["openai-images-edits"]["image_capabilities"] == ["image_to_image"]
        assert sorted(by_key["gemini-image"]["image_capabilities"]) == ["image_to_image", "text_to_image"]

    def test_endpoint_route_not_shadowed_by_provider_id(self, custom_providers_client: TestClient):
        """Register /endpoints before /{provider_id}, preventing integer provider_id parsing."""
        resp = custom_providers_client.get("/api/v1/custom-providers/endpoints")
        assert resp.status_code == 200, resp.text


class TestEndpointDefinition:
    """GET /endpoints/{key}/definition retrieves built-in declarative definitions for unchanged Copy to mine submissions."""

    def test_returns_the_definition_verbatim(
        self, custom_providers_client: TestClient, monkeypatch: pytest.MonkeyPatch
    ):
        definition = _example_template_definition()
        monkeypatch.setitem(ENDPOINT_REGISTRY, "demo-video", declarative_endpoint_spec("demo-video", definition))
        resp = custom_providers_client.get("/api/v1/custom-providers/endpoints/demo-video/definition")
        assert resp.status_code == 200, resp.text
        assert resp.json() == definition

    def test_python_endpoint_has_no_definition(self, custom_providers_client: TestClient):
        resp = custom_providers_client.get("/api/v1/custom-providers/endpoints/openai-video/definition")
        assert resp.status_code == 404

    def test_unknown_key_returns_404(self, custom_providers_client: TestClient):
        resp = custom_providers_client.get("/api/v1/custom-providers/endpoints/nope/definition")
        assert resp.status_code == 404


class TestGetProvider:
    def test_returns_provider(self, custom_providers_client: TestClient):
        create_resp = custom_providers_client.post(
            "/api/v1/custom-providers",
            json={
                "display_name": "My Provider",
                "discovery_format": "openai",
                "base_url": "https://api.example.com/v1",
                "api_key": "sk-get-test-12345678",
                "models": [
                    {
                        "model_id": "gpt-4o",
                        "display_name": "GPT-4o",
                        "endpoint": "openai-chat",
                    }
                ],
            },
        )
        pid = create_resp.json()["id"]
        resp = custom_providers_client.get(f"/api/v1/custom-providers/{pid}")
        assert resp.status_code == 200
        body = resp.json()
        assert body["display_name"] == "My Provider"
        assert len(body["models"]) == 1

    def test_returns_404_for_nonexistent(self, custom_providers_client: TestClient):
        resp = custom_providers_client.get("/api/v1/custom-providers/9999")
        assert resp.status_code == 404


class TestUpdateProvider:
    def test_update_display_name(self, custom_providers_client: TestClient):
        create_resp = custom_providers_client.post(
            "/api/v1/custom-providers",
            json={
                "display_name": "Old Name",
                "discovery_format": "openai",
                "base_url": "https://api.example.com/v1",
                "api_key": "sk-update-test-1234",
            },
        )
        pid = create_resp.json()["id"]
        resp = custom_providers_client.patch(
            f"/api/v1/custom-providers/{pid}",
            json={"display_name": "New Name"},
        )
        assert resp.status_code == 200
        assert resp.json()["display_name"] == "New Name"

    def test_update_api_key_is_masked(self, custom_providers_client: TestClient):
        create_resp = custom_providers_client.post(
            "/api/v1/custom-providers",
            json={
                "display_name": "Key Test",
                "discovery_format": "openai",
                "base_url": "https://api.example.com/v1",
                "api_key": "sk-old-key-12345678",
            },
        )
        pid = create_resp.json()["id"]
        resp = custom_providers_client.patch(
            f"/api/v1/custom-providers/{pid}",
            json={"api_key": "sk-new-key-87654321"},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert "sk-new-key-87654321" not in body["api_key_masked"]
        assert body["api_key_masked"].startswith("sk-n")

    def test_returns_404_for_nonexistent(self, custom_providers_client: TestClient):
        resp = custom_providers_client.patch(
            "/api/v1/custom-providers/9999",
            json={"display_name": "Nope"},
        )
        assert resp.status_code == 404

    def test_returns_400_for_empty_body(self, custom_providers_client: TestClient):
        create_resp = custom_providers_client.post(
            "/api/v1/custom-providers",
            json={
                "display_name": "Empty Update",
                "discovery_format": "openai",
                "base_url": "https://api.example.com/v1",
                "api_key": "sk-empty-test-1234",
            },
        )
        pid = create_resp.json()["id"]
        resp = custom_providers_client.patch(f"/api/v1/custom-providers/{pid}", json={})
        assert resp.status_code == 400


class TestDeleteProvider:
    def test_delete_existing(self, custom_providers_client: TestClient):
        create_resp = custom_providers_client.post(
            "/api/v1/custom-providers",
            json={
                "display_name": "To Delete",
                "discovery_format": "openai",
                "base_url": "https://api.example.com/v1",
                "api_key": "sk-delete-key-1234",
            },
        )
        pid = create_resp.json()["id"]
        resp = custom_providers_client.delete(f"/api/v1/custom-providers/{pid}")
        assert resp.status_code == 204

        # Verify it's gone
        get_resp = custom_providers_client.get(f"/api/v1/custom-providers/{pid}")
        assert get_resp.status_code == 404

    def test_returns_404_for_nonexistent(self, custom_providers_client: TestClient):
        resp = custom_providers_client.delete("/api/v1/custom-providers/9999")
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Discover models (mock)
# ---------------------------------------------------------------------------


class TestDiscoverModels:
    def test_discover_openai(self, custom_providers_client: TestClient):
        fake_models = [
            {
                "model_id": "gpt-4",
                "display_name": "gpt-4",
                "endpoint": "openai-chat",
                "is_default": True,
                "is_enabled": True,
            },
        ]
        with patch(
            "lib.custom_provider.discovery.discover_models",
            new_callable=AsyncMock,
            return_value=fake_models,
        ):
            resp = custom_providers_client.post(
                "/api/v1/custom-providers/discover",
                json={
                    "discovery_format": "openai",
                    "base_url": "https://api.example.com/v1",
                    "api_key": "sk-discover-test",
                },
            )
        assert resp.status_code == 200
        assert len(resp.json()["models"]) == 1
        assert resp.json()["models"][0]["model_id"] == "gpt-4"

    def test_discover_refuses_a_metadata_destination_with_a_redacted_reason(self, custom_providers_client: TestClient):
        """Rejected outbound destinations return 502 with truncated/redacted failures, sharing upstream-error exits."""
        with capture_http() as router:
            route = router.get("http://169.254.169.254/v1/models").respond(json={"data": []})
            resp = custom_providers_client.post(
                "/api/v1/custom-providers/discover",
                json={"discovery_format": "anthropic", "base_url": "http://169.254.169.254", "api_key": "sk-secret"},
            )
        assert route.call_count == 0
        assert resp.status_code == 502
        detail = resp.json()["detail"]
        assert "disallowed address" in detail
        assert "sk-secret" not in detail

    def test_discover_google(self, custom_providers_client: TestClient):
        """Forward google discovery_format to discover_models."""
        fake_models = [
            {
                "model_id": "gemini-2.0-flash",
                "display_name": "gemini-2.0-flash",
                "endpoint": "gemini-generate",
                "is_default": True,
                "is_enabled": True,
            },
        ]
        with patch(
            "lib.custom_provider.discovery.discover_models",
            new_callable=AsyncMock,
            return_value=fake_models,
        ) as mock_discover:
            resp = custom_providers_client.post(
                "/api/v1/custom-providers/discover",
                json={
                    "discovery_format": "google",
                    "base_url": "https://generativelanguage.googleapis.com",
                    "api_key": "AIza-google-discover",
                },
            )
        assert resp.status_code == 200
        assert resp.json()["models"][0]["model_id"] == "gemini-2.0-flash"
        # Confirm discovery_format forwarding.
        assert mock_discover.call_args.kwargs["discovery_format"] == "google"

    def test_discover_rejects_credential_bearing_base_url(self, custom_providers_client: TestClient):
        """Reject anthropic base_url query/userinfo credentials before requests; returned text excludes credentials."""
        base_url = "https://ant-user:sk-leaked-userinfo@relay.example.com/anthropic?api_key=sk-leaked-query"
        with capture_http() as http:
            route = http.get(host="relay.example.com").respond(status_code=401, text="unauthorized")
            resp = custom_providers_client.post(
                "/api/v1/custom-providers/discover",
                json={
                    "discovery_format": "anthropic",
                    "base_url": base_url,
                    "api_key": "sk-ant",
                },
            )

        assert route.call_count == 0
        assert resp.status_code == 422
        detail = resp.json()["detail"]
        assert "sk-leaked-userinfo" not in detail
        assert "sk-leaked-query" not in detail

    def test_discover_invalid_format(self, custom_providers_client: TestClient):
        """UnsupportedDiscoveryFormatError from discover_models returns 400."""
        from lib.custom_provider.discovery import UnsupportedDiscoveryFormatError

        with patch(
            "lib.custom_provider.discovery.discover_models",
            new_callable=AsyncMock,
            side_effect=UnsupportedDiscoveryFormatError("不支持的 discovery_format: 'unknown'"),
        ):
            resp = custom_providers_client.post(
                "/api/v1/custom-providers/discover",
                json={
                    "discovery_format": "openai",
                    "base_url": "https://api.example.com/v1",
                    "api_key": "sk-test",
                },
            )
        assert resp.status_code == 400

    def test_discover_sdk_value_error_returns_502_not_400(self, custom_providers_client: TestClient):
        """Ordinary SDK validation ValueError, such as rejected Google credentials, must not be misclassified as format errors: 502."""
        with patch(
            "lib.custom_provider.discovery.discover_models",
            new_callable=AsyncMock,
            side_effect=ValueError("Invalid API key provided"),
        ):
            resp = custom_providers_client.post(
                "/api/v1/custom-providers/discover",
                json={
                    "discovery_format": "google",
                    "base_url": "https://generativelanguage.googleapis.com",
                    "api_key": "bad-key",
                },
            )
        assert resp.status_code == 502

    def test_discover_api_failure(self, custom_providers_client: TestClient):
        with patch(
            "lib.custom_provider.discovery.discover_models",
            new_callable=AsyncMock,
            side_effect=RuntimeError("Connection refused"),
        ):
            resp = custom_providers_client.post(
                "/api/v1/custom-providers/discover",
                json={
                    "discovery_format": "openai",
                    "base_url": "https://api.example.com/v1",
                    "api_key": "sk-test",
                },
            )
        assert resp.status_code == 502


class TestDiscoverModelsByStoredProvider:
    """Editing saved providers cannot resubmit plaintext api_key; use stored credentials through by-id endpoints."""

    def _create(self, custom_providers_client: TestClient) -> int:
        resp = custom_providers_client.post(
            "/api/v1/custom-providers",
            json={
                "display_name": "Stored Cred Provider",
                "discovery_format": "openai",
                "base_url": "https://api.example.com/v1",
                "api_key": "sk-stored-discover-1234",
            },
        )
        return resp.json()["id"]

    def test_uses_stored_credentials(self, custom_providers_client: TestClient):
        """By-id discovery forwards stored discovery_format/base_url/api_key to discover_models."""
        pid = self._create(custom_providers_client)
        fake_models = [
            {
                "model_id": "gpt-4",
                "display_name": "gpt-4",
                "endpoint": "openai-chat",
                "is_default": True,
                "is_enabled": True,
            }
        ]
        with patch(
            "lib.custom_provider.discovery.discover_models",
            new_callable=AsyncMock,
            return_value=fake_models,
        ) as mock_discover:
            resp = custom_providers_client.post(f"/api/v1/custom-providers/{pid}/discover")
        assert resp.status_code == 200
        assert resp.json()["models"][0]["model_id"] == "gpt-4"
        # Verify stored credentials forward plaintext api_key, never masked values.
        kwargs = mock_discover.call_args.kwargs
        assert kwargs["discovery_format"] == "openai"
        assert kwargs["base_url"] == "https://api.example.com/v1"
        assert kwargs["api_key"] == "sk-stored-discover-1234"

    def test_returns_404_for_nonexistent(self, custom_providers_client: TestClient):
        resp = custom_providers_client.post("/api/v1/custom-providers/9999/discover")
        assert resp.status_code == 404

    def test_upstream_failure_returns_502(self, custom_providers_client: TestClient):
        pid = self._create(custom_providers_client)
        with patch(
            "lib.custom_provider.discovery.discover_models",
            new_callable=AsyncMock,
            side_effect=RuntimeError("Connection refused"),
        ):
            resp = custom_providers_client.post(f"/api/v1/custom-providers/{pid}/discover")
        assert resp.status_code == 502


# ---------------------------------------------------------------------------
# Connection test (mock)
# ---------------------------------------------------------------------------


def _openai_models_page(count: int) -> dict[str, object]:
    """OpenAI ``GET /models`` response-body shape."""
    return {"object": "list", "data": [{"id": f"m{i}", "object": "model"} for i in range(count)]}


def _google_models_page(count: int) -> dict[str, object]:
    """Google genai ``GET /v1beta/models`` response-body shape."""
    return {"models": [{"name": f"models/m{i}"} for i in range(count)]}


class TestConnectionTest:
    def test_openai_success(self, custom_providers_client: TestClient):
        with capture_http(assert_all_called=True) as http:
            http.get("https://api.example.com/v1/models").respond(json=_openai_models_page(5))
            resp = custom_providers_client.post(
                "/api/v1/custom-providers/test",
                json={
                    "discovery_format": "openai",
                    "base_url": "https://api.example.com/v1",
                    "api_key": "sk-conn-test",
                },
            )
        assert resp.status_code == 200
        body = resp.json()
        assert body["success"] is True
        assert body["model_count"] == 5

    def test_google_success(self, custom_providers_client: TestClient):
        with capture_http(assert_all_called=True) as http:
            http.get("https://api.example.com/v1beta/models").respond(json=_google_models_page(10))
            resp = custom_providers_client.post(
                "/api/v1/custom-providers/test",
                json={
                    "discovery_format": "google",
                    "base_url": "https://api.example.com",
                    "api_key": "AIza-test",
                },
            )
        assert resp.status_code == 200
        body = resp.json()
        assert body["success"] is True
        assert body["model_count"] == 10

    def test_openai_format_probes_openai_endpoint(self, custom_providers_client: TestClient):
        """discovery_format=openai probes OpenAI: outbound /models with Bearer credentials."""
        with capture_http(assert_all_called=True) as http:
            route = http.get("https://openai.example.com/v1/models").respond(json=_openai_models_page(42))
            resp = custom_providers_client.post(
                "/api/v1/custom-providers/test",
                json={
                    "discovery_format": "openai",
                    "base_url": "https://openai.example.com/v1",
                    "api_key": "sk-openai-conn",
                },
            )
        assert resp.status_code == 200
        body = resp.json()
        assert body["success"] is True
        assert body["model_count"] == 42
        assert only_request(route).headers["authorization"] == "Bearer sk-openai-conn"

    def test_unsupported_format_returns_false(self, custom_providers_client: TestClient):
        """Unsupported discovery_format returns success=False."""
        resp = custom_providers_client.post(
            "/api/v1/custom-providers/test",
            json={
                "discovery_format": "unsupported",
                "base_url": "https://api.example.com",
                "api_key": "test",
            },
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["success"] is False

    def test_connection_failure(self, custom_providers_client: TestClient):
        """Probe exceptions soft-fail endpoints: still 200, success=False with upstream error summaries."""
        with capture_http(assert_all_called=True) as http:
            http.get("https://api.example.com/v1/models").mock(side_effect=httpx.ConnectError("Connection refused"))
            resp = custom_providers_client.post(
                "/api/v1/custom-providers/test",
                json={
                    "discovery_format": "openai",
                    "base_url": "https://api.example.com/v1",
                    "api_key": "sk-fail-test",
                },
            )
        assert resp.status_code == 200
        body = resp.json()
        assert body["success"] is False
        assert "Connection error" in body["message"]


# ---------------------------------------------------------------------------
# Whole-provider writes, deletions and reference cleanup
# ---------------------------------------------------------------------------

_PROVIDER_PAYLOAD = {
    "display_name": "Regression Test",
    "discovery_format": "openai",
    "base_url": "https://api.example.com/v1",
    "api_key": "sk-regression-1234",
    "models": [
        {
            "model_id": "gpt-4o",
            "display_name": "GPT-4o",
            "endpoint": "openai-chat",
            "is_default": True,
            "is_enabled": True,
        },
        {
            "model_id": "dall-e-3",
            "display_name": "DALL-E 3",
            "endpoint": "openai-images",
            "is_default": True,
            "is_enabled": True,
        },
    ],
}


class TestDeleteProviderCleansGlobalSettings:
    """Provider deletion clears referencing default_*_backend values in global DBs."""

    async def test_global_settings_cleaned_on_delete(
        self, custom_providers_client: TestClient, db_session: AsyncSession
    ):
        # Create providers.
        resp = custom_providers_client.post("/api/v1/custom-providers", json=_PROVIDER_PAYLOAD)
        pid = resp.json()["id"]

        # Simulate global configuration referencing providers.
        svc = ConfigService(db_session)
        await svc.set_setting("default_text_backend", f"custom-{pid}/gpt-4o")
        await svc.set_setting("default_image_backend", f"custom-{pid}/dall-e-3")
        await svc.set_setting("default_audio_backend", f"custom-{pid}/tts-1")
        await svc.set_setting("default_video_backend", "gemini-aistudio/veo-3")  # Must not be cleaned up.
        await db_session.commit()

        # Real project cleanup with empty isolated-root project lists leaves global-settings cleanup only.
        del_resp = custom_providers_client.delete(f"/api/v1/custom-providers/{pid}")
        assert del_resp.status_code == 204

        # Verify reference cleanup.
        assert await svc.get_setting("default_text_backend", "") == ""
        assert await svc.get_setting("default_image_backend", "") == ""
        assert await svc.get_setting("default_audio_backend", "") == ""
        # Retain unrelated settings.
        assert await svc.get_setting("default_video_backend", "") == "gemini-aistudio/veo-3"


class TestDeleteProviderCleansProjectRefs:
    """Provider deletion clears dangling project-level project.json references."""

    def test_project_refs_cleaned_on_delete(self, custom_providers_client: TestClient, monkeypatch):
        resp = custom_providers_client.post("/api/v1/custom-providers", json=_PROVIDER_PAYLOAD)
        pid = resp.json()["id"]
        prefix = f"custom-{pid}/"

        # Simulate ProjectManager.
        mock_pm = MagicMock()
        mock_pm.list_projects.return_value = ["project-a"]
        project_data = {"text_backend_complex": f"{prefix}gpt-4o", "title": "Test"}
        mock_pm.load_project.return_value = project_data

        use_project_manager(monkeypatch, mock_pm)
        del_resp = custom_providers_client.delete(f"/api/v1/custom-providers/{pid}")
        assert del_resp.status_code == 204

        # Verify update_project calls clean references.
        mock_pm.update_project.assert_called_once()
        call_args = mock_pm.update_project.call_args
        assert call_args[0][0] == "project-a"
        # Execute mutate_fn to verify project media-override cleanup, whose keys differ from global settings.
        mutate_fn = call_args[0][1]
        test_proj = {
            "text_backend_complex": f"{prefix}gpt-4o",
            "default_text_backend": f"{prefix}gpt-4o-mini",
            "video_backend": f"{prefix}sora-2",
            "audio_backend": f"{prefix}tts-1",
            "image_provider_t2i": "gemini-aistudio/gemini-3.1-flash-image-preview",  # Other providers remain.
            "title": "Test",
        }
        mutate_fn(test_proj)
        assert "text_backend_complex" not in test_proj
        assert "default_text_backend" not in test_proj
        assert "video_backend" not in test_proj
        assert "audio_backend" not in test_proj
        assert test_proj["image_provider_t2i"].startswith("gemini-aistudio/")  # Retain other-provider references.
        assert test_proj["title"] == "Test"  # Retain unrelated fields.


class TestFullUpdateCleansStaleModelRefs:
    """Settings PUT /{provider_id} replaces whole model tables; clear global-default keys referencing deleted models."""

    async def test_only_refs_to_deleted_models_of_this_provider_are_cleared(
        self, custom_providers_client: TestClient, db_session: AsyncSession
    ):
        pid = custom_providers_client.post("/api/v1/custom-providers", json=_PROVIDER_PAYLOAD).json()["id"]
        other_pid = custom_providers_client.post("/api/v1/custom-providers", json=_PROVIDER_PAYLOAD).json()["id"]

        svc = ConfigService(db_session)
        await svc.set_setting("default_text_backend", f"custom-{pid}/gpt-4o")
        await svc.set_setting("default_image_backend", f"custom-{pid}/dall-e-3")
        # Same model_id under another provider
        await svc.set_setting("text_backend_simple", f"custom-{other_pid}/gpt-4o")
        # Resolution gates reject dangling video-bucket references; writes never cascade cleanup.
        await svc.set_setting("default_video_backend_i2v", f"custom-{pid}/gpt-4o")
        await db_session.commit()

        # Save removes gpt-4o, retaining dall-e-3.
        resp = custom_providers_client.put(
            f"/api/v1/custom-providers/{pid}",
            json={
                "display_name": "Regression Test",
                "base_url": "https://api.example.com/v1",
                "models": [
                    {
                        "model_id": "dall-e-3",
                        "display_name": "DALL-E 3",
                        "endpoint": "openai-images",
                        "is_default": True,
                        "is_enabled": True,
                    },
                ],
            },
        )
        assert resp.status_code == 200

        assert await svc.get_setting("default_text_backend", "") == ""
        assert await svc.get_setting("default_image_backend", "") == f"custom-{pid}/dall-e-3"
        assert await svc.get_setting("text_backend_simple", "") == f"custom-{other_pid}/gpt-4o"
        assert await svc.get_setting("default_video_backend_i2v", "") == f"custom-{pid}/gpt-4o"


class TestEmptyModelIdRejected:
    """Enabled models require nonempty model_id."""

    def test_create_with_empty_model_id(self, custom_providers_client: TestClient):
        resp = custom_providers_client.post(
            "/api/v1/custom-providers",
            json={
                "display_name": "Bad Provider",
                "discovery_format": "openai",
                "base_url": "https://api.example.com/v1",
                "api_key": "sk-bad",
                "models": [
                    {"model_id": "", "display_name": "Empty", "endpoint": "openai-chat", "is_enabled": True},
                ],
            },
        )
        assert resp.status_code == 422

    def test_full_update_with_whitespace_model_id(self, custom_providers_client: TestClient):
        create_resp = custom_providers_client.post("/api/v1/custom-providers", json=_PROVIDER_PAYLOAD)
        pid = create_resp.json()["id"]
        resp = custom_providers_client.put(
            f"/api/v1/custom-providers/{pid}",
            json={
                "display_name": "Regression Test",
                "base_url": "https://api.example.com/v1",
                "models": [
                    {"model_id": "  ", "display_name": "Blank", "endpoint": "openai-chat", "is_enabled": True},
                ],
            },
        )
        assert resp.status_code == 422


class TestUnknownEndpointRejected:
    """AfterValidator rejects unregistered endpoint keys on writes with 422."""

    def test_create_with_unknown_endpoint(self, custom_providers_client: TestClient):
        resp = custom_providers_client.post(
            "/api/v1/custom-providers",
            json={
                "display_name": "Unknown Endpoint",
                "discovery_format": "openai",
                "base_url": "https://api.example.com/v1",
                "api_key": "sk-key",
                "models": [
                    {
                        "model_id": "m1",
                        "display_name": "M",
                        "endpoint": "acme-unknown",
                        "is_enabled": True,
                    },
                ],
            },
        )
        assert resp.status_code == 422
        assert "unknown endpoint" in resp.text


class TestDuplicateModelIdRejected:
    """Duplicate model_id values are forbidden within providers."""

    def test_create_with_duplicate(self, custom_providers_client: TestClient):
        resp = custom_providers_client.post(
            "/api/v1/custom-providers",
            json={
                "display_name": "Dup Provider",
                "discovery_format": "openai",
                "base_url": "https://api.example.com/v1",
                "api_key": "sk-dup",
                "models": [
                    {"model_id": "m1", "display_name": "M1a", "endpoint": "openai-chat", "is_enabled": True},
                    {"model_id": "m1", "display_name": "M1b", "endpoint": "openai-chat", "is_enabled": True},
                ],
            },
        )
        assert resp.status_code == 422
        assert "重复" in resp.json()["detail"]


class TestFullUpdateProvider:
    """Full PUT updates must atomically update provider + models."""

    def test_full_update(self, custom_providers_client: TestClient):
        create_resp = custom_providers_client.post("/api/v1/custom-providers", json=_PROVIDER_PAYLOAD)
        pid = create_resp.json()["id"]
        resp = custom_providers_client.put(
            f"/api/v1/custom-providers/{pid}",
            json={
                "display_name": "Updated Name",
                "base_url": "https://new-api.example.com/v1",
                "models": [
                    {
                        "model_id": "new-model",
                        "display_name": "New",
                        "endpoint": "openai-chat",
                        "is_default": True,
                        "is_enabled": True,
                    },
                ],
            },
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["display_name"] == "Updated Name"
        assert body["base_url"] == "https://new-api.example.com/v1"
        assert len(body["models"]) == 1
        assert body["models"][0]["model_id"] == "new-model"

    def test_full_update_rejects_empty_model_id(self, custom_providers_client: TestClient):
        create_resp = custom_providers_client.post("/api/v1/custom-providers", json=_PROVIDER_PAYLOAD)
        pid = create_resp.json()["id"]
        resp = custom_providers_client.put(
            f"/api/v1/custom-providers/{pid}",
            json={
                "display_name": "X",
                "base_url": "https://x.com",
                "models": [
                    {"model_id": "", "display_name": "Bad", "endpoint": "openai-chat", "is_enabled": True},
                ],
            },
        )
        assert resp.status_code == 422

    def test_full_update_without_models_keeps_models(self, custom_providers_client: TestClient):
        """Details save models individually through provider-model APIs; provider-setting saves omit models, retaining existing models unchanged."""
        create_resp = custom_providers_client.post("/api/v1/custom-providers", json=_PROVIDER_PAYLOAD)
        pid = create_resp.json()["id"]
        before = [(m["model_id"], m["is_enabled"]) for m in create_resp.json()["models"]]

        resp = custom_providers_client.put(
            f"/api/v1/custom-providers/{pid}",
            json={"display_name": "Renamed", "base_url": "https://new-api.example.com/v1"},
        )

        assert resp.status_code == 200
        assert resp.json()["display_name"] == "Renamed"
        assert [(m["model_id"], m["is_enabled"]) for m in resp.json()["models"]] == before

    def test_full_update_404_for_nonexistent(self, custom_providers_client: TestClient):
        resp = custom_providers_client.put(
            "/api/v1/custom-providers/9999",
            json={
                "display_name": "X",
                "base_url": "https://x.com",
                "models": [],
            },
        )
        assert resp.status_code == 404


class TestConcurrencyFields:
    """image/video/audio_max_workers save/echo through POST / PUT: blank → null, 0/negative → 422."""

    def test_create_echoes_workers(self, custom_providers_client: TestClient):
        resp = custom_providers_client.post(
            "/api/v1/custom-providers",
            json={
                "display_name": "P",
                "discovery_format": "openai",
                "base_url": "https://x.com",
                "api_key": "sk-test-key-12345678",
                "models": [],
                "image_max_workers": 2,
                "video_max_workers": 7,
                "audio_max_workers": 1,
            },
        )
        assert resp.status_code == 201
        body = resp.json()
        assert body["image_max_workers"] == 2
        assert body["video_max_workers"] == 7
        assert body["audio_max_workers"] == 1

    def test_create_defaults_to_null_when_omitted(self, custom_providers_client: TestClient):
        resp = custom_providers_client.post(
            "/api/v1/custom-providers",
            json={
                "display_name": "P",
                "discovery_format": "openai",
                "base_url": "https://x.com",
                "api_key": "sk-test-key-12345678",
                "models": [],
            },
        )
        assert resp.status_code == 201
        body = resp.json()
        assert body["image_max_workers"] is None
        assert body["video_max_workers"] is None
        assert body["audio_max_workers"] is None

    @pytest.mark.parametrize("bad_value", [-1, 0])
    def test_create_rejects_below_one(self, custom_providers_client: TestClient, bad_value: int):
        resp = custom_providers_client.post(
            "/api/v1/custom-providers",
            json={
                "display_name": "P",
                "discovery_format": "openai",
                "base_url": "https://x.com",
                "api_key": "sk-test-key-12345678",
                "models": [],
                "image_max_workers": bad_value,
            },
        )
        assert resp.status_code == 422

    def test_full_update_overwrites_and_clears(self, custom_providers_client: TestClient):
        create_resp = custom_providers_client.post(
            "/api/v1/custom-providers",
            json={
                "display_name": "P",
                "discovery_format": "openai",
                "base_url": "https://x.com",
                "api_key": "sk-test-key-12345678",
                "models": [],
                "image_max_workers": 3,
                "video_max_workers": 4,
            },
        )
        pid = create_resp.json()["id"]
        # PUT omitting image_max_workers means null, authoritative clearing; override video to 9.
        resp = custom_providers_client.put(
            f"/api/v1/custom-providers/{pid}",
            json={
                "display_name": "P",
                "base_url": "https://x.com",
                "models": [],
                "video_max_workers": 9,
            },
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["image_max_workers"] is None
        assert body["video_max_workers"] == 9
        assert body["audio_max_workers"] is None


class TestValidateBackendValueCustomPrefix:
    """validate_backend_value must accept custom-* prefixes."""

    def test_custom_prefix_accepted(self):
        from lib.infra.api_errors import BadRequestError
        from server.routers._validators import validate_backend_value

        # custom- prefixes absent from PROVIDER_REGISTRY still pass; provider APIs gate per-model capabilities.
        with pytest.raises(BadRequestError):
            validate_backend_value("custom3/gpt-4o", "default_text_backend")
        assert validate_backend_value("custom-3/gpt-4o", "default_text_backend") is None

    def test_unknown_provider_rejected(self):
        from lib.infra.api_errors import BadRequestError
        from server.routers._validators import validate_backend_value

        with pytest.raises(BadRequestError) as exc_info:
            validate_backend_value("nonexistent/model", "default_text_backend")
        assert exc_info.value.status_code == 400
        # Field names enter diagnostics only, never user-facing summaries.
        assert exc_info.value.diagnostic == "field: default_text_backend"


class TestDuplicateDefaultRejected:
    """At most one is_default=True model per media_type."""

    def test_create_with_duplicate_defaults(self, custom_providers_client: TestClient):
        """Creating two is_default=true models of one media_type expects 422."""
        resp = custom_providers_client.post(
            "/api/v1/custom-providers",
            json={
                "display_name": "Dup Default Provider",
                "discovery_format": "openai",
                "base_url": "https://api.example.com/v1",
                "api_key": "sk-dup-default-1234",
                "models": [
                    {
                        "model_id": "text-a",
                        "display_name": "Text A",
                        "endpoint": "openai-chat",
                        "is_default": True,
                        "is_enabled": True,
                    },
                    {
                        "model_id": "text-b",
                        "display_name": "Text B",
                        "endpoint": "openai-chat",
                        "is_default": True,
                        "is_enabled": True,
                    },
                ],
            },
        )
        assert resp.status_code == 422
        assert "默认模型" in resp.json()["detail"]

    def test_single_default_per_type_allowed(self, custom_providers_client: TestClient):
        """One default per distinct media_type expects successful 201."""
        resp = custom_providers_client.post(
            "/api/v1/custom-providers",
            json={
                "display_name": "Multi Default Provider",
                "discovery_format": "openai",
                "base_url": "https://api.example.com/v1",
                "api_key": "sk-multi-default-12",
                "models": [
                    {
                        "model_id": "text-model",
                        "display_name": "Text Model",
                        "endpoint": "openai-chat",
                        "is_default": True,
                        "is_enabled": True,
                    },
                    {
                        "model_id": "image-model",
                        "display_name": "Image Model",
                        "endpoint": "openai-images",
                        "is_default": True,
                        "is_enabled": True,
                    },
                    {
                        "model_id": "video-model",
                        "display_name": "Video Model",
                        "endpoint": "newapi-video",
                        "is_default": True,
                        "is_enabled": True,
                    },
                ],
            },
        )
        assert resp.status_code == 201


class TestModelPricingFields:
    """Price models by matching entries: forms supply multipliers/bindings only; responses include matched entries (docs/adr/0100,0105,0106)."""

    @staticmethod
    def _create(client: TestClient, models: list[dict[str, Any]]):
        return client.post(
            "/api/v1/custom-providers",
            json={
                "display_name": "Relay",
                "discovery_format": "openai",
                "base_url": "https://relay.test/v1",
                "api_key": "sk-relay-12345678",
                "models": models,
            },
        )

    def test_multiplier_binding_and_matched_entry_round_trip(self, custom_providers_client: TestClient):
        resp = self._create(
            custom_providers_client,
            [
                {"model_id": "relay/GPT-5.4", "display_name": "GPT", "endpoint": "openai-chat", "multiplier": 0.8},
                {
                    "model_id": "renamed-llm",
                    "display_name": "Renamed",
                    "endpoint": "openai-chat",
                    "entry_binding": " openai/gpt-5.4 ",
                },
                {"model_id": "my-private-llm", "display_name": "Private", "endpoint": "openai-chat"},
            ],
        )

        assert resp.status_code == 201, resp.text
        detail = custom_providers_client.get(f"/api/v1/custom-providers/{resp.json()['id']}").json()
        models = {m["model_id"]: m for m in detail["models"]}
        assert (models["relay/GPT-5.4"]["multiplier"], models["relay/GPT-5.4"]["entry_binding"]) == (0.8, None)
        assert models["relay/GPT-5.4"]["matched_entry"] == {
            "maker": "openai",
            "name": "gpt-5.4",
            "media_type": "text",
            "display_name": models["relay/GPT-5.4"]["matched_entry"]["display_name"],
            "priced": True,
        }
        assert models["renamed-llm"]["entry_binding"] == "openai/gpt-5.4"
        assert models["renamed-llm"]["matched_entry"]["name"] == "gpt-5.4"
        assert models["my-private-llm"]["multiplier"] == 1.0
        assert models["my-private-llm"]["matched_entry"] is None

    @pytest.mark.parametrize(
        "field", [{"multiplier": -0.1}, {"entry_binding": "gpt-5.4"}, {"entry_binding": "/gpt-5.4"}]
    )
    def test_invalid_multiplier_or_binding_shape_is_rejected(self, custom_providers_client: TestClient, field):
        resp = self._create(
            custom_providers_client,
            [{"model_id": "m1", "display_name": "M1", "endpoint": "openai-chat", **field}],
        )

        assert resp.status_code == 422

    def test_a_binding_to_an_entry_no_longer_in_the_catalog_is_kept(self, custom_providers_client: TestClient):
        """Validate shape only: official entry removal must not make echoed legacy bindings block unrelated edits with 422."""
        resp = self._create(
            custom_providers_client,
            [{"model_id": "m1", "display_name": "M1", "endpoint": "openai-chat", "entry_binding": "acme/gone"}],
        )

        assert resp.status_code == 201
        model = resp.json()["models"][0]
        assert (model["entry_binding"], model["matched_entry"]) == ("acme/gone", None)

    def test_legacy_price_and_capability_fields_are_ignored(self, custom_providers_client: TestClient):
        """Legacy clients still submit prices, durations and maximum output tokens: ignore, never persist or echo."""
        resp = self._create(
            custom_providers_client,
            [
                {
                    "model_id": "m1",
                    "display_name": "M1",
                    "endpoint": "openai-chat",
                    "price_input": 0.1,
                    "price_output": 0.2,
                    "currency": "USD",
                    "max_output_tokens": 8192,
                }
            ],
        )

        assert resp.status_code == 201
        model = resp.json()["models"][0]
        assert {"price_input", "price_output", "currency", "price_unit", "max_output_tokens"}.isdisjoint(model)


class TestResolutionField:
    """Verify ModelInput / ModelResponse resolution read/write continuity."""

    def test_create_with_resolution_and_read_back(self, custom_providers_client: TestClient):
        resp = custom_providers_client.post(
            "/api/v1/custom-providers",
            json={
                "display_name": "X",
                "discovery_format": "openai",
                "base_url": "https://api.example.com",
                "api_key": "k",
                "models": [
                    {
                        "model_id": "m1",
                        "display_name": "M1",
                        "endpoint": "newapi-video",
                        "is_default": True,
                        "is_enabled": True,
                        "resolution": "720p",
                    },
                ],
            },
        )
        assert resp.status_code == 201
        pid = resp.json()["id"]

        # Read and confirm returned resolution.
        resp = custom_providers_client.get(f"/api/v1/custom-providers/{pid}")
        assert resp.status_code == 200
        models = resp.json()["models"]
        assert len(models) == 1
        assert models[0]["resolution"] == "720p"

    def test_resolution_defaults_to_null_when_omitted(self, custom_providers_client: TestClient):
        """Unspecified resolution returns None."""
        resp = custom_providers_client.post(
            "/api/v1/custom-providers",
            json={
                "display_name": "Y",
                "discovery_format": "openai",
                "base_url": "https://api.example.com",
                "api_key": "k",
                "models": [
                    {
                        "model_id": "m1",
                        "display_name": "M1",
                        "endpoint": "newapi-video",
                        "is_enabled": True,
                    },
                ],
            },
        )
        assert resp.status_code == 201
        pid = resp.json()["id"]

        resp = custom_providers_client.get(f"/api/v1/custom-providers/{pid}")
        assert resp.status_code == 200
        assert resp.json()["models"][0]["resolution"] is None

    def test_full_update_clears_resolution_to_null(self, custom_providers_client: TestClient):
        """PUT /{provider_id} full-table saves clear omitted resolution to null."""
        # First create providers with resolution.
        resp = custom_providers_client.post(
            "/api/v1/custom-providers",
            json={
                "display_name": "Z",
                "discovery_format": "openai",
                "base_url": "https://api.example.com",
                "api_key": "k",
                "models": [
                    {
                        "model_id": "m1",
                        "display_name": "M1",
                        "endpoint": "newapi-video",
                        "is_enabled": True,
                        "resolution": "1080p",
                    },
                ],
            },
        )
        assert resp.status_code == 201
        pid = resp.json()["id"]

        # Replace model lists; omitted resolution means null.
        resp = custom_providers_client.put(
            f"/api/v1/custom-providers/{pid}",
            json={
                "display_name": "Z",
                "base_url": "https://api.example.com",
                "models": [
                    {
                        "model_id": "m1",
                        "display_name": "M1",
                        "endpoint": "newapi-video",
                        "is_enabled": True,
                    },
                ],
            },
        )
        assert resp.status_code == 200

        # Read and verify null.
        resp = custom_providers_client.get(f"/api/v1/custom-providers/{pid}")
        assert resp.status_code == 200
        assert resp.json()["models"][0]["resolution"] is None


# ---------------------------------------------------------------------------
# Additional 422 validation tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_provider_with_unknown_endpoint_returns_422(custom_providers_client):
    payload = {
        "display_name": "X",
        "discovery_format": "openai",
        "base_url": "https://x",
        "api_key": "k",
        "models": [
            {
                "model_id": "claude-4",
                "display_name": "Claude 4",
                "endpoint": "acme-unknown",  # Invalid
                "is_default": False,
                "is_enabled": True,
            }
        ],
    }
    resp = custom_providers_client.post("/api/v1/custom-providers", json=payload)
    assert resp.status_code == 422
    assert "unknown_endpoint" in resp.text or "acme-unknown" in resp.text


@pytest.mark.asyncio
async def test_create_provider_unknown_discovery_format_returns_422(custom_providers_client):
    payload = {
        "display_name": "X",
        "discovery_format": "newapi",  # Removed already
        "base_url": "https://x",
        "api_key": "k",
        "models": [],
    }
    resp = custom_providers_client.post("/api/v1/custom-providers", json=payload)
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_default_conflict_grouped_by_endpoint_media(custom_providers_client):
    """Different endpoints deriving identical media_type cannot both have is_default."""
    payload = {
        "display_name": "X",
        "discovery_format": "openai",
        "base_url": "https://x",
        "api_key": "k",
        "models": [
            {
                "model_id": "gpt-4o",
                "display_name": "a",
                "endpoint": "openai-chat",
                "is_default": True,
                "is_enabled": True,
            },
            {
                "model_id": "gemini-2.5",
                "display_name": "b",
                "endpoint": "gemini-generate",
                "is_default": True,
                "is_enabled": True,
            },  # Both text → conflict
        ],
    }
    resp = custom_providers_client.post("/api/v1/custom-providers", json=payload)
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_split_image_endpoints_may_both_be_default(custom_providers_client):
    """Same-provider -generations and -edits defaults both allowed: capabilities do not overlap."""
    payload = {
        "display_name": "X",
        "discovery_format": "openai",
        "base_url": "https://x",
        "api_key": "k",
        "models": [
            {
                "model_id": "m1",
                "display_name": "m1",
                "endpoint": "openai-images-generations",
                "is_default": True,
                "is_enabled": True,
            },
            {
                "model_id": "m2",
                "display_name": "m2",
                "endpoint": "openai-images-edits",
                "is_default": True,
                "is_enabled": True,
            },
        ],
    }
    resp = custom_providers_client.post("/api/v1/custom-providers", json=payload)
    assert resp.status_code == 201, resp.text
    defaults = {model["model_id"]: model["is_default"] for model in resp.json()["models"]}
    assert defaults == {"m1": True, "m2": True}


def _builtin_specs(models) -> dict[str, EndpointSpec]:
    """Built-in endpoint specs, shaped like `_resolve_model_endpoint_specs` output."""
    return {m.endpoint: get_endpoint_spec(m.endpoint) for m in models}


def _custom_endpoint_spec(key: str, media_type: str) -> EndpointSpec:
    """A ce- endpoint spec with media types adjusted as needed; key prefixes cannot infer types, so specs must supply them."""
    spec = declarative_endpoint_spec(key, custom_endpoint_definition(), source="custom")
    return replace(spec, media_type=media_type)


def test_check_unique_defaults_reads_the_media_type_from_the_resolved_spec():
    """Conflicts between two ce- defaults depend on spec media types, never key prefixes."""
    from fastapi import HTTPException

    from server.routers.custom_providers import ModelInput, _check_unique_defaults

    models = [
        ModelInput(model_id="m1", display_name="m1", endpoint="ce-7", is_default=True),
        ModelInput(model_id="m2", display_name="m2", endpoint="ce-8", is_default=True),
    ]

    def t(key, **params):
        return f"{key}:{params}"

    same_lane = {"ce-7": _custom_endpoint_spec("ce-7", "audio"), "ce-8": _custom_endpoint_spec("ce-8", "audio")}
    with pytest.raises(HTTPException) as excinfo:
        _check_unique_defaults(models, same_lane, t)
    assert excinfo.value.status_code == 422

    split_lanes = {"ce-7": _custom_endpoint_spec("ce-7", "audio"), "ce-8": _custom_endpoint_spec("ce-8", "video")}
    _check_unique_defaults(models, split_lanes, t)


def test_check_unique_defaults_refuses_two_image_defaults_that_declare_no_capabilities():
    """Empty-capability image endpoints are indistinguishable; allowing two defaults makes default-model queries return two rows.

    Built-in lookup cannot find ``ce-`` capability flags; previously entire endpoints were skipped, excluding defaults from mutual-exclusion checks.
    """
    from fastapi import HTTPException

    from server.routers.custom_providers import ModelInput, _check_unique_defaults

    models = [
        ModelInput(model_id="m1", display_name="m1", endpoint="ce-7", is_default=True),
        ModelInput(model_id="m2", display_name="m2", endpoint="ce-8", is_default=True),
    ]
    specs = {"ce-7": _custom_endpoint_spec("ce-7", "image"), "ce-8": _custom_endpoint_spec("ce-8", "image")}

    def t(key, **params):
        return f"{key}:{params}"

    with pytest.raises(HTTPException) as excinfo:
        _check_unique_defaults(models, specs, t)

    assert excinfo.value.status_code == 422


def _comfyui_image_spec(key: str, *, reference_slots: int) -> EndpointSpec:
    """ComfyUI image endpoint specs: reference-image slots determine t2i versus i2i buckets."""
    from lib.custom_provider.endpoints import comfyui_endpoint_spec
    from tests.factories import comfyui_endpoint_definition

    definition = comfyui_endpoint_definition(media_type="image")
    if reference_slots:
        definition["workflow"]["20"] = {"class_type": "LoadImage", "inputs": {"image": "draft.png"}}
        definition["bindings"]["reference_images"] = [
            {"node": "20", "input": "image", "class_type": "LoadImage"}
        ] * reference_slots
    return comfyui_endpoint_spec(key, definition)


def test_check_unique_defaults_separates_two_comfyui_image_endpoints_by_capability():
    """One text-to-image and one image-to-image workflow may each default; disjoint capabilities do not conflict."""
    from server.routers.custom_providers import ModelInput, _check_unique_defaults

    models = [
        ModelInput(model_id="t2i", display_name="t2i", endpoint="ce-7", is_default=True),
        ModelInput(model_id="i2i", display_name="i2i", endpoint="ce-8", is_default=True),
    ]
    specs = {
        "ce-7": _comfyui_image_spec("ce-7", reference_slots=0),
        "ce-8": _comfyui_image_spec("ce-8", reference_slots=1),
    }

    _check_unique_defaults(models, specs, lambda key, **params: key)


def test_check_unique_defaults_rejects_two_comfyui_text_to_image_defaults():
    """Two text-to-image-only workflows create two defaults in one slot, returning two default-model rows."""
    from fastapi import HTTPException

    from server.routers.custom_providers import ModelInput, _check_unique_defaults

    models = [
        ModelInput(model_id="m1", display_name="m1", endpoint="ce-7", is_default=True),
        ModelInput(model_id="m2", display_name="m2", endpoint="ce-8", is_default=True),
    ]
    specs = {
        "ce-7": _comfyui_image_spec("ce-7", reference_slots=0),
        "ce-8": _comfyui_image_spec("ce-8", reference_slots=0),
    }

    with pytest.raises(HTTPException) as excinfo:
        _check_unique_defaults(models, specs, lambda key, **params: f"{key}:{params}")

    assert excinfo.value.status_code == 422


def test_check_unique_defaults_allows_one_image_default_without_capabilities():
    """A single such default has no indistinguishable counterpart and passes normally."""
    from server.routers.custom_providers import ModelInput, _check_unique_defaults

    models = [ModelInput(model_id="m1", display_name="m1", endpoint="ce-7", is_default=True)]

    _check_unique_defaults(models, {"ce-7": _custom_endpoint_spec("ce-7", "image")}, lambda key, **params: key)


def test_check_unique_defaults_rejects_two_generations_defaults():
    """Two same-provider -generations defaults return 422."""
    from fastapi import HTTPException

    from server.routers.custom_providers import ModelInput, _check_unique_defaults

    models = [
        ModelInput(model_id="m1", display_name="m1", endpoint="openai-images-generations", is_default=True),
        ModelInput(model_id="m2", display_name="m2", endpoint="openai-images-generations", is_default=True),
    ]

    def t(key, **params):
        return f"{key}:{params}"

    with pytest.raises(HTTPException) as excinfo:
        _check_unique_defaults(models, _builtin_specs(models), t)
    assert excinfo.value.status_code == 422


def test_check_unique_defaults_rejects_wildcard_with_split():
    """Wildcard plus -generations defaults forbidden: wildcard occupies T2I, conflicting with -generations."""
    from fastapi import HTTPException

    from server.routers.custom_providers import ModelInput, _check_unique_defaults

    models = [
        ModelInput(model_id="m1", display_name="m1", endpoint="openai-images", is_default=True),
        ModelInput(model_id="m2", display_name="m2", endpoint="openai-images-generations", is_default=True),
    ]

    def t(key, **params):
        return f"{key}:{params}"

    with pytest.raises(HTTPException):
        _check_unique_defaults(models, _builtin_specs(models), t)


def test_check_unique_defaults_text_still_media_type_exclusive():
    """text/video retain old rules: one default per media_type."""
    from fastapi import HTTPException

    from server.routers.custom_providers import ModelInput, _check_unique_defaults

    models = [
        ModelInput(model_id="m1", display_name="m1", endpoint="openai-chat", is_default=True),
        ModelInput(model_id="m2", display_name="m2", endpoint="gemini-generate", is_default=True),
    ]

    def t(key, **params):
        return f"{key}:{params}"

    with pytest.raises(HTTPException):
        _check_unique_defaults(models, _builtin_specs(models), t)


# ---------------------------------------------------------------------------
# Anthropic discovery, Agent configuration only
# ---------------------------------------------------------------------------


class TestDiscoverAnthropic:
    def test_explicit_credentials(self, custom_providers_client: TestClient):
        """Explicit base_url + api_key invokes _run_discover('anthropic', ...)."""
        mock_models = [
            {"model_id": "claude-x", "display_name": "X", "endpoint": "", "is_default": False, "is_enabled": True}
        ]
        with patch(
            "lib.custom_provider.discovery.discover_models",
            new_callable=AsyncMock,
            return_value=mock_models,
        ) as mock_discover:
            resp = custom_providers_client.post(
                "/api/v1/custom-providers/discover-anthropic",
                json={"base_url": "https://example.com", "api_key": "sk-ant"},
            )

        assert resp.status_code == 200
        assert [m["model_id"] for m in resp.json()["models"]] == ["claude-x"]
        # Fixed anthropic discovery format, forwarding credentials unchanged.
        kwargs = mock_discover.call_args.kwargs
        assert kwargs["discovery_format"] == "anthropic"
        assert kwargs["base_url"] == "https://example.com"
        assert kwargs["api_key"] == "sk-ant"

    async def test_falls_back_to_stored_api_key(self, custom_providers_client: TestClient, db_session: AsyncSession):
        """Requests without api_key fall back to active AgentAnthropicCredential."""
        from lib.db.repositories.agent_credential_repo import AgentCredentialRepository

        repo = AgentCredentialRepository(db_session)
        cred = await repo.create(
            preset_id="__custom__",
            display_name="stored",
            base_url="https://stored.example",
            api_key="sk-stored",
        )
        await repo.set_active(cred.id)
        await db_session.commit()

        with patch(
            "lib.custom_provider.discovery.discover_models",
            new_callable=AsyncMock,
            return_value=[],
        ) as mock_discover:
            resp = custom_providers_client.post("/api/v1/custom-providers/discover-anthropic", json={})

        assert resp.status_code == 200
        kwargs = mock_discover.call_args.kwargs
        assert kwargs["base_url"] == "https://stored.example"
        assert kwargs["api_key"] == "sk-stored"

    @pytest.mark.parametrize(
        ("stored_base_url", "expected_discovery_base"),
        [
            # Preset defaults are not overrides; model lists use preset discovery_url.
            ("https://api.deepseek.com/anthropic", "https://api.deepseek.com"),
            # User-overridden base_url discovery uses stored values.
            ("https://proxy.internal/anthropic", "https://proxy.internal/anthropic"),
        ],
    )
    async def test_active_preset_credential_discovers_from_preset_root_unless_overridden(
        self,
        custom_providers_client: TestClient,
        db_session: AsyncSession,
        stored_base_url: str,
        expected_discovery_base: str,
    ):
        from lib.db.repositories.agent_credential_repo import AgentCredentialRepository

        repo = AgentCredentialRepository(db_session)
        cred = await repo.create(
            preset_id="deepseek",
            display_name="DeepSeek",
            base_url=stored_base_url,
            api_key="sk-stored",
        )
        await repo.set_active(cred.id)
        await db_session.commit()

        with patch(
            "lib.custom_provider.discovery.discover_models",
            new_callable=AsyncMock,
            return_value=[],
        ) as mock_discover:
            resp = custom_providers_client.post("/api/v1/custom-providers/discover-anthropic", json={})

        assert resp.status_code == 200
        assert mock_discover.call_args.kwargs["base_url"] == expected_discovery_base

    @pytest.mark.parametrize(
        "base_url",
        [
            "https://relay.example.com/anthropic?api_key=sk-x",
            "https://x/a#frag",
            "https://u:p@x/a",
            "x.example.com",
        ],
    )
    def test_rejects_unsupported_base_url(self, custom_providers_client: TestClient, base_url: str):
        """Agent discovery rejects query / fragment / userinfo or missing schemes, never echoing query values."""
        resp = custom_providers_client.post(
            "/api/v1/custom-providers/discover-anthropic",
            json={"base_url": base_url, "api_key": "sk-ant"},
        )
        assert resp.status_code == 422
        assert "sk-x" not in resp.text

    def test_returns_400_when_no_key_anywhere(self, custom_providers_client: TestClient):
        """Missing request api_key and database credentials returns 400."""
        resp = custom_providers_client.post("/api/v1/custom-providers/discover-anthropic", json={})
        assert resp.status_code == 400
        # i18n defaults to zh.
        assert "API Key" in resp.json()["detail"]

    async def test_whitespace_only_api_key_falls_back_to_stored(
        self, custom_providers_client: TestClient, db_session: AsyncSession
    ):
        """Whitespace-only body.api_key counts as absent, falling back to active credentials instead of forwarding whitespace keys."""
        from lib.db.repositories.agent_credential_repo import AgentCredentialRepository

        repo = AgentCredentialRepository(db_session)
        cred = await repo.create(
            preset_id="__custom__",
            display_name="stored",
            base_url="https://stored.example",
            api_key="sk-stored",
        )
        await repo.set_active(cred.id)
        await db_session.commit()

        with patch(
            "lib.custom_provider.discovery.discover_models",
            new_callable=AsyncMock,
            return_value=[],
        ) as mock_discover:
            resp = custom_providers_client.post(
                "/api/v1/custom-providers/discover-anthropic",
                json={"api_key": "   "},
            )

        assert resp.status_code == 200
        # Upstream receives stored keys, not request whitespace.
        assert mock_discover.call_args.kwargs["api_key"] == "sk-stored"


class TestProviderSecretReadback:
    def test_stored_api_key_has_no_readback_route(self, custom_providers_client: TestClient):
        """Provider secrets appear only masked in responses; no by-id plaintext retrieval route exists."""
        create_resp = custom_providers_client.post(
            "/api/v1/custom-providers",
            json={
                "display_name": "OneAPI",
                "discovery_format": "openai",
                "base_url": "https://oneapi.example.com",
                "api_key": "sk-secret",
                "models": [],
            },
        )
        assert create_resp.status_code == 201
        provider_id = create_resp.json()["id"]

        resp = custom_providers_client.get(f"/api/v1/custom-providers/{provider_id}/credentials")
        assert resp.status_code in (404, 405)
        detail = custom_providers_client.get(f"/api/v1/custom-providers/{provider_id}")
        assert "sk-secret" not in detail.text


class TestDurationsAndOutputLimitAsOverrides:
    """Durations/maximum output tokens persist only as capability overrides; saving never prefills name presets (docs/adr/0105,0106)."""

    @staticmethod
    def _create(client: TestClient, models: list[dict[str, Any]]):
        return client.post(
            "/api/v1/custom-providers",
            json={
                "display_name": "Relay",
                "discovery_format": "openai",
                "base_url": "https://relay.test/v1",
                "api_key": "sk-relay-12345678",
                "models": models,
            },
        )

    def test_video_model_without_override_is_not_prefilled(self, custom_providers_client: TestClient):
        resp = self._create(
            custom_providers_client,
            [{"model_id": "sora-2-pro", "display_name": "Sora 2 Pro", "endpoint": "openai-video"}],
        )

        assert resp.status_code == 201, resp.text
        assert resp.json()["models"][0]["capability_overrides"] is None

    def test_overrides_are_kept_per_media_type(self, custom_providers_client: TestClient):
        resp = self._create(
            custom_providers_client,
            [
                {
                    "model_id": "sora-2-pro",
                    "display_name": "Sora 2 Pro",
                    "endpoint": "openai-video",
                    "capability_overrides": {"durations": [6, 10]},
                },
                {
                    "model_id": "my-llm",
                    "display_name": "My LLM",
                    "endpoint": "openai-chat",
                    "capability_overrides": {"max_output_tokens": 8192},
                },
            ],
        )

        assert resp.status_code == 201, resp.text
        detail = custom_providers_client.get(f"/api/v1/custom-providers/{resp.json()['id']}").json()
        overrides = {m["model_id"]: m["capability_overrides"] for m in detail["models"]}
        assert overrides == {"sora-2-pro": {"durations": [6, 10]}, "my-llm": {"max_output_tokens": 8192}}

    @pytest.mark.parametrize("bad", [0, -1])
    def test_non_positive_output_limit_is_rejected(self, custom_providers_client: TestClient, bad: int):
        resp = self._create(
            custom_providers_client,
            [
                {
                    "model_id": "my-llm",
                    "display_name": "My LLM",
                    "endpoint": "openai-chat",
                    "capability_overrides": {"max_output_tokens": bad},
                }
            ],
        )

        assert resp.status_code == 422


class TestPathOverrides:
    _POLL_OVERRIDE: ClassVar[dict[str, str]] = {"poll": "/api/videos/{{ task_id }}"}

    @staticmethod
    def _model(endpoint: str, path_overrides: dict[str, str] | None) -> dict[str, Any]:
        return {"model_id": "veo-3", "display_name": "Veo", "endpoint": endpoint, "path_overrides": path_overrides}

    def _create(self, client: TestClient, path_overrides: dict[str, str] | None):
        return client.post(
            "/api/v1/custom-providers",
            json={
                "display_name": "Relay",
                "discovery_format": "openai",
                "base_url": "https://relay.test/v1",
                "api_key": "sk-relay-12345678",
                "models": [self._model("openrouter-video", path_overrides)],
            },
        )

    def test_changing_the_endpoint_on_save_clears_the_original_overrides(self, custom_providers_client: TestClient):
        created = self._create(custom_providers_client, self._POLL_OVERRIDE).json()
        assert created["models"][0]["path_overrides"] == self._POLL_OVERRIDE
        assert [r["name"] for r in created["models"][0]["requests"]] == ["submit", "poll"]

        resp = custom_providers_client.put(
            f"/api/v1/custom-providers/{created['id']}",
            json={
                "display_name": "Relay",
                "base_url": "https://relay.test/v1",
                "models": [self._model("xai-video", self._POLL_OVERRIDE)],
            },
        )

        assert resp.status_code == 200
        assert resp.json()["models"][0]["endpoint"] == "xai-video"
        assert resp.json()["models"][0]["path_overrides"] is None

    def test_request_name_outside_the_endpoint_is_rejected(self, custom_providers_client: TestClient):
        resp = self._create(custom_providers_client, {"result": "/api/result"})

        assert resp.status_code == 422
