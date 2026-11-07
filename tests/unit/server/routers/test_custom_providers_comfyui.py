"""Custom-provider management API behavior with ``discovery_format = comfyui``.

Same subject/app/client setup as ``test_custom_providers_api.py``, split by behavior:
protocol-specific connectivity, inapplicable discovery, bidirectional endpoint pairing and disabled capability overrides.
"""

from __future__ import annotations

from collections.abc import Generator
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient

from lib.db.models.custom_provider import CustomProvider
from lib.db.repositories.custom_endpoint_repo import CustomEndpointRepository
from lib.db.repositories.custom_provider_repo import CustomProviderRepository
from tests.factories import comfyui_endpoint_definition, custom_endpoint_definition
from tests.http_capture import capture_http, only_request

_COMFY_URL = "http://comfy.test:8188"
_SYSTEM_STATS = f"{_COMFY_URL}/system_stats"


@pytest.fixture
def comfyui_client(custom_providers_app) -> Generator[TestClient, None, None]:
    with TestClient(custom_providers_app) as c:
        yield c


async def _store_endpoint(session_factory, definition: dict[str, Any]) -> str:
    """Persist definitions, returning endpoint keys; mirror columns derive from definitions, sharing save-route truth."""
    async with session_factory() as session:
        row = await CustomEndpointRepository(session).create(
            definition=definition,
            kind=str(definition["kind"]),
            schema_version=str(definition["schema_version"]),
            media_type=str(definition.get("media_type", "video")),
            display_name=str(definition["meta"]["name"]),
        )
        await session.commit()
        return f"ce-{row.id}"


async def _stored_provider(session_factory, provider_id: int) -> CustomProvider:
    async with session_factory() as session:
        provider = await CustomProviderRepository(session).get_provider(provider_id)
        assert provider is not None
        return provider


def _create_provider(client: TestClient, **overrides: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "display_name": "我的 ComfyUI",
        "discovery_format": "comfyui",
        "base_url": _COMFY_URL,
        "api_key": "",
        "models": [],
    }
    body.update(overrides)
    resp = client.post("/api/v1/custom-providers", json=body)
    assert resp.status_code == 201, resp.text
    return resp.json()


def _model(endpoint: str, **overrides: Any) -> dict[str, Any]:
    row: dict[str, Any] = {"model_id": "wan-t2v", "display_name": "Wan T2V", "endpoint": endpoint}
    row.update(overrides)
    return row


# ---------------------------------------------------------------------------
# Provider creation and endpoint catalogs
# ---------------------------------------------------------------------------


class TestComfyuiProviderCreation:
    async def test_an_empty_api_key_is_accepted(self, comfyui_client: TestClient, custom_providers_app_session_factory):
        """ComfyUI itself has no authentication; credential templates live in definitions, so empty provider api_key must save."""
        provider = _create_provider(comfyui_client)
        assert provider["discovery_format"] == "comfyui"
        stored = await _stored_provider(custom_providers_app_session_factory, provider["id"])
        assert stored.api_key == ""
        assert stored.base_url == _COMFY_URL

    def test_an_unknown_protocol_is_refused_at_the_request_boundary(self, comfyui_client: TestClient):
        """Protocol catalogs are closed: only DiscoveryFormatLiteral admits new values."""
        resp = comfyui_client.post(
            "/api/v1/custom-providers",
            json={
                "display_name": "x",
                "discovery_format": "comfy",
                "base_url": _COMFY_URL,
                "api_key": "",
                "models": [],
            },
        )
        assert resp.status_code == 422

    async def test_a_comfyui_endpoint_reaches_the_endpoint_catalog(
        self, comfyui_client, custom_providers_app_session_factory
    ):
        """ComfyUI endpoint catalogs read media/capabilities from definitions, deriving capabilities from bindings."""
        key = await _store_endpoint(custom_providers_app_session_factory, comfyui_endpoint_definition())
        catalog = comfyui_client.get("/api/v1/custom-providers/endpoints").json()["endpoints"]
        entry = next(e for e in catalog if e["key"] == key)
        assert entry["kind"] == "comfyui"
        assert entry["media_type"] == "video"
        assert entry["source"] == "custom"
        assert entry["display_name"] == "示例 ComfyUI 端点"
        assert entry["request_method"] == "POST"
        assert entry["request_path_template"] == "/prompt"
        assert entry["end_image_capable"] is False

    async def test_an_image_comfyui_endpoint_carries_its_own_media_type(
        self, comfyui_client, custom_providers_app_session_factory
    ):
        """Definitions determine image versus video outputs; endpoint keys cannot infer media types."""
        definition = comfyui_endpoint_definition(media_type="image")
        key = await _store_endpoint(custom_providers_app_session_factory, definition)
        catalog = comfyui_client.get("/api/v1/custom-providers/endpoints").json()["endpoints"]
        entry = next(e for e in catalog if e["key"] == key)
        assert entry["media_type"] == "image"
        # Fixtures lack reference-image bindings: workflows support text-to-image only (``docs/adr/0082``).
        assert entry["image_capabilities"] == ["text_to_image"]


