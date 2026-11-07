"""Capability-override API and presentation share truth.

Cover configuration read/write, stripping nonwhitelisted keys/4xx/whole-replacement semantics, resolver effective capabilities,
and shared presentation/execution composition. API uses test_custom_providers_api.py in-memory SQLite +
TestClient patterns; shared-truth tests use real repository loading from test_custom_provider_loader.py.
"""

from __future__ import annotations

from collections.abc import Generator
from unittest.mock import MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from lib.custom_provider import make_provider_id
from lib.custom_provider.capabilities import synthesize_video_capabilities, two_sided_video_capabilities
from lib.custom_provider.loader import load_custom_backend
from lib.db.repositories.custom_provider_repo import CustomProviderRepository
from server.auth import CurrentUserInfo, get_current_user
from server.error_handlers import register_error_handlers
from tests.auth_deps import AUTH_DEPENDENCIES
from tests.fakes import use_project_manager, use_projects_session_factory

# System last_frame=False/max_reference_images=1 makes before/after overrides distinguishable.
VIDEO_ENDPOINT = "openai-video"
VIDEO_MODEL = "relay-sora"

# openai-video delegates never serialize end_image (see _check_capability_overrides
# end_image_capable gates); last_frame=True tests require actually ending-frame-capable endpoints.
# ark-seedance-2 with 1.0 pro fast reports last_frame=False (docs/api-docs/providers/ark.md
# capability tables mark -), permitting before/after assertions. Avoid seedance-2 families whose system
# decisions already True cannot demonstrate override differences.
LAST_FRAME_ENDPOINT = "ark-seedance-2"
LAST_FRAME_MODEL = "doubao-seedance-1-0-pro-fast-251015"


@pytest.fixture
def capability_client(custom_providers_app) -> Generator[TestClient, None, None]:
    with TestClient(custom_providers_app) as c:
        yield c


def _post_provider(capability_client: TestClient, models: list[dict]):
    return capability_client.post(
        "/api/v1/custom-providers",
        json={
            "display_name": "Relay",
            "discovery_format": "openai",
            "base_url": "https://relay.test/v1",
            "api_key": "sk-relay",
            "models": models,
        },
    )


def _create_provider(capability_client: TestClient, models: list[dict]) -> int:
    resp = _post_provider(capability_client, models)
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


async def _seed_provider_with_raw_models(custom_providers_app_session_factory, models: list[dict]) -> int:
    """Bypass APIs to persist manually edited override dictionaries impossible through UI."""
    async with custom_providers_app_session_factory() as db_session:
        repo = CustomProviderRepository(db_session)
        provider = await repo.create_provider(
            display_name="Relay",
            discovery_format="openai",
            base_url="https://relay.test/v1",
            api_key="sk-relay",
            models=models,
        )
        await db_session.commit()
        return provider.id


def _video_model(**overrides) -> dict:
    model = {
        "model_id": VIDEO_MODEL,
        "display_name": "Sora 2",
        "endpoint": VIDEO_ENDPOINT,
        "is_default": True,
        "is_enabled": True,
    }
    model.update(overrides)
    return model


