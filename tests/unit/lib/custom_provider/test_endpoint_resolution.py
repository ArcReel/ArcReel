"""Endpoint-key prefix routing: built-in lookup, dynamic DB construction for ``ce-``, and definition-to-spec projection."""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any, cast

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from arcreel_market_core.video_backend_contract import ReferenceAudioMode, VideoAudioMode
from lib.backends.image_backends.base import ImageCapability
from lib.custom_provider import is_custom_endpoint, make_endpoint_key
from lib.custom_provider.backends import CustomVideoBackend
from lib.custom_provider.comfyui.failures import ComfyuiError
from lib.custom_provider.endpoint_resolution import (
    derive_mirror_columns,
    endpoint_spec_from_row,
    resolve_endpoint_spec,
)
from lib.custom_provider.endpoints import ENDPOINT_REGISTRY, get_endpoint_spec
from lib.db.repositories.custom_endpoint_repo import CustomEndpointRepository
from lib.generation.task_failure import FAILURE_CODE_KEYS
from tests.factories import comfyui_endpoint_definition, custom_endpoint_definition, image_endpoint_definition

if TYPE_CHECKING:
    from lib.db.models.custom_endpoint import CustomEndpoint


async def _store(session: AsyncSession, definition: dict) -> int:
    row = await CustomEndpointRepository(session).create(
        definition=definition,
        kind="declarative",
        schema_version="1.0.0",
        media_type="video",
        display_name=definition["meta"]["name"],
    )
    await session.commit()
    return row.id


class TestBuiltinRegistryInvariant:
    def test_no_builtin_key_uses_custom_prefix(self):
        """Built-in keys occupying ce- would destroy unique prefix routing; import invariants prevent this."""
        assert not [key for key in ENDPOINT_REGISTRY if is_custom_endpoint(key)]


class TestSpecFromRow:
    """User and shipped definitions share declarative_endpoint_spec projection (definition-to-spec defaults, family, path stripping,
    etc. are covered by test_builtin_endpoint_definitions); only guard ce--specific fields here."""

    async def test_projects_identity_and_source(self, db_session: AsyncSession):
        endpoint_id = await _store(db_session, custom_endpoint_definition())
        row = await CustomEndpointRepository(db_session).get(endpoint_id)
        assert row is not None

        spec = endpoint_spec_from_row(row)

        assert spec.key == f"ce-{endpoint_id}"
        assert spec.media_type == "video"
        # Source controls catalog grouping and edit/delete availability; do not derive family from the key's first segment ("ce").
        # User-defined protocols describe themselves in definitions and belong to no external family.
        assert spec.source == "custom"
        assert spec.family == "custom"
        assert spec.kind == "declarative"
        assert spec.display_name == "示例端点"
        assert spec.display_name_key == ""
        assert spec.request_method == "POST"
        # Providers supply base_url; the catalog displays API paths.
        assert spec.request_path_template == "/v1/video/create"

    async def test_capabilities_reach_the_spec(self, db_session: AsyncSession):
        definition = custom_endpoint_definition()
        definition["inputs"]["voice"] = {"source": "reference_audio_files", "encoding": "base64"}
        definition["submit"]["body"]["voices"] = [{"$each": {"in": "inputs.voice", "as": "clip", "item": "{{ clip }}"}}]
        definition["capabilities"] = {
            "first_frame": True,
            "reference_audio_mode": "direct",
            "max_reference_audio_count": 2,
        }
        endpoint_id = await _store(db_session, definition)
        row = await CustomEndpointRepository(db_session).get(endpoint_id)
        assert row is not None

        spec = endpoint_spec_from_row(row)

        assert spec.reference_audio_capable is True
        assert spec.video_caps_for_model is not None
        assert spec.video_caps_for_model("m", None).reference_audio_mode is ReferenceAudioMode.DIRECT