# ---------------------------------------------------------------------------
# Connectivity checks
# ---------------------------------------------------------------------------


def _stats_response(version: str | None = "0.3.60") -> httpx.Response:
    system: dict[str, Any] = {"os": "posix", "python_version": "3.12.0"}
    if version is not None:
        system["comfyui_version"] = version
    return httpx.Response(200, json={"system": system, "devices": []})


class TestComfyuiConnectivityCheck:
    def test_a_non_empty_api_key_is_sent_as_a_bare_bearer_token(self, comfyui_client: TestClient):
        with capture_http() as router:
            route = router.get(_SYSTEM_STATS).mock(return_value=_stats_response())
            resp = comfyui_client.post(
                "/api/v1/custom-providers/test",
                json={"discovery_format": "comfyui", "base_url": _COMFY_URL, "api_key": "proxy-token"},
            )
        assert resp.status_code == 200
        assert only_request(route).headers["authorization"] == "Bearer proxy-token"

    def test_an_empty_api_key_sends_no_credentials_at_all(self, comfyui_client: TestClient):
        """Empty credentials must not become ``Bearer ``: proxies treat them as failed authentication, not anonymous requests."""
        with capture_http() as router:
            route = router.get(_SYSTEM_STATS).mock(return_value=_stats_response())
            resp = comfyui_client.post(
                "/api/v1/custom-providers/test",
                json={"discovery_format": "comfyui", "base_url": _COMFY_URL, "api_key": ""},
            )
        assert resp.status_code == 200
        assert "authorization" not in only_request(route).headers

    def test_the_reported_version_comes_back_in_the_message(self, comfyui_client: TestClient):
        with capture_http() as router:
            router.get(_SYSTEM_STATS).mock(return_value=_stats_response("0.3.60"))
            body = comfyui_client.post(
                "/api/v1/custom-providers/test",
                json={"discovery_format": "comfyui", "base_url": _COMFY_URL, "api_key": ""},
            ).json()
        assert body["success"] is True
        assert "0.3.60" in body["message"]

    def test_a_missing_version_still_counts_as_reachable(self, comfyui_client: TestClient):
        """Older versions/some proxies omit this field; treating absence as failure blocks usable deployments."""
        with capture_http() as router:
            router.get(_SYSTEM_STATS).mock(return_value=_stats_response(version=None))
            body = comfyui_client.post(
                "/api/v1/custom-providers/test",
                json={"discovery_format": "comfyui", "base_url": _COMFY_URL, "api_key": ""},
            ).json()
        assert body["success"] is True
        assert body["message"]

    def test_an_unreachable_service_comes_back_as_a_readable_failure(self, comfyui_client: TestClient):
        """Soft failures return 200 + success=False like both other protocols, with UI directly displaying reasons."""
        with capture_http() as router:
            router.get(_SYSTEM_STATS).mock(side_effect=httpx.ConnectError("connection refused"))
            resp = comfyui_client.post(
                "/api/v1/custom-providers/test",
                json={"discovery_format": "comfyui", "base_url": _COMFY_URL, "api_key": ""},
            )
        assert resp.status_code == 200
        body = resp.json()
        assert body["success"] is False
        assert "connection refused" in body["message"]

    def test_an_error_status_comes_back_as_a_readable_failure(self, comfyui_client: TestClient):
        with capture_http() as router:
            router.get(_SYSTEM_STATS).mock(return_value=httpx.Response(401, json={"error": "unauthorized"}))
            body = comfyui_client.post(
                "/api/v1/custom-providers/test",
                json={"discovery_format": "comfyui", "base_url": _COMFY_URL, "api_key": "wrong"},
            ).json()
        assert body["success"] is False
        assert "401" in body["message"]

    def test_the_stored_credential_entry_probes_the_same_way(self, comfyui_client: TestClient):
        """Stored/plaintext credential entries share one probe, never different paths."""
        provider = _create_provider(comfyui_client)
        with capture_http() as router:
            route = router.get(_SYSTEM_STATS).mock(return_value=_stats_response())
            body = comfyui_client.post(f"/api/v1/custom-providers/{provider['id']}/test").json()
        assert body["success"] is True
        assert "authorization" not in only_request(route).headers