class TestModelListExposesCapabilities:
    """Model lists return system decisions/user overrides, letting settings merge trivially for display."""

    def test_video_model_reports_system_capabilities(self, capability_client: TestClient):
        pid = _create_provider(capability_client, [_video_model()])

        models = capability_client.get(f"/api/v1/custom-providers/{pid}").json()["models"]
        assert models[0]["system_capabilities"] == {
            "text_to_video": True,
            "first_frame": True,
            "last_frame": False,
            "max_reference_images": 1,
            "reference_audio_mode": "none",
            "max_reference_audio_count": 0,
            "max_reference_audio_total_seconds": None,
            "reference_audio_per_image": False,
            "max_prompt_chars": None,
            "first_frame_ratio_adaptive_only": False,
        }
        assert models[0]["capability_overrides"] is None

    def test_system_capabilities_matches_synthesis_source(self, capability_client: TestClient):
        """API decisions are not duplicated; they are preoverride channel/entry evaluations from composition functions."""
        pid = _create_provider(capability_client, [_video_model()])

        models = capability_client.get(f"/api/v1/custom-providers/{pid}").json()["models"]
        expected = two_sided_video_capabilities(endpoint=VIDEO_ENDPOINT, model_id=VIDEO_MODEL)
        assert models[0]["system_capabilities"] == {
            "text_to_video": expected.text_to_video,
            "first_frame": expected.first_frame,
            "last_frame": expected.last_frame,
            "max_reference_images": expected.max_reference_images,
            "reference_audio_mode": expected.reference_audio_mode.value,
            "max_reference_audio_count": expected.max_reference_audio_count,
            "max_reference_audio_total_seconds": expected.max_reference_audio_total_seconds,
            "reference_audio_per_image": expected.reference_audio_per_image,
            "max_prompt_chars": expected.max_prompt_chars,
            "first_frame_ratio_adaptive_only": expected.first_frame_ratio_adaptive_only,
        }

    def test_non_video_model_has_no_system_capabilities(self, capability_client: TestClient):
        pid = _create_provider(
            capability_client,
            [{"model_id": "dall-e-3", "display_name": "D3", "endpoint": "openai-images", "is_enabled": True}],
        )

        models = capability_client.get(f"/api/v1/custom-providers/{pid}").json()["models"]
        assert models[0]["system_capabilities"] is None

    def test_overrides_round_trip_through_create(self, capability_client: TestClient):
        pid = _create_provider(
            capability_client,
            [
                _video_model(
                    capability_overrides={"last_frame": True}, endpoint=LAST_FRAME_ENDPOINT, model_id=LAST_FRAME_MODEL
                )
            ],
        )

        models = capability_client.get(f"/api/v1/custom-providers/{pid}").json()["models"]
        assert models[0]["capability_overrides"] == {"last_frame": True}
        # Overrides never change decisions; settings must show decision False/effective True simultaneously.
        assert models[0]["system_capabilities"]["last_frame"] is False

    async def test_stale_incompatible_override_filtered_from_response(
        self, capability_client: TestClient, custom_providers_app_session_factory
    ):
        """Legacy/non-API writes may retain incompatible overrides, such as last_frame=True on non-end_image_capable
        openai-video. Writes cannot guard already-stored data; filter before echoing, never displaying
        effective overrides execution silently ignores. Raw echoes also make subsequent ordinary saves
        fail validation with 422, blocking unrelated edits."""
        pid = await _seed_provider_with_raw_models(
            custom_providers_app_session_factory,
            [
                {
                    "model_id": VIDEO_MODEL,
                    "display_name": "Sora 2",
                    "endpoint": VIDEO_ENDPOINT,
                    "is_enabled": True,
                    "is_default": True,
                    "capability_overrides": {"last_frame": True},
                }
            ],
        )

        models = capability_client.get(f"/api/v1/custom-providers/{pid}").json()["models"]
        assert models[0]["capability_overrides"] is None

    async def test_stale_incoherent_audio_override_filtered_from_response(
        self, capability_client: TestClient, custom_providers_app_session_factory
    ):
        """Legacy direct ⊕ zero limits pass individual-key filters but execution downgrades to none.

        Echoing unchanged has two consequences like last_frame: UI claims direct voices are effective while generation
        carries no voice inputs; clients echoing during unrelated edits hit the same write invariant,
        rejecting whole saves with 422.
        """
        pid = await _seed_provider_with_raw_models(
            custom_providers_app_session_factory,
            [
                {
                    "model_id": LAST_FRAME_MODEL,
                    "display_name": "Seedance",
                    "endpoint": LAST_FRAME_ENDPOINT,
                    "is_enabled": True,
                    "is_default": True,
                    "capability_overrides": {"last_frame": True, "reference_audio_mode": "direct"},
                }
            ],
        )

        models = capability_client.get(f"/api/v1/custom-providers/{pid}").json()["models"]
        # Remove both audio dimensions together, retaining unrelated last_frame overrides unchanged.
        assert models[0]["capability_overrides"] == {"last_frame": True}

    async def test_corrupted_non_dict_override_does_not_500_response(
        self, capability_client: TestClient, custom_providers_app_session_factory
    ):
        """Legacy/manual SQL can store nondictionaries in JSON columns; execution's
        synthesize_video_capabilities tolerates/ignores them, and response boundaries must likewise avoid
        inserting raw values into dict | None ModelResponse, causing Pydantic errors and whole list/detail
        500s that prevent users entering settings to clean bad values."""
        pid = await _seed_provider_with_raw_models(
            custom_providers_app_session_factory,
            [
                {
                    "model_id": VIDEO_MODEL,
                    "display_name": "Sora 2",
                    "endpoint": VIDEO_ENDPOINT,
                    "is_enabled": True,
                    "is_default": True,
                    "capability_overrides": "last_frame",
                }
            ],
        )

        resp = capability_client.get(f"/api/v1/custom-providers/{pid}")
        assert resp.status_code == 200
        assert resp.json()["models"][0]["capability_overrides"] is None

    async def test_retired_endpoint_override_does_not_500_response(
        self, capability_client: TestClient, custom_providers_app_session_factory
    ):
        """Removed registry endpoints make get_endpoint_spec raise ValueError; response filtering
        must tolerate lookup failures rather than let dirty legacy settings crash lists/details with 500."""
        pid = await _seed_provider_with_raw_models(
            custom_providers_app_session_factory,
            [
                {
                    "model_id": "retired-model",
                    "display_name": "Retired",
                    "endpoint": "no-such-retired-endpoint",
                    "is_enabled": True,
                    "is_default": True,
                    "capability_overrides": {"last_frame": True},
                }
            ],
        )

        resp = capability_client.get(f"/api/v1/custom-providers/{pid}")
        assert resp.status_code == 200
        assert resp.json()["models"][0]["capability_overrides"] is None

    async def test_unallowlisted_key_dropped_from_response(
        self, capability_client: TestClient, custom_providers_app_session_factory
    ):
        """DB legacy nonwhitelisted keys, such as valid but noneditable reference_audio_per_image, must not echo
        unchanged: UI would claim effective overrides that saving strips. GET/PUT key sets must match; callers
        should not need hidden knowledge that dirty echoes disappear on writing."""
        pid = await _seed_provider_with_raw_models(
            custom_providers_app_session_factory,
            [
                {
                    "model_id": VIDEO_MODEL,
                    "display_name": "Sora 2",
                    "endpoint": VIDEO_ENDPOINT,
                    "is_enabled": True,
                    "is_default": True,
                    "capability_overrides": {"reference_audio_per_image": True},
                }
            ],
        )

        models = capability_client.get(f"/api/v1/custom-providers/{pid}").json()["models"]
        assert models[0]["capability_overrides"] is None

    async def test_allowlisted_key_kept_when_mixed_with_unallowlisted(
        self, capability_client: TestClient, custom_providers_app_session_factory
    ):
        """Mixed legacy overrides retain whitelisted last_frame unchanged and remove nonwhitelisted
        reference_audio_per_image, matching echoed/persisted sets."""
        pid = await _seed_provider_with_raw_models(
            custom_providers_app_session_factory,
            [
                {
                    "model_id": LAST_FRAME_MODEL,
                    "display_name": "Seedance",
                    "endpoint": LAST_FRAME_ENDPOINT,
                    "is_enabled": True,
                    "is_default": True,
                    "capability_overrides": {"last_frame": True, "reference_audio_per_image": True},
                }
            ],
        )

        models = capability_client.get(f"/api/v1/custom-providers/{pid}").json()["models"]
        assert models[0]["capability_overrides"] == {"last_frame": True}