class TestDeclarativeImageProjection:
    """Project image definitions as image endpoints: explicit definition media/capabilities construct an image channel."""

    def test_mirror_columns_carry_the_declared_image_media_type(self):
        assert derive_mirror_columns(image_endpoint_definition()).media_type == "image"

    def test_declared_text_to_image_becomes_the_endpoint_image_capability(self):
        row = SimpleNamespace(id=7, definition=image_endpoint_definition())

        spec = endpoint_spec_from_row(cast("CustomEndpoint", row))

        assert spec.media_type == "image"
        assert spec.image_capabilities == frozenset({ImageCapability.TEXT_TO_IMAGE})
        assert spec.video_caps_for_model is None

    def test_declared_image_to_image_joins_text_to_image_in_the_endpoint_capabilities(self):
        definition = image_endpoint_definition(
            capabilities={"text_to_image": True, "image_to_image": True, "max_reference_images": 4}
        )
        row = SimpleNamespace(id=7, definition=definition)

        spec = endpoint_spec_from_row(cast("CustomEndpoint", row))

        assert spec.image_capabilities == frozenset({ImageCapability.TEXT_TO_IMAGE, ImageCapability.IMAGE_TO_IMAGE})

    def test_an_image_spec_builds_the_image_channel(self):
        from lib.custom_provider.backends import CustomImageBackend

        row = SimpleNamespace(id=7, definition=image_endpoint_definition())
        provider = SimpleNamespace(provider_id="custom-1", base_url="https://relay.test", api_key="sk")

        backend = endpoint_spec_from_row(cast("CustomEndpoint", row)).build_backend(
            cast("Any", provider), "gpt-image-2"
        )

        assert isinstance(backend, CustomImageBackend)
        assert (backend.name, backend.model) == ("custom-1", "gpt-image-2")
        assert backend.capabilities == {ImageCapability.TEXT_TO_IMAGE}