class TestComfyuiProbeDestination:
    """Probes and artifact downloads share outbound-destination validation."""

    @pytest.mark.parametrize(
        "base_url",
        [
            "http://169.254.169.254",
            "http://[fd00:ec2::254]",
            "http://100.100.100.200",
            "http://192.0.0.192",
        ],
    )
    def test_a_metadata_destination_is_refused_before_the_request_leaves(
        self, comfyui_client: TestClient, base_url: str
    ):
        with capture_http() as router:
            route = router.get(f"{base_url}/system_stats").mock(return_value=_stats_response())
            body = comfyui_client.post(
                "/api/v1/custom-providers/test",
                json={"discovery_format": "comfyui", "base_url": base_url, "api_key": ""},
            ).json()
        assert route.call_count == 0
        assert body["success"] is False
        assert "disallowed address" in body["message"]

    @pytest.mark.parametrize("base_url", ["http://127.0.0.1:8188", "http://192.168.24.7:8188"])
    def test_loopback_and_private_addresses_stay_reachable(self, comfyui_client: TestClient, base_url: str):
        """Self-hosted ComfyUI legitimately uses loopback/private networks; this validation must allow them."""
        with capture_http() as router:
            route = router.get(f"{base_url}/system_stats").mock(return_value=_stats_response())
            body = comfyui_client.post(
                "/api/v1/custom-providers/test",
                json={"discovery_format": "comfyui", "base_url": base_url, "api_key": ""},
            ).json()
        assert route.call_count == 1
        assert body["success"] is True


# ---------------------------------------------------------------------------
# Model discovery
# ---------------------------------------------------------------------------


