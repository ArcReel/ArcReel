"""Custom backend DB loading through lib.custom_provider.loader: in-memory SQLite and real CustomProviderRepository.

Look up providers, validate models (existence/enabled/endpoint-inferred media_type), fall back to
defaults on invalidation, and delegate existing create_custom_backend without changing
ENDPOINT_REGISTRY. Mirror test_custom_provider_repo.py in-memory DB patterns without mocking repos.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from lib.backends.image_backends.base import ImageCapability
from lib.custom_provider import make_provider_id
from lib.custom_provider.backends import CustomImageBackend
from lib.custom_provider.capabilities import two_sided_video_capabilities
from lib.custom_provider.loader import custom_model_max_output_tokens, load_custom_backend
from lib.db.repositories.custom_provider_repo import CustomProviderRepository


async def _seed(db_session, *, models: list[dict]) -> str:
    repo = CustomProviderRepository(db_session)
    # display_name is NOT NULL; default to model_id.
    for m in models:
        m.setdefault("display_name", m["model_id"])
    provider = await repo.create_provider(
        display_name="Relay",
        discovery_format="openai",
        base_url="https://relay.test/v1",
        api_key="sk-relay",
        models=models,
    )
    await db_session.commit()
    return make_provider_id(provider.id)


class TestLoadCustomBackend:
    @patch("lib.custom_provider.endpoints.OpenAIImageBackend")
    async def test_resolves_named_model_and_delegates(self, mock_cls, db_session):
        pid = await _seed(
            db_session,
            models=[{"model_id": "dall-e-3", "endpoint": "openai-images", "is_enabled": True}],
        )
        result = await load_custom_backend(session=db_session, provider_id=pid, model_id="dall-e-3", media_type="image")
        assert isinstance(result, CustomImageBackend)
        assert result.model == "dall-e-3"
        mock_cls.assert_called_once_with(
            api_key="sk-relay", base_url="https://relay.test/v1", model="dall-e-3", transport_layer=None
        )

    @patch("lib.custom_provider.endpoints.OpenAIImageBackend")
    async def test_falls_back_to_default_when_model_disabled(self, mock_cls, db_session):
        # Requested model disabled → use default enabled model for that media_type.
        pid = await _seed(
            db_session,
            models=[
                {"model_id": "disabled-m", "endpoint": "openai-images", "is_enabled": False},
                {"model_id": "active-m", "endpoint": "openai-images", "is_enabled": True, "is_default": True},
            ],
        )
        result = await load_custom_backend(
            session=db_session, provider_id=pid, model_id="disabled-m", media_type="image"
        )
        assert result.model == "active-m"

    async def test_provider_not_found_fails_loud(self, db_session):
        with pytest.raises(ValueError, match="不存在"):
            await load_custom_backend(
                session=db_session, provider_id=make_provider_id(999), model_id="x", media_type="image"
            )

    async def test_no_default_model_for_media_fails_loud(self, db_session):
        # Only image models exist; video requests have no default video model.
        pid = await _seed(
            db_session,
            models=[{"model_id": "dall-e-3", "endpoint": "openai-images", "is_enabled": True}],
        )
        with pytest.raises(ValueError, match="没有默认"):
            await load_custom_backend(session=db_session, provider_id=pid, model_id=None, media_type="video")


class TestBucketAwareDefaultFallback:
    """Default fallback selects by task-type bucket: image t2i/i2i each have one default; inability to select a unique row fails loudly."""

    @staticmethod
    async def _seed_two_image_buckets(db_session) -> str:
        return await _seed(
            db_session,
            models=[
                # openai-images-generations declares t2i only; openai-images-edits i2i only.
                {"model_id": "t2i-m", "endpoint": "openai-images-generations", "is_enabled": True, "is_default": True},
                {"model_id": "i2i-m", "endpoint": "openai-images-edits", "is_enabled": True, "is_default": True},
            ],
        )

    @pytest.mark.parametrize(("generation_type", "expected"), [("t2i", "t2i-m"), ("i2i", "i2i-m")])
    @patch("lib.custom_provider.endpoints.OpenAIImageBackend", new=MagicMock())
    async def test_falls_back_to_the_default_of_the_requested_bucket(self, generation_type, expected, db_session):
        pid = await self._seed_two_image_buckets(db_session)
        result = await load_custom_backend(
            session=db_session,
            provider_id=pid,
            model_id=None,
            media_type="image",
            generation_type=generation_type,
        )
        assert result.model == expected

    async def test_bucket_without_default_fails_loud(self, db_session):
        pid = await _seed(
            db_session,
            models=[
                {"model_id": "t2i-m", "endpoint": "openai-images-generations", "is_enabled": True, "is_default": True}
            ],
        )
        with pytest.raises(ValueError, match="没有默认 i2i image 模型"):
            await load_custom_backend(
                session=db_session, provider_id=pid, model_id=None, media_type="image", generation_type="i2i"
            )

    async def test_two_defaults_in_the_same_bucket_fail_loud(self, db_session):
        # Both rows declaring t2i+i2i leave two t2i defaults after filtering; choosing either silently changes models.
        pid = await _seed(
            db_session,
            models=[
                {"model_id": "m-a", "endpoint": "openai-images", "is_enabled": True, "is_default": True},
                {"model_id": "m-b", "endpoint": "gemini-image", "is_enabled": True, "is_default": True},
            ],
        )
        with pytest.raises(ValueError, match="有多个默认 t2i image 模型：m-a, m-b"):
            await load_custom_backend(
                session=db_session, provider_id=pid, model_id=None, media_type="image", generation_type="t2i"
            )

    @patch("lib.custom_provider.endpoints.OpenAIImageBackend", new=MagicMock())
    async def test_double_default_without_bucket_still_resolves_the_named_model(self, db_session):
        # Bucketless callers (text/audio factories etc.) do not filter; explicit model hits bypass dual-default resolution.
        pid = await self._seed_two_image_buckets(db_session)
        result = await load_custom_backend(session=db_session, provider_id=pid, model_id="i2i-m", media_type="image")
        assert result.model == "i2i-m"


class TestVideoCapabilityOverridesReachExecution:
    """DB capability_overrides must apply to loaded backends, the source read by execution gates."""

    @staticmethod
    async def _load_video_backend(
        db_session, *, overrides: object | None, endpoint: str = "openai-video", model_id: str = "relay-sora"
    ):
        pid = await _seed(
            db_session,
            models=[
                {
                    "model_id": model_id,
                    "endpoint": endpoint,
                    "is_enabled": True,
                    "is_default": True,
                    "capability_overrides": overrides,
                }
            ],
        )
        # Clear identity maps to force real SELECT; override dictionaries must round-trip JSON to verify DB semantics.
        db_session.expunge_all()
        return await load_custom_backend(session=db_session, provider_id=pid, model_id=model_id, media_type="video")

    @patch("lib.custom_provider.endpoints.OpenAIVideoBackend", new=MagicMock())
    async def test_null_overrides_follow_system_judgement(self, db_session):
        backend = await self._load_video_backend(db_session, overrides=None)
        assert backend.video_capabilities == two_sided_video_capabilities(
            endpoint="openai-video", model_id="relay-sora"
        )

    @patch.dict("lib.custom_provider.endpoints.SEEDANCE_BACKENDS", {"ark-seedance-2": MagicMock()})
    async def test_override_forces_capability_on(self, db_session):
        # Unmatched catalog entries conservatively disable last frames; forced overrides expose True and stop forwarding the wrapped backend.
        # Overrides require endpoint end_image_capable support, which openai-video lacks.
        backend = await self._load_video_backend(
            db_session, overrides={"last_frame": True}, endpoint="ark-seedance-2", model_id="seedance-1-pro"
        )
        assert two_sided_video_capabilities(endpoint="ark-seedance-2", model_id="seedance-1-pro").last_frame is False
        assert backend.video_capabilities.last_frame is True
        # Unoverridden dimensions follow joint evaluation; reference-image limits are unrecorded.
        assert backend.video_capabilities.max_reference_images is None

    @patch("lib.custom_provider.endpoints.OpenAIVideoBackend", new=MagicMock())
    async def test_override_forces_capability_off(self, db_session):
        backend = await self._load_video_backend(db_session, overrides={"max_reference_images": 0})
        assert backend.video_capabilities.max_reference_images == 0
        assert backend.video_capabilities.first_frame is True

    @patch.dict("lib.custom_provider.endpoints.SEEDANCE_BACKENDS", {"ark-seedance-2": MagicMock()})
    async def test_unknown_key_in_stored_overrides_does_not_break_loading(self, db_session):
        # Existing rows may retain retired keys; loading must succeed, leaving that dimension system-determined.
        backend = await self._load_video_backend(
            db_session,
            overrides={"retired_dimension": True, "last_frame": True},
            endpoint="ark-seedance-2",
            model_id="seedance-1-pro",
        )
        assert backend.video_capabilities.last_frame is True

    @patch("lib.custom_provider.endpoints.OpenAIImageBackend", new=MagicMock())
    async def test_non_video_backend_untouched_by_overrides(self, db_session):
        pid = await _seed(
            db_session,
            models=[
                {
                    "model_id": "dall-e-3",
                    "endpoint": "openai-images",
                    "is_enabled": True,
                    "capability_overrides": {"last_frame": True},
                }
            ],
        )
        result = await load_custom_backend(session=db_session, provider_id=pid, model_id="dall-e-3", media_type="image")
        assert isinstance(result, CustomImageBackend)


class TestCustomModelMaxOutputTokens:
    async def test_reads_the_limit_registered_on_the_model_entry(self, async_session):
        pid = await _seed(
            async_session,
            models=[
                {
                    "model_id": "my-llm",
                    "endpoint": "openai-chat",
                    "is_enabled": True,
                    "capability_overrides": {"max_output_tokens": 8192},
                },
                {"model_id": "bare-llm", "endpoint": "openai-chat", "is_enabled": True},
            ],
        )

        registered = await custom_model_max_output_tokens(session=async_session, provider_id=pid, model_id="my-llm")
        bare = await custom_model_max_output_tokens(session=async_session, provider_id=pid, model_id="bare-llm")
        missing = await custom_model_max_output_tokens(session=async_session, provider_id=pid, model_id="gone")

        assert (registered, bare, missing) == (8192, None, None)

    async def test_override_beats_the_entry_and_the_binding_picks_the_entry(self, async_session):
        """Capability overrides outrank matched entries; without overrides use entry values, prioritizing bindings over name matching."""
        pid = await _seed(
            async_session,
            models=[
                {
                    "model_id": "overridden-llm",
                    "endpoint": "openai-chat",
                    "is_enabled": True,
                    "entry_binding": "google/gemini-3-flash-preview",
                    "capability_overrides": {"max_output_tokens": 2048},
                },
                {"model_id": "vendor/gemini-3-flash-preview", "endpoint": "openai-chat", "is_enabled": True},
                {
                    "model_id": "relay-alias",
                    "endpoint": "openai-chat",
                    "is_enabled": True,
                    "entry_binding": "google/gemini-3-flash-preview",
                },
            ],
        )

        overridden = await custom_model_max_output_tokens(
            session=async_session, provider_id=pid, model_id="overridden-llm"
        )
        from_entry = await custom_model_max_output_tokens(
            session=async_session, provider_id=pid, model_id="vendor/gemini-3-flash-preview"
        )

        bound = await custom_model_max_output_tokens(session=async_session, provider_id=pid, model_id="relay-alias")

        assert (overridden, from_entry, bound) == (2048, 65536, 65536)


class TestCustomImageModelCapabilities:
    async def test_factory_injects_model_side_from_overrides(self, async_session):
        """Image backends carry model facts: overriding text-to-image off leaves image-to-image as the only effective execution capability."""
        pid = await _seed(
            async_session,
            models=[
                {
                    "model_id": "relay-image",
                    "endpoint": "openai-images",
                    "is_enabled": True,
                    "capability_overrides": {"text_to_image": False},
                }
            ],
        )

        backend = await load_custom_backend(
            session=async_session, provider_id=pid, model_id="relay-image", media_type="image"
        )

        assert isinstance(backend, CustomImageBackend)
        assert backend.effective_capabilities == {ImageCapability.IMAGE_TO_IMAGE}