class TestKindDispatch:
    """Definition ``kind`` selects projection; reject unknown kinds at projection instead of using another kind's rules as fallback."""

    def test_mirror_columns_take_kind_and_media_type_from_the_definition(self):
        mirror = derive_mirror_columns(custom_endpoint_definition())

        assert mirror.kind == "declarative"
        assert mirror.media_type == "video"

    @pytest.mark.parametrize("media_type", ["image", "video"])
    def test_a_comfyui_definition_declares_its_own_media_type(self, media_type: str):
        mirror = derive_mirror_columns(comfyui_endpoint_definition(media_type=media_type))

        assert mirror.kind == "comfyui"
        assert mirror.media_type == media_type

    def test_spec_from_a_comfyui_row_reads_the_definition(self):
        """Project ComfyUI rows to specs: derive keys from row IDs, read media_type/kind from definitions, and mark source custom."""
        row = SimpleNamespace(id=7, definition=comfyui_endpoint_definition())

        spec = endpoint_spec_from_row(cast("CustomEndpoint", row))

        assert spec.key == "ce-7"
        assert spec.kind == "comfyui"
        assert spec.media_type == "video"
        assert spec.source == "custom"
        assert spec.display_name == "示例 ComfyUI 端点"

    def test_a_comfyui_spec_reads_its_capabilities_off_the_bindings(self):
        """This fixture has no image bindings: pure text-to-video, without first/last-frame or reference-image support."""
        row = SimpleNamespace(id=7, definition=comfyui_endpoint_definition())

        spec = endpoint_spec_from_row(cast("CustomEndpoint", row))
        caps = spec.video_caps_for_model("wan-t2v", None) if spec.video_caps_for_model else None

        assert caps is not None
        assert caps.text_to_video is True
        assert caps.first_frame is False
        assert caps.max_reference_images == 0
        assert spec.end_image_capable is False
        assert spec.reference_audio_capable is False

    def test_the_dimensions_the_bindings_say_nothing_about_stay_unclaimed(self):
        """Dimensions without matching binding semantic keys retain ``VideoCapabilities`` defaults rather than invent declarations."""
        row = SimpleNamespace(id=7, definition=comfyui_endpoint_definition())

        spec = endpoint_spec_from_row(cast("CustomEndpoint", row))
        caps = spec.video_caps_for_model("m", None) if spec.video_caps_for_model else None

        assert caps is not None
        assert caps.reference_audio_mode is ReferenceAudioMode.NONE
        assert caps.max_reference_audio_count == 0
        assert caps.max_prompt_chars is None
        assert caps.first_frame_ratio_adaptive_only is False

    def test_the_audio_track_is_read_off_the_output_chain_and_is_never_controllable(self):
        definition = comfyui_endpoint_definition()
        definition["workflow"]["20"] = {"class_type": "VHS_VideoCombine", "inputs": {"audio": ["21", 0]}}
        definition["bindings"]["output"] = [{"node": "20", "class_type": "VHS_VideoCombine"}]
        row = SimpleNamespace(id=7, definition=definition)

        spec = endpoint_spec_from_row(cast("CustomEndpoint", row))
        caps = spec.video_caps_for_model("m", None) if spec.video_caps_for_model else None

        assert caps is not None
        assert caps.audio_track is VideoAudioMode.ALWAYS_ON
        assert caps.audio_track_for_route("r2v") is VideoAudioMode.ALWAYS_ON

    def test_the_size_dimension_is_fixed_unless_both_sides_are_bound(self):
        drivable = SimpleNamespace(id=7, definition=comfyui_endpoint_definition())
        one_sided = comfyui_endpoint_definition()
        one_sided["bindings"].pop("height")

        assert endpoint_spec_from_row(cast("CustomEndpoint", drivable)).size_fixed is False
        assert endpoint_spec_from_row(cast("CustomEndpoint", SimpleNamespace(id=7, definition=one_sided))).size_fixed

    def test_the_duration_dimension_is_fixed_when_frames_are_unbound(self):
        """``duration_fixed`` differs from ``duration_tier_optional``; the former determines whether users can edit tiers."""
        fixed = SimpleNamespace(id=7, definition=comfyui_endpoint_definition())
        drivable = comfyui_endpoint_definition()
        drivable["bindings"]["frames"] = [{"node": "5", "input": "length", "class_type": "EmptyLatentImage"}]

        assert endpoint_spec_from_row(cast("CustomEndpoint", fixed)).duration_fixed is True
        spec = endpoint_spec_from_row(cast("CustomEndpoint", SimpleNamespace(id=7, definition=drivable)))
        assert spec.duration_fixed is False
        assert spec.duration_tier_optional is True

    def test_all_three_ways_of_having_no_tier_report_the_same_empty_bit(self):
        """UI read-only/disabled state depends on empty tiers and must hold for all three branches; the other flags only select copy."""
        unbound = SimpleNamespace(id=7, definition=comfyui_endpoint_definition())
        no_fps = comfyui_endpoint_definition()
        no_fps["workflow"]["5"]["inputs"]["length"] = 81
        no_fps["bindings"].pop("fps")
        no_fps["bindings"]["frames"] = [{"node": "5", "input": "length", "class_type": "EmptyLatentImage"}]
        # 50 frames @ 16fps: frame rate is readable, but 3-second conversion writes back 49 frames; no valid tier.
        no_whole_second = comfyui_endpoint_definition()
        no_whole_second["workflow"]["5"]["inputs"]["length"] = 50
        no_whole_second["bindings"]["frames"] = [{"node": "5", "input": "length", "class_type": "EmptyLatentImage"}]
        derivable = comfyui_endpoint_definition()
        derivable["workflow"]["5"]["inputs"]["length"] = 81
        derivable["bindings"]["frames"] = [{"node": "5", "input": "length", "class_type": "EmptyLatentImage"}]

        def spec_of(definition: dict):
            return endpoint_spec_from_row(cast("CustomEndpoint", SimpleNamespace(id=7, definition=definition)))

        def bits_of(definition: dict) -> tuple[bool, bool, bool]:
            spec = spec_of(definition)
            return (spec.duration_tier_empty, spec.duration_fixed, spec.duration_frame_rate_missing)

        assert bits_of(unbound.definition) == (True, True, False)
        assert bits_of(no_fps) == (True, False, True)
        assert bits_of(no_whole_second) == (True, False, False)
        assert bits_of(derivable) == (False, False, False)
        assert get_endpoint_spec("openai-video").duration_tier_empty is False
        assert get_endpoint_spec("openai-video").duration_frame_rate_missing is False

    def test_a_native_duration_that_cannot_round_trip_is_no_tier_at_all(self):
        """81 frames @ 24fps converts to 3 seconds, but selecting 3 seconds makes construction write 73 frames.

        A native tier that modifies the original graph when selected is worse than no tier, so treat this dimension as not driven
        by ArcReel. The same frame count at 16fps round-trips exactly and still reports 5 seconds.
        """
        off = comfyui_endpoint_definition()
        off["workflow"]["5"]["inputs"]["length"] = 81
        off["workflow"]["9"]["inputs"]["fps"] = 24
        off["bindings"]["frames"] = [{"node": "5", "input": "length", "class_type": "EmptyLatentImage"}]
        exact = json.loads(json.dumps(off))
        exact["workflow"]["9"]["inputs"]["fps"] = 16

        def spec_of(definition: dict):
            return endpoint_spec_from_row(cast("CustomEndpoint", SimpleNamespace(id=7, definition=definition)))

        assert spec_of(off).duration_tier_empty is True
        assert spec_of(exact).duration_tier_empty is False
        assert spec_of(exact).endpoint_durations == [5]

    def test_the_native_resolution_borrows_a_tier_word_only_on_an_exact_hit(self):
        """Fixture literal dimensions are 832 × 480; short side 480 exactly matches video tier 480p."""
        row = SimpleNamespace(id=7, definition=comfyui_endpoint_definition())

        assert endpoint_spec_from_row(cast("CustomEndpoint", row)).native_resolution == "480p"

    def test_a_short_edge_off_the_tiers_is_reported_as_pixels(self):
        """848 is neither 720p nor 1080p; reporting the nearest tier falsely suggests selecting it produces the same image."""
        definition = comfyui_endpoint_definition()
        definition["workflow"]["5"]["inputs"]["width"] = 848
        definition["workflow"]["5"]["inputs"]["height"] = 1504
        row = SimpleNamespace(id=7, definition=definition)

        assert endpoint_spec_from_row(cast("CustomEndpoint", row)).native_resolution == "848px"

    def test_an_unreadable_literal_size_leaves_the_native_resolution_unknown(self):
        definition = comfyui_endpoint_definition()
        definition["workflow"]["5"]["inputs"]["width"] = "832"
        definition["workflow"]["5"]["inputs"]["height"] = "480"
        row = SimpleNamespace(id=7, definition=definition)

        assert endpoint_spec_from_row(cast("CustomEndpoint", row)).native_resolution is None

    def test_a_one_sided_size_binding_reports_no_native_resolution(self):
        """Binding only one side fixes the whole size dimension; construction never reaches literal-short-side handling.

        The readable literal may not even be the short side: calling long side 832 native 832px implies that omitting a tier
        gives a short side of 832. This branch instead takes placeholder copy from ``size_fixed``.
        """
        definition = comfyui_endpoint_definition()
        definition["bindings"].pop("height")
        row = SimpleNamespace(id=7, definition=definition)

        spec = endpoint_spec_from_row(cast("CustomEndpoint", row))

        assert (spec.size_fixed, spec.native_resolution) == (True, None)

    def test_a_declarative_endpoint_declares_no_fixed_dimension(self):
        """Endpoint-fixed size/duration is specific to ComfyUI; other endpoints follow request parameters."""
        spec = get_endpoint_spec("openai-video")

        assert (spec.size_fixed, spec.duration_fixed, spec.native_resolution) == (False, False, None)

    def test_only_a_comfyui_spec_lets_its_duration_tier_be_empty(self):
        """ADR 0018 empty-set fail-loud behavior remains for other endpoints; the spec exposes the criterion directly to capability resolution."""
        row = SimpleNamespace(id=7, definition=comfyui_endpoint_definition())

        assert endpoint_spec_from_row(cast("CustomEndpoint", row)).duration_tier_optional is True
        assert get_endpoint_spec("openai-video").duration_tier_optional is False

    def test_the_model_name_does_not_enter_the_derivation(self):
        """One workflow is exactly one model; renaming model rows does not change its capabilities."""
        definition = comfyui_endpoint_definition()
        definition["bindings"]["start_image"] = [{"node": "11", "input": "image", "class_type": "LoadImage"}]
        row = SimpleNamespace(id=7, definition=definition)

        spec = endpoint_spec_from_row(cast("CustomEndpoint", row))
        caps_fn = spec.video_caps_for_model

        assert caps_fn is not None
        assert caps_fn("wan-t2v", None) == caps_fn("完全不相干的名字", None)
        assert caps_fn("wan-t2v", None).first_frame is True
        assert caps_fn("wan-t2v", None).text_to_video is False

    def test_the_end_frame_binding_reaches_the_end_image_transport_bit(self):
        """Handle ``end_image_capable`` like declarative endpoints: derive from caps without separate criteria."""
        definition = comfyui_endpoint_definition()
        definition["bindings"]["end_image"] = [{"node": "12", "input": "image", "class_type": "LoadImage"}]
        row = SimpleNamespace(id=7, definition=definition)

        assert endpoint_spec_from_row(cast("CustomEndpoint", row)).end_image_capable is True

    def test_an_image_comfyui_spec_derives_its_image_capabilities(self):
        definition = comfyui_endpoint_definition(media_type="image")
        row = SimpleNamespace(id=7, definition=definition)

        spec = endpoint_spec_from_row(cast("CustomEndpoint", row))

        assert spec.image_capabilities == frozenset({ImageCapability.TEXT_TO_IMAGE})
        assert spec.video_caps_for_model is None

    def test_a_comfyui_spec_builds_the_comfyui_video_backend(self):
        """Projection branches by kind to Python ComfyUI backends, retaining the custom-provider wrapper."""
        row = SimpleNamespace(id=7, definition=comfyui_endpoint_definition())
        provider = SimpleNamespace(provider_id="custom-1", base_url="https://comfy.test", api_key="")

        spec = endpoint_spec_from_row(cast("CustomEndpoint", row))
        backend = spec.build_backend(cast("Any", provider), "wan-t2v")

        assert isinstance(backend, CustomVideoBackend)
        assert backend.name == "custom-1"
        assert backend.model == "wan-t2v"

    def test_building_a_comfyui_backend_without_a_base_url_is_refused(self):
        """Without an address, submission has no destination; use the nonexistent-endpoint exit so upstream handles it through existing routing."""
        row = SimpleNamespace(id=7, definition=comfyui_endpoint_definition())
        provider = SimpleNamespace(provider_id="custom-1", base_url="", api_key="")

        spec = endpoint_spec_from_row(cast("CustomEndpoint", row))

        with pytest.raises(ValueError, match="base_url"):
            spec.build_backend(cast("Any", provider), "wan-t2v")

    def test_building_a_backend_for_a_comfyui_image_spec_gives_the_image_channel(self):
        """Media type selects the channel: image definitions construct image-backend wrappers, not video wrappers."""
        from lib.custom_provider.backends import CustomImageBackend

        definition = comfyui_endpoint_definition(media_type="image")
        row = SimpleNamespace(id=7, definition=definition)
        provider = SimpleNamespace(provider_id="custom-1", base_url="https://comfy.test", api_key="")

        spec = endpoint_spec_from_row(cast("CustomEndpoint", row))
        backend = spec.build_backend(cast("Any", provider), "flux")

        assert isinstance(backend, CustomImageBackend)
        assert (backend.name, backend.model) == ("custom-1", "flux")

    def test_building_a_backend_for_an_out_of_range_media_type_says_the_runtime_is_missing(self):
        """Schemas allow only image / video; a third value implies manual DB modification.

        Raise ComfyuiError with a failure code, distinct from unknown endpoints. Add the code to ``FAILURE_CODE_KEYS`` so the worker
        persists structured failure and reads render by language; plain text would show a Chinese sentence to non-Chinese users in task lists.
        """
        definition = comfyui_endpoint_definition(media_type="image")
        # This shape requires bypassing schemas through direct DB modification; do not call validate_definition.
        definition["media_type"] = "audio"
        row = SimpleNamespace(id=7, definition=definition)
        provider = SimpleNamespace(provider_id="custom-1", base_url="https://comfy.test", api_key="")

        spec = endpoint_spec_from_row(cast("CustomEndpoint", row))

        with pytest.raises(ComfyuiError) as caught:
            spec.build_backend(cast("Any", provider), "flux")

        assert caught.value.code == "provider_unsupported_media"
        assert caught.value.params == {"provider_id": "custom-1", "media_type": "audio"}
        assert caught.value.code in FAILURE_CODE_KEYS

    def test_spec_from_a_row_of_an_unsupported_kind_is_refused(self):
        """The stored kind lacks a projection implementation here: raise ValueError through the same exit as nonexistent endpoints."""
        row = SimpleNamespace(id=7, definition=custom_endpoint_definition(kind="unregistered"))

        with pytest.raises(ValueError, match="unsupported endpoint definition kind"):
            endpoint_spec_from_row(cast("CustomEndpoint", row))