class TestComfyuiDiscovery:
    def test_discovery_reports_not_applicable_rather_than_an_empty_list(self, comfyui_client: TestClient):
        """Empty lists mean no discovered models, prompting credential checks in UI; that is a different condition."""
        resp = comfyui_client.post(
            "/api/v1/custom-providers/discover",
            json={"discovery_format": "comfyui", "base_url": _COMFY_URL, "api_key": ""},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["not_applicable"] is True
        assert body["models"] == []
        assert body["reason"]

    def test_the_stored_credential_entry_reports_the_same(self, comfyui_client: TestClient):
        provider = _create_provider(comfyui_client)
        body = comfyui_client.post(f"/api/v1/custom-providers/{provider['id']}/discover").json()
        assert body["not_applicable"] is True

    def test_other_protocols_keep_reporting_applicable(self, comfyui_client: TestClient):
        """Inapplicable belongs only to comfyui; failed discovery under other protocols must remain failure, never inapplicable."""
        with capture_http() as router:
            router.get(url__regex=r".*/models.*").mock(return_value=httpx.Response(200, json={"data": []}))
            body = comfyui_client.post(
                "/api/v1/custom-providers/discover",
                json={"discovery_format": "openai", "base_url": "https://api.example.com/v1", "api_key": "sk-x"},
            ).json()
        assert body["not_applicable"] is False


# ---------------------------------------------------------------------------
# Bidirectional endpoint-association validation
# ---------------------------------------------------------------------------


class TestComfyuiEndpointAttachment:
    async def test_a_comfyui_endpoint_cannot_hang_on_another_protocol(
        self, comfyui_client, custom_providers_app_session_factory
    ):
        key = await _store_endpoint(custom_providers_app_session_factory, comfyui_endpoint_definition())
        resp = comfyui_client.post(
            "/api/v1/custom-providers",
            json={
                "display_name": "OpenAI 中转",
                "discovery_format": "openai",
                "base_url": "https://api.example.com/v1",
                "api_key": "sk-x",
                "models": [_model(key)],
            },
        )
        assert resp.status_code == 422
        assert key in resp.json()["detail"]

    async def test_a_comfyui_provider_cannot_hang_a_declarative_endpoint(
        self, comfyui_client, custom_providers_app_session_factory
    ):
        key = await _store_endpoint(custom_providers_app_session_factory, custom_endpoint_definition())
        resp = comfyui_client.post(
            "/api/v1/custom-providers",
            json={
                "display_name": "我的 ComfyUI",
                "discovery_format": "comfyui",
                "base_url": _COMFY_URL,
                "api_key": "",
                "models": [_model(key)],
            },
        )
        assert resp.status_code == 422
        assert key in resp.json()["detail"]

    async def test_a_comfyui_provider_cannot_hang_a_builtin_endpoint(self, comfyui_client: TestClient):
        """Built-in endpoint kind ``python`` is likewise not ComfyUI."""
        resp = comfyui_client.post(
            "/api/v1/custom-providers",
            json={
                "display_name": "我的 ComfyUI",
                "discovery_format": "comfyui",
                "base_url": _COMFY_URL,
                "api_key": "",
                "models": [_model("openai-chat")],
            },
        )
        assert resp.status_code == 422

    async def test_a_matching_pair_is_accepted(self, comfyui_client, custom_providers_app_session_factory):
        key = await _store_endpoint(custom_providers_app_session_factory, comfyui_endpoint_definition())
        provider = _create_provider(comfyui_client, models=[_model(key)])
        assert provider["models"][0]["endpoint"] == key

    async def test_other_protocols_keep_their_free_attachment(
        self, comfyui_client, custom_providers_app_session_factory
    ):
        """Constraints apply only to comfyui; declarative endpoints still attach to openai providers normally."""
        key = await _store_endpoint(custom_providers_app_session_factory, custom_endpoint_definition())
        resp = comfyui_client.post(
            "/api/v1/custom-providers",
            json={
                "display_name": "OpenAI 中转",
                "discovery_format": "openai",
                "base_url": "https://api.example.com/v1",
                "api_key": "sk-x",
                "models": [_model(key)],
            },
        )
        assert resp.status_code == 201

    async def test_the_full_update_path_checks_the_pair_too(self, comfyui_client, custom_providers_app_session_factory):
        """Read protocols from database rows, not bodies; PUT forbids protocol changes, and endpoint changes require reevaluation."""
        comfyui_key = await _store_endpoint(custom_providers_app_session_factory, comfyui_endpoint_definition())
        declarative_key = await _store_endpoint(custom_providers_app_session_factory, custom_endpoint_definition())
        provider = _create_provider(comfyui_client, models=[_model(comfyui_key)])
        resp = comfyui_client.put(
            f"/api/v1/custom-providers/{provider['id']}",
            json={
                "display_name": "我的 ComfyUI",
                "base_url": _COMFY_URL,
                "models": [_model(declarative_key)],
                "image_max_workers": None,
                "video_max_workers": None,
                "audio_max_workers": None,
            },
        )
        assert resp.status_code == 422
        # Entire batches remain unpersisted; original rows remain.
        kept = comfyui_client.get(f"/api/v1/custom-providers/{provider['id']}").json()
        assert [m["endpoint"] for m in kept["models"]] == [comfyui_key]


# ---------------------------------------------------------------------------
# Capability overrides disabled
# ---------------------------------------------------------------------------


class TestComfyuiCapabilityOverrides:
    @pytest.mark.parametrize("override", [{"last_frame": True}, {"last_frame": False}])
    async def test_writing_an_override_is_refused_for_the_protocol_reason(
        self, comfyui_client, custom_providers_app_session_factory, override: dict[str, Any]
    ):
        """Rejection must specifically identify unsupported protocol overrides.

        ComfyUI endpoints have empty capabilities, so ``last_frame=True`` also fails generic ending-frame validation. Protocol checks
        ordered later tell users endpoints lack ending frames, sending them to find ending-frame-capable ComfyUI endpoints, although this
        protocol has no overrides at all. Assert both values so ordering regressions fail.
        """
        key = await _store_endpoint(custom_providers_app_session_factory, comfyui_endpoint_definition())
        resp = comfyui_client.post(
            "/api/v1/custom-providers",
            json={
                "display_name": "我的 ComfyUI",
                "discovery_format": "comfyui",
                "base_url": _COMFY_URL,
                "api_key": "",
                "models": [_model(key, capability_overrides=override)],
            },
        )
        assert resp.status_code == 422
        detail = resp.json()["detail"]
        assert "wan-t2v" in detail
        assert "节点绑定" in detail

    async def test_the_full_update_path_refuses_it_for_the_same_reason(
        self, comfyui_client, custom_providers_app_session_factory
    ):
        """Full-table saves share creation decision order; dedicated explanations must not exist only on creation paths."""
        key = await _store_endpoint(custom_providers_app_session_factory, comfyui_endpoint_definition())
        provider = _create_provider(comfyui_client, models=[_model(key)])
        resp = comfyui_client.put(
            f"/api/v1/custom-providers/{provider['id']}",
            json={
                "display_name": "我的 ComfyUI",
                "base_url": _COMFY_URL,
                "models": [_model(key, capability_overrides={"last_frame": True})],
                "image_max_workers": None,
                "video_max_workers": None,
                "audio_max_workers": None,
            },
        )
        assert resp.status_code == 422
        assert "节点绑定" in resp.json()["detail"]

    async def test_a_stored_override_is_ignored_on_read(self, comfyui_client, custom_providers_app_session_factory):
        """Write guards cannot prevent manual DB edits; responses also ignore overrides so UI never shows execution-unsupported values."""
        key = await _store_endpoint(custom_providers_app_session_factory, comfyui_endpoint_definition())
        provider = _create_provider(comfyui_client, models=[_model(key)])
        async with custom_providers_app_session_factory() as session:
            from lib.db.repositories.custom_provider_repo import CustomProviderRepository

            rows = await CustomProviderRepository(session).list_models(provider["id"])
            rows[0].capability_overrides = {"last_frame": True}
            await session.commit()
        read_back = comfyui_client.get(f"/api/v1/custom-providers/{provider['id']}").json()
        assert read_back["models"][0]["capability_overrides"] is None


def _comfyui_spec(definition: dict[str, Any]):
    from lib.custom_provider.endpoints import comfyui_endpoint_spec

    return comfyui_endpoint_spec("ce-7", definition)


def _with_frames(**frames_extra: Any) -> dict[str, Any]:
    """Frame-count-driven definition: ``length`` literal 81, readonly ``fps`` binding reads 16 → native 5 seconds."""
    definition = comfyui_endpoint_definition()
    definition["workflow"]["5"]["inputs"]["length"] = 81
    definition["bindings"]["frames"] = [
        {"node": "5", "input": "length", "class_type": "EmptyLatentImage", **frames_extra}
    ]
    return definition


def _stored_durations(model, definition: dict[str, Any]) -> object:
    overrides = model.to_db_dict(_comfyui_spec(definition))["capability_overrides"]
    return None if overrides is None else overrides.get("durations")


class TestComfyuiSupportedDurations:
    """Default duration tiers derive from bindings, never model-name heuristics; user-added/removed tiers persist as ``durations`` overrides
    (exceptions in ``docs/adr/0082``, ``0104``)."""

    def test_without_an_edit_nothing_is_stored_and_the_name_heuristic_never_runs(self):
        """Heuristic presets give ``my-wan-workflow`` ``[4, 8]``, unrelated to this workflow."""
        from lib.custom_provider.duration_presets import infer_supported_durations
        from server.routers.custom_providers import ModelInput

        model = ModelInput(model_id="my-wan-workflow", display_name="m", endpoint="ce-7")

        assert infer_supported_durations("my-wan-workflow") == [4, 8]
        assert model.to_db_dict(_comfyui_spec(_with_frames()))["capability_overrides"] is None

    def test_a_user_edited_tier_is_kept_when_frames_are_drivable(self):
        from server.routers.custom_providers import ModelInput

        model = ModelInput(
            model_id="m", display_name="m", endpoint="ce-7", capability_overrides={"durations": [3, 5, 8]}
        )

        assert _stored_durations(model, _with_frames()) == [3, 5, 8]

    def test_a_tier_equal_to_the_native_duration_is_not_stored(self):
        from server.routers.custom_providers import ModelInput

        model = ModelInput(model_id="m", display_name="m", endpoint="ce-7", capability_overrides={"durations": [5]})

        assert model.to_db_dict(_comfyui_spec(_with_frames()))["capability_overrides"] is None

    def test_frames_unbound_drops_the_edit(self):
        """Fixed-duration workflows cannot change tiers; never persist supplied overrides."""
        from server.routers.custom_providers import ModelInput

        model = ModelInput(model_id="m", display_name="m", endpoint="ce-7", capability_overrides={"durations": [3, 5]})

        assert _stored_durations(model, comfyui_endpoint_definition()) is None

    def test_frames_bound_without_a_frame_rate_source_drops_it_too(self):
        from server.routers.custom_providers import ModelInput

        definition = _with_frames()
        definition["bindings"].pop("fps")
        model = ModelInput(model_id="m", display_name="m", endpoint="ce-7", capability_overrides={"durations": [3, 5]})

        assert _stored_durations(model, definition) is None

    def test_a_manual_frame_rate_on_the_entry_makes_the_tier_editable_again(self):
        from server.routers.custom_providers import ModelInput

        definition = _with_frames(fps=20)
        definition["bindings"].pop("fps")
        model = ModelInput(model_id="m", display_name="m", endpoint="ce-7", capability_overrides={"durations": [4, 8]})

        assert _stored_durations(model, definition) == [4, 8]


class TestComfyuiDurationsOverrideApi:
    """ComfyUI video rows permit only ``durations`` overrides; other dimensions and image endpoints remain protocol-rejected."""

    @staticmethod
    def _create(client: TestClient, key: str, overrides: dict[str, Any]):
        return client.post(
            "/api/v1/custom-providers",
            json={
                "display_name": "我的 ComfyUI",
                "discovery_format": "comfyui",
                "base_url": _COMFY_URL,
                "api_key": "",
                "models": [_model(key, capability_overrides=overrides)],
            },
        )

    async def test_a_video_row_accepts_and_echoes_its_durations(
        self, comfyui_client, custom_providers_app_session_factory
    ):
        key = await _store_endpoint(custom_providers_app_session_factory, _with_frames())

        resp = self._create(comfyui_client, key, {"durations": [5, 10]})

        assert resp.status_code == 201, resp.text
        read_back = comfyui_client.get(f"/api/v1/custom-providers/{resp.json()['id']}").json()
        assert read_back["models"][0]["capability_overrides"] == {"durations": [5, 10]}

    async def test_durations_with_another_dimension_is_refused(
        self, comfyui_client, custom_providers_app_session_factory
    ):
        key = await _store_endpoint(custom_providers_app_session_factory, _with_frames())

        resp = self._create(comfyui_client, key, {"durations": [5, 10], "last_frame": True})

        assert resp.status_code == 422
        assert "节点绑定" in resp.json()["detail"]

    async def test_an_image_row_refuses_durations(self, comfyui_client, custom_providers_app_session_factory):
        key = await _store_endpoint(
            custom_providers_app_session_factory, comfyui_endpoint_definition(media_type="image")
        )

        resp = self._create(comfyui_client, key, {"durations": [5]})

        assert resp.status_code == 422

    async def test_invalid_durations_are_refused(self, comfyui_client, custom_providers_app_session_factory):
        key = await _store_endpoint(custom_providers_app_session_factory, _with_frames())

        resp = self._create(comfyui_client, key, {"durations": [0, 5]})

        assert resp.status_code == 422