class TestSaveDropsUnlistedOverrides:
    """Save silently strips nonwhitelisted keys, their only exit.

    Such keys arise through manual DB edits; UI neither displays nor deletes them. Write rejection would trap users in
    unresolvable 422s, blocking even display-name edits.
    """

    def test_unknown_key_stripped_on_create(self, capability_client: TestClient):
        pid = _create_provider(capability_client, [_video_model(capability_overrides={"no_such_capability": True})])

        models = capability_client.get(f"/api/v1/custom-providers/{pid}").json()["models"]
        assert models[0]["capability_overrides"] is None

    def test_known_but_unallowlisted_key_stripped_on_create(self, capability_client: TestClient):
        """reference_audio_per_image is valid VideoCapabilities but describes request construction, not editable overrides."""
        pid = _create_provider(
            capability_client, [_video_model(capability_overrides={"reference_audio_per_image": True})]
        )

        models = capability_client.get(f"/api/v1/custom-providers/{pid}").json()["models"]
        assert models[0]["capability_overrides"] is None

    async def test_legacy_key_stripped_while_editing_last_frame(
        self, capability_client: TestClient, custom_providers_app_session_factory
    ):
        """Saving last_frame edits on legacy-key rows succeeds without 422, persisting whitelisted keys only."""
        pid = await _seed_provider_with_raw_models(
            custom_providers_app_session_factory,
            [
                {
                    "model_id": LAST_FRAME_MODEL,
                    "display_name": "Seedance",
                    "endpoint": LAST_FRAME_ENDPOINT,
                    "is_enabled": True,
                    "is_default": True,
                    "capability_overrides": {"reference_audio_per_image": True},
                }
            ],
        )

        resp = capability_client.put(
            f"/api/v1/custom-providers/{pid}",
            json={
                "display_name": "Relay",
                "base_url": "https://relay.test/v1",
                "models": [
                    _video_model(
                        endpoint=LAST_FRAME_ENDPOINT,
                        model_id=LAST_FRAME_MODEL,
                        # Forms echo GET-returned legacy keys, switching last_frame to force-on.
                        capability_overrides={"reference_audio_per_image": True, "last_frame": True},
                    )
                ],
            },
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["models"][0]["capability_overrides"] == {"last_frame": True}

        models = capability_client.get(f"/api/v1/custom-providers/{pid}").json()["models"]
        assert models[0]["capability_overrides"] == {"last_frame": True}

    async def test_legacy_key_stripped_on_full_update(
        self, capability_client: TestClient, custom_providers_app_session_factory
    ):
        """Likewise display_name-only edits: dirty historical overrides neither block nor persist unchanged."""
        pid = await _seed_provider_with_raw_models(
            custom_providers_app_session_factory,
            [
                {
                    "model_id": VIDEO_MODEL,
                    "display_name": "Sora 2",
                    "endpoint": VIDEO_ENDPOINT,
                    "is_enabled": True,
                    "is_default": True,
                    "capability_overrides": {"reference_audio_per_image": True},
                }
            ],
        )

        resp = capability_client.put(
            f"/api/v1/custom-providers/{pid}",
            json={
                "display_name": "Relay Renamed",
                "base_url": "https://relay.test/v1",
                "models": [_video_model(capability_overrides={"reference_audio_per_image": True})],
            },
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["display_name"] == "Relay Renamed"
        assert resp.json()["models"][0]["capability_overrides"] is None

    def test_patch_endpoint_absent_from_openapi(self, custom_providers_app):
        """Single-model override PATCH is removed: (provider_id, model_id, endpoint) cannot locate new
        providers/new rows/changed model IDs; override editing uniformly uses whole-form saves."""
        paths = custom_providers_app.openapi()["paths"]
        assert not [path for path in paths if "capability-overrides" in path]


class TestSaveValidatesOpenOverrides:
    """Whitelisted keys still validate endpoints/value types strictly; users can resolve failures through UI."""

    @pytest.mark.parametrize("bad_value", ["true", 1, 0, None, [], {}])
    def test_wrong_value_type_rejected_on_create(self, capability_client: TestClient, bad_value):
        resp = _post_provider(capability_client, [_video_model(capability_overrides={"last_frame": bad_value})])
        assert resp.status_code == 422

    def test_last_frame_rejected_on_endpoint_without_end_image_support(self, capability_client: TestClient):
        """openai-video delegates omit end_image; enabling overrides falsely advertises support while execution drops frames."""
        resp = _post_provider(capability_client, [_video_model(capability_overrides={"last_frame": True})])
        assert resp.status_code == 422
        assert "last_frame" in resp.json()["detail"]

    def test_reference_audio_direct_rejected_on_endpoint_without_audio_support(self, capability_client: TestClient):
        """openai-video delegates omit reference_audio_files; enabling overrides falsely advertises voice inputs,
        while execution still generates random voices."""
        resp = _post_provider(
            capability_client, [_video_model(capability_overrides={"reference_audio_mode": "direct"})]
        )
        assert resp.status_code == 422
        assert "reference_audio_mode" in resp.json()["detail"]

    def test_reference_audio_none_accepted_on_endpoint_without_audio_support(self, capability_client: TestClient):
        """Guards block claimed support only; disabling voices requires no transport endorsement."""
        pid = _create_provider(capability_client, [_video_model(capability_overrides={"reference_audio_mode": "none"})])

        models = capability_client.get(f"/api/v1/custom-providers/{pid}").json()["models"]
        assert models[0]["capability_overrides"] == {"reference_audio_mode": "none"}

    def test_reference_audio_direct_accepted_on_audio_capable_endpoint(self, capability_client: TestClient):
        resp = _post_provider(
            capability_client,
            [
                _video_model(
                    endpoint=LAST_FRAME_ENDPOINT,
                    model_id=LAST_FRAME_MODEL,
                    capability_overrides={"reference_audio_mode": "direct", "max_reference_audio_count": 2},
                )
            ],
        )
        assert resp.status_code == 201

    def test_reference_audio_direct_accepted_on_vidu_q4(self, capability_client: TestClient):
        """Vidu Q4 reference video writes audio to sounds; direct-reference overrides should save successfully."""
        resp = _post_provider(
            capability_client,
            [
                _video_model(
                    endpoint="vidu-video-q4",
                    model_id="viduq4-preview",
                    capability_overrides={"reference_audio_mode": "direct", "max_reference_audio_count": 3},
                )
            ],
        )
        assert resp.status_code == 201

    def test_reference_audio_direct_rejected_on_vidu_endpoint_without_sounds(self, capability_client: TestClient):
        """Related Vidu Q3 Turbo lacks reference audio; enable only endpoints actually sending sounds, never whole provider families."""
        resp = _post_provider(
            capability_client,
            [
                _video_model(
                    endpoint="vidu-video-q3-turbo",
                    model_id="viduq3-turbo",
                    capability_overrides={"reference_audio_mode": "direct", "max_reference_audio_count": 3},
                )
            ],
        )
        assert resp.status_code == 422
        assert "reference_audio_mode" in resp.json()["detail"]

    def test_direct_without_positive_count_rejected(self, capability_client: TestClient):
        """Merged invariants reject voice-input support combined with zero limits at write time.

        Admission otherwise silently downgrades during execution, leaving impossible user guidance of at most zero audio segments.
        This endpoint's system limit is zero, so overriding modes alone creates this combination.
        """
        resp = _post_provider(
            capability_client,
            [
                _video_model(
                    endpoint=LAST_FRAME_ENDPOINT,
                    model_id=LAST_FRAME_MODEL,
                    capability_overrides={"reference_audio_mode": "direct"},
                )
            ],
        )
        assert resp.status_code == 422
        assert "max_reference_audio_count" in resp.json()["detail"]

    @pytest.mark.parametrize("bad_value", ["DIRECT", "native", True, 1, None])
    def test_invalid_reference_audio_mode_rejected(self, capability_client: TestClient, bad_value):
        """Enum values agree across layers: write rejections/execution-ignored sets must match, avoiding UI admission followed by execution refusal."""
        resp = _post_provider(
            capability_client,
            [
                _video_model(
                    endpoint=LAST_FRAME_ENDPOINT,
                    model_id=LAST_FRAME_MODEL,
                    capability_overrides={"reference_audio_mode": bad_value},
                )
            ],
        )
        assert resp.status_code == 422

    def test_non_video_endpoint_rejected(self, capability_client: TestClient):
        resp = _post_provider(
            capability_client,
            [
                {
                    "model_id": "dall-e-3",
                    "display_name": "D3",
                    "endpoint": "openai-images",
                    "is_enabled": True,
                    "capability_overrides": {"last_frame": True},
                }
            ],
        )
        assert resp.status_code == 422

    def test_rejected_save_leaves_stored_overrides_intact(self, capability_client: TestClient):
        pid = _create_provider(
            capability_client,
            [
                _video_model(
                    capability_overrides={"last_frame": True},
                    endpoint=LAST_FRAME_ENDPOINT,
                    model_id=LAST_FRAME_MODEL,
                )
            ],
        )

        resp = capability_client.put(
            f"/api/v1/custom-providers/{pid}",
            json={
                "display_name": "Relay",
                "base_url": "https://relay.test/v1",
                "models": [
                    _video_model(
                        endpoint=LAST_FRAME_ENDPOINT,
                        model_id=LAST_FRAME_MODEL,
                        capability_overrides={"last_frame": "yes"},
                    )
                ],
            },
        )
        assert resp.status_code == 422

        models = capability_client.get(f"/api/v1/custom-providers/{pid}").json()["models"]
        assert models[0]["capability_overrides"] == {"last_frame": True}


class TestModelFactOverrides:
    """Model facts (docs/adr/0104) open by endpoint media type; replace whole dimensions, matching echoed/writable key sets."""

    def test_dimensions_of_each_media_type_round_trip(self, capability_client: TestClient):
        video = {
            "durations": [5, 10],
            "resolutions": ["720p", "1080p"],
            "duration_constraints": {"by_resolution": {"1080p": [5]}},
            "max_reference_images": 2,
            "audio_track": "always_off",
        }
        text = {"image_input": False, "structured_output": True, "max_output_tokens": 4096}
        image = {"text_to_image": False}
        pid = _create_provider(
            capability_client,
            [
                _video_model(capability_overrides=video),
                {
                    "model_id": "relay-llm",
                    "display_name": "LLM",
                    "endpoint": "openai-chat",
                    "is_enabled": True,
                    "capability_overrides": text,
                },
                {
                    "model_id": "relay-image",
                    "display_name": "Image",
                    "endpoint": "openai-images",
                    "is_enabled": True,
                    "capability_overrides": image,
                },
            ],
        )

        models = {m["model_id"]: m for m in capability_client.get(f"/api/v1/custom-providers/{pid}").json()["models"]}
        assert models[VIDEO_MODEL]["capability_overrides"] == video
        assert models["relay-llm"]["capability_overrides"] == text
        assert models["relay-image"]["capability_overrides"] == image

    @pytest.mark.parametrize(
        ("model", "capability"),
        [
            ({"endpoint": "openai-chat", "capability_overrides": {"last_frame": False}}, "last_frame"),
            ({"endpoint": "openai-chat", "capability_overrides": {"durations": [5]}}, "durations"),
            ({"endpoint": "openai-images", "capability_overrides": {"image_input": True}}, "image_input"),
            ({"endpoint": "openai-video", "capability_overrides": {"text_to_image": True}}, "text_to_image"),
        ],
    )
    def test_dimension_of_another_media_type_rejected(self, capability_client: TestClient, model, capability):
        resp = _post_provider(capability_client, [{"model_id": "m", "display_name": "M", "is_enabled": True, **model}])
        assert resp.status_code == 422
        assert capability in resp.json()["detail"]

    @pytest.mark.parametrize(
        "overrides",
        [
            {"durations": []},
            {"durations": [0, 5]},
            {"resolutions": "720p"},
            {"duration_constraints": {"by_resolution": {"1080p": []}}},
        ],
    )
    def test_malformed_video_fact_rejected(self, capability_client: TestClient, overrides):
        resp = _post_provider(capability_client, [_video_model(capability_overrides=overrides)])
        assert resp.status_code == 422

    def test_image_capability_the_endpoint_does_not_implement_rejected(self, capability_client: TestClient):
        """openai-images-generations implements text-to-image only; overrides cannot enable image-to-image and writes reject."""
        model = {"model_id": "m", "display_name": "M", "endpoint": "openai-images-generations", "is_enabled": True}
        resp = _post_provider(capability_client, [{**model, "capability_overrides": {"image_to_image": True}}])
        assert resp.status_code == 422
        assert "image_to_image" in resp.json()["detail"]

        # Disabling implemented channel dimensions needs no endorsement.
        resp = _post_provider(capability_client, [{**model, "capability_overrides": {"text_to_image": False}}])
        assert resp.status_code == 201


class TestFullUpdateOverrideSemantics:
    """Model-list saves replace whole lists; echo overrides with lists or they clear."""

    def test_invalid_override_rejected_on_full_update(self, capability_client: TestClient):
        pid = _create_provider(capability_client, [_video_model()])

        resp = capability_client.put(
            f"/api/v1/custom-providers/{pid}",
            json={
                "display_name": "Relay",
                "base_url": "https://relay.test/v1",
                "models": [_video_model(capability_overrides={"last_frame": "yes"})],
            },
        )
        assert resp.status_code == 422

    def test_override_written_and_read_back_through_full_update(self, capability_client: TestClient):
        """Settings override edits use whole-table PUT; after Follow→Force on saves,
        reloaded GET must retain identical overrides, preserving actual user choices."""
        pid = _create_provider(
            capability_client,
            [_video_model(endpoint=LAST_FRAME_ENDPOINT, model_id=LAST_FRAME_MODEL)],
        )

        resp = capability_client.put(
            f"/api/v1/custom-providers/{pid}",
            json={
                "display_name": "Relay",
                "base_url": "https://relay.test/v1",
                "models": [
                    _video_model(
                        capability_overrides={"last_frame": True},
                        endpoint=LAST_FRAME_ENDPOINT,
                        model_id=LAST_FRAME_MODEL,
                    )
                ],
            },
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["models"][0]["capability_overrides"] == {"last_frame": True}

        reloaded = capability_client.get(f"/api/v1/custom-providers/{pid}")
        assert reloaded.status_code == 200
        assert reloaded.json()["models"][0]["capability_overrides"] == {"last_frame": True}

    def test_override_cleared_to_null_through_full_update(self, capability_client: TestClient):
        """Restoring Follow removes sparse keys; empty dictionaries normalize to null.
        PUT must truly clear stored overrides so effective values follow system decisions again."""
        pid = _create_provider(
            capability_client,
            [
                _video_model(
                    capability_overrides={"last_frame": True},
                    endpoint=LAST_FRAME_ENDPOINT,
                    model_id=LAST_FRAME_MODEL,
                )
            ],
        )

        resp = capability_client.put(
            f"/api/v1/custom-providers/{pid}",
            json={
                "display_name": "Relay",
                "base_url": "https://relay.test/v1",
                "models": [
                    _video_model(
                        capability_overrides=None,
                        endpoint=LAST_FRAME_ENDPOINT,
                        model_id=LAST_FRAME_MODEL,
                    )
                ],
            },
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["models"][0]["capability_overrides"] is None

        reloaded = capability_client.get(f"/api/v1/custom-providers/{pid}")
        assert reloaded.json()["models"][0]["capability_overrides"] is None

    async def test_endpoint_change_validated_even_when_override_dict_unchanged(
        self, capability_client: TestClient, custom_providers_app_session_factory
    ):
        """Validate submitted (endpoint, override) per row: changing endpoints alone while retaining model_id/overrides
        naturally changes validation; last_frame=True validity depends on endpoint
        end_image_capable, so reject against new endpoints."""
        pid = await _seed_provider_with_raw_models(
            custom_providers_app_session_factory,
            [
                {
                    "model_id": LAST_FRAME_MODEL,
                    "display_name": "Seedance",
                    "endpoint": LAST_FRAME_ENDPOINT,
                    "is_enabled": True,
                    "is_default": True,
                    "capability_overrides": {"last_frame": True},
                }
            ],
        )

        resp = capability_client.put(
            f"/api/v1/custom-providers/{pid}",
            json={
                "display_name": "Relay",
                "base_url": "https://relay.test/v1",
                "models": [
                    _video_model(
                        model_id=LAST_FRAME_MODEL,
                        # Endpoints silently change to openai-video, not end_image_capable; override dictionaries remain unchanged.
                        endpoint=VIDEO_ENDPOINT,
                        capability_overrides={"last_frame": True},
                    )
                ],
            },
        )
        assert resp.status_code == 422


class TestResolverReturnsEffectiveCapabilities:
    """/video-capabilities resolvers must return effective capabilities sharing execution truth."""

    @staticmethod
    async def _seed(
        db_session: AsyncSession,
        *,
        overrides: object | None,
        endpoint: str = VIDEO_ENDPOINT,
        model_id: str = VIDEO_MODEL,
    ) -> str:
        repo = CustomProviderRepository(db_session)
        provider = await repo.create_provider(
            display_name="Relay",
            discovery_format="openai",
            base_url="https://relay.test/v1",
            api_key="sk-relay",
            models=[
                {
                    "model_id": model_id,
                    "display_name": "Sora 2",
                    "endpoint": endpoint,
                    "is_enabled": True,
                    "is_default": True,
                    "capability_overrides": overrides,
                }
            ],
        )
        await db_session.commit()
        return make_provider_id(provider.id)

    @staticmethod
    async def _resolve(db_session: AsyncSession, provider_id: str, model_id: str = VIDEO_MODEL) -> dict:
        from lib.config.resolver import ConfigResolver
        from lib.config.service import ConfigService

        factory = async_sessionmaker(bind=db_session.bind, class_=AsyncSession, expire_on_commit=False)
        resolver = ConfigResolver(factory, _bound_session=db_session)
        return await resolver._resolve_video_caps_for_model(
            ConfigService(db_session), db_session, provider_id, model_id, None
        )

    async def test_custom_model_without_overrides_follows_system(self, db_session: AsyncSession):
        pid = await self._seed(db_session, overrides=None)

        caps = await self._resolve(db_session, pid)
        system = two_sided_video_capabilities(endpoint=VIDEO_ENDPOINT, model_id=VIDEO_MODEL)
        assert caps["last_frame"] is system.last_frame
        assert caps["first_frame"] is system.first_frame
        assert caps["max_reference_images"] == system.max_reference_images

    async def test_override_changes_resolver_output(self, db_session: AsyncSession):
        """Acceptance: custom-model override writes change endpoint response values."""
        pid = await self._seed(
            db_session, overrides={"last_frame": True}, endpoint=LAST_FRAME_ENDPOINT, model_id=LAST_FRAME_MODEL
        )

        caps = await self._resolve(db_session, pid, model_id=LAST_FRAME_MODEL)
        assert two_sided_video_capabilities(endpoint=LAST_FRAME_ENDPOINT, model_id=LAST_FRAME_MODEL).last_frame is False
        assert caps["last_frame"] is True

    async def test_override_ignored_when_endpoint_lacks_end_image_support(self, db_session: AsyncSession):
        """openai-video omits end_image; legacy/non-API last_frame=True must still make resolvers
        fall back to system decisions, never accept falsely advertised support with execution dropping frames."""
        pid = await self._seed(db_session, overrides={"last_frame": True})

        caps = await self._resolve(db_session, pid)
        assert caps["last_frame"] is False

    @patch.dict("lib.custom_provider.endpoints.SEEDANCE_BACKENDS", {"ark-seedance-2": MagicMock()})
    async def test_resolver_matches_execution_layer(self, db_session: AsyncSession):
        """Presentation/execution share composition: compare resolver and loaded-backend fields individually."""
        pid = await self._seed(
            db_session, overrides={"last_frame": True}, endpoint=LAST_FRAME_ENDPOINT, model_id=LAST_FRAME_MODEL
        )

        caps = await self._resolve(db_session, pid, model_id=LAST_FRAME_MODEL)
        db_session.expunge_all()
        backend = await load_custom_backend(
            session=db_session, provider_id=pid, model_id=LAST_FRAME_MODEL, media_type="video"
        )

        executed = backend.video_capabilities
        assert caps["first_frame"] is executed.first_frame
        assert caps["last_frame"] is executed.last_frame
        assert caps["max_reference_images"] == executed.max_reference_images
        # Shared predicate: both sides equal composition functions on identical inputs.
        expected = synthesize_video_capabilities(
            endpoint=LAST_FRAME_ENDPOINT, model_id=LAST_FRAME_MODEL, overrides={"last_frame": True}
        )
        assert executed == expected

    async def test_disabled_model_falls_back_to_default_model(self, db_session: AsyncSession):
        """Disabled requested models share execution fallback: provider default enabled video
        models rather than errors; otherwise /video-capabilities returns 4xx while actual generation silently succeeds
        with defaults, causing layer drift."""
        repo = CustomProviderRepository(db_session)
        provider = await repo.create_provider(
            display_name="Relay",
            discovery_format="openai",
            base_url="https://relay.test/v1",
            api_key="sk-relay",
            models=[
                {
                    "model_id": "disabled-model",
                    "display_name": "Disabled",
                    "endpoint": VIDEO_ENDPOINT,
                    "is_enabled": False,
                    "is_default": False,
                    "capability_overrides": None,
                },
                {
                    "model_id": VIDEO_MODEL,
                    "display_name": "Sora 2",
                    "endpoint": VIDEO_ENDPOINT,
                    "is_enabled": True,
                    "is_default": True,
                    "capability_overrides": None,
                },
            ],
        )
        await db_session.commit()
        pid = make_provider_id(provider.id)

        caps = await self._resolve(db_session, pid, model_id="disabled-model")
        system = two_sided_video_capabilities(endpoint=VIDEO_ENDPOINT, model_id=VIDEO_MODEL)
        assert caps["last_frame"] is system.last_frame
        assert caps["first_frame"] is system.first_frame

    async def test_media_type_mismatch_falls_back_to_default_model(self, db_session: AsyncSession):
        """Enabled requested models on nonvideo endpoints likewise share execution
        fallback to provider default enabled video models, preventing presentation/execution drift."""
        repo = CustomProviderRepository(db_session)
        provider = await repo.create_provider(
            display_name="Relay",
            discovery_format="openai",
            base_url="https://relay.test/v1",
            api_key="sk-relay",
            models=[
                {
                    "model_id": "repurposed-model",
                    "display_name": "Repurposed",
                    "endpoint": "openai-images",
                    "is_enabled": True,
                    "is_default": False,
                    "capability_overrides": None,
                },
                {
                    "model_id": VIDEO_MODEL,
                    "display_name": "Sora 2",
                    "endpoint": VIDEO_ENDPOINT,
                    "is_enabled": True,
                    "is_default": True,
                    "capability_overrides": None,
                },
            ],
        )
        await db_session.commit()
        pid = make_provider_id(provider.id)

        caps = await self._resolve(db_session, pid, model_id="repurposed-model")
        system = two_sided_video_capabilities(endpoint=VIDEO_ENDPOINT, model_id=VIDEO_MODEL)
        assert caps["last_frame"] is system.last_frame
        assert caps["first_frame"] is system.first_frame

    async def test_builtin_boolean_caps_come_from_backend(self, db_session: AsyncSession):
        """Built-in booleans come from backend pure functions; registries store no duplicate truth."""
        from lib.backends.backend_assembly.specs import builtin_video_capabilities_for_model
        from lib.config.resolver import ConfigResolver
        from lib.config.service import ConfigService

        factory = async_sessionmaker(bind=db_session.bind, class_=AsyncSession, expire_on_commit=False)
        resolver = ConfigResolver(factory, _bound_session=db_session)
        caps = await resolver._resolve_video_caps_for_model(
            ConfigService(db_session), db_session, "grok", "grok-imagine-video", None
        )

        backend_caps = builtin_video_capabilities_for_model("grok", "grok-imagine-video")
        assert caps["source"] == "registry"
        assert caps["first_frame"] is backend_caps.first_frame
        assert caps["last_frame"] is backend_caps.last_frame


class TestBuiltinBackendsDeclareCapabilityFunction:
    """Every video-capable built-in provider must expose boolean capabilities through pure functions."""

    def test_every_builtin_video_provider_resolvable(self):
        from arcreel_market_core.video_backend_contract import VideoCapabilities
        from lib.backends.backend_assembly.specs import builtin_video_capabilities_for_model
        from lib.catalog import query as catalog
        from lib.config.registry import PROVIDER_REGISTRY

        for provider_id in PROVIDER_REGISTRY:
            video_models = [mid for mid, mi in catalog.list_models(provider_id).items() if mi.media_type == "video"]
            if not video_models:
                continue
            for model_id in video_models:
                caps = builtin_video_capabilities_for_model(provider_id, model_id)
                assert isinstance(caps, VideoCapabilities), f"{provider_id}/{model_id}"

    def test_unknown_backend_name_fails_loud(self):
        from lib.backends.video_backends.registry import video_capabilities_for_model

        with pytest.raises(ValueError, match="Unknown video backend"):
            video_capabilities_for_model("no-such-backend", "m")


class TestVideoCapabilitiesEndpoint:
    """GET /projects/{name}/video-capabilities response shapes react to overrides.

    Other shared-truth tests call private resolvers; cover HTTP here: both boolean flags actually
    appear in responses and change after override writes, beyond internal resolver changes alone.
    """

    @staticmethod
    def _client(
        monkeypatch, custom_providers_app_session_factory, provider_id: str, model_id: str = VIDEO_MODEL
    ) -> TestClient:

        from server.routers import projects as projects_mod

        class _FakePM:
            def load_project(self, name: str) -> dict:
                return {"name": name, "video_backend": f"{provider_id}/{model_id}"}

        use_projects_session_factory(monkeypatch, custom_providers_app_session_factory)
        use_project_manager(monkeypatch, _FakePM())

        app = FastAPI()
        app.dependency_overrides[get_current_user] = lambda: CurrentUserInfo(id="t", sub="t", role="admin")
        app.include_router(projects_mod.router, prefix="/api/v1", dependencies=AUTH_DEPENDENCIES)
        register_error_handlers(app)
        return TestClient(app)

    async def test_endpoint_returns_effective_boolean_caps(self, custom_providers_app_session_factory, monkeypatch):
        async with custom_providers_app_session_factory() as db_session:
            pid = await TestResolverReturnsEffectiveCapabilities._seed(db_session, overrides=None)

        with self._client(monkeypatch, custom_providers_app_session_factory, pid) as capability_client:
            body = capability_client.get("/api/v1/projects/demo/video-capabilities").json()

        system = two_sided_video_capabilities(endpoint=VIDEO_ENDPOINT, model_id=VIDEO_MODEL)
        assert body["first_frame"] is system.first_frame
        assert body["last_frame"] is system.last_frame is False

    async def test_endpoint_follows_written_override(self, custom_providers_app_session_factory, monkeypatch):
        """Acceptance: custom-model override writes change endpoint response values."""
        async with custom_providers_app_session_factory() as db_session:
            pid = await TestResolverReturnsEffectiveCapabilities._seed(
                db_session, overrides={"last_frame": True}, endpoint=LAST_FRAME_ENDPOINT, model_id=LAST_FRAME_MODEL
            )

        with self._client(
            monkeypatch, custom_providers_app_session_factory, pid, model_id=LAST_FRAME_MODEL
        ) as capability_client:
            body = capability_client.get("/api/v1/projects/demo/video-capabilities").json()

        assert two_sided_video_capabilities(endpoint=LAST_FRAME_ENDPOINT, model_id=LAST_FRAME_MODEL).last_frame is False
        assert body["last_frame"] is True


def test_image_reference_limit_override_roundtrip(capability_client: TestClient):
    model = {
        "model_id": "grok-imagine-image",
        "display_name": "Image",
        "endpoint": "openai-images",
        "is_enabled": True,
        "capability_overrides": {"image_to_image": True, "max_reference_images": 8},
    }
    pid = _create_provider(capability_client, [model])
    url = f"/api/v1/custom-providers/{pid}"
    saved = capability_client.get(url).json()["models"][0]
    assert saved["capability_overrides"] == model["capability_overrides"]