class TestResolveEndpointSpec:
    async def test_builtin_key_delegates_to_registry(self, db_session: AsyncSession):
        repo = CustomEndpointRepository(db_session)
        assert await resolve_endpoint_spec("openai-video", repo.get) is get_endpoint_spec("openai-video")

    async def test_custom_key_reads_definition_from_db(self, db_session: AsyncSession):
        endpoint_id = await _store(db_session, custom_endpoint_definition())

        spec = await resolve_endpoint_spec(make_endpoint_key(endpoint_id), CustomEndpointRepository(db_session).get)

        assert spec.key == f"ce-{endpoint_id}"
        assert spec.display_name == "示例端点"

    async def test_updated_definition_resolves_to_new_spec(self, db_session: AsyncSession):
        """In-place updates immediately affect new resolutions; neither startup-wide loading nor in-process registry caching applies."""
        endpoint_id = await _store(db_session, custom_endpoint_definition())
        repo = CustomEndpointRepository(db_session)
        renamed = custom_endpoint_definition(meta={"name": "改名后", "author": "ArcReel", "version": "0.2.0"})
        await repo.update(
            endpoint_id,
            definition=renamed,
            kind="declarative",
            schema_version="1.0.0",
            media_type="video",
            display_name="改名后",
        )
        await db_session.commit()

        spec = await resolve_endpoint_spec(make_endpoint_key(endpoint_id), repo.get)

        assert spec.display_name == "改名后"

    async def test_deleted_custom_endpoint_is_unknown(self, db_session: AsyncSession):
        with pytest.raises(ValueError, match="unknown endpoint"):
            await resolve_endpoint_spec("ce-404", CustomEndpointRepository(db_session).get)

    @pytest.mark.parametrize(
        "endpoint",
        [
            "ce-not-a-number",
            "ce-",
            "ce-0",
            "ce-03",
            "ce- 3",
            "ce-+3",
            "ce--3",
            "ce-٣",
            "ce-3_0",
            "ce-3\n",
        ],
    )
    async def test_malformed_custom_key_is_unknown(self, db_session: AsyncSession, endpoint: str):
        with pytest.raises(ValueError, match="unknown endpoint"):
            await resolve_endpoint_spec(endpoint, CustomEndpointRepository(db_session).get)

    async def test_unknown_builtin_key_is_unknown(self, db_session: AsyncSession):
        with pytest.raises(ValueError, match="unknown endpoint"):
            await resolve_endpoint_spec("no-such-endpoint", CustomEndpointRepository(db_session).get)
