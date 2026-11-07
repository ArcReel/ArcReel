from typing import cast
from unittest.mock import patch

import pytest
from sqlalchemy import select

from lib.catalog.errors import ModelNotInCatalogError
from lib.catalog.query import factory_snapshot
from lib.config.resolver import (
    _VIDEO_LAYERED_KEYS,
    ConfigResolver,
    VideoBucketCapabilityError,
    VideoGenerationType,
    caps_generation_mode,
    video_bucket_for_generation_mode,
)
from lib.config.service import ProviderStatus
from tests.catalog_support import install_catalog, with_entry_changes


def _make_ready_provider(name: str, media_types: list[str]) -> ProviderStatus:
    return ProviderStatus(
        name=name,
        display_name=name,
        status="ready",
        media_types=media_types,
        capabilities=[],
        required_keys=[],
        credential_count=0,
    )


def _ready_with_vertex_video() -> list[ProviderStatus]:
    """The factory catalog has no available AI Studio video models; automatic selection advances to the next ready provider, Vertex."""
    return [
        _make_ready_provider("gemini-aistudio", ["text", "image", "video"]),
        _make_ready_provider("gemini-vertex", ["video"]),
    ]


class _FakeConfigService:
    """Minimal ConfigService fake implementing only resolver-required methods."""

    def __init__(
        self,
        settings: dict[str, str] | None = None,
        *,
        ready_providers: list[ProviderStatus] | None = None,
    ):
        self._settings = settings or {}
        self._ready_providers = ready_providers

    async def get_setting(self, key: str, default: str = "") -> str:
        return self._settings.get(key, default)

    async def get_all_settings(self) -> dict[str, str]:
        return dict(self._settings)

    async def get_provider_config(self, provider: str) -> dict[str, str]:
        return {"api_key": f"key-{provider}"}

    async def get_all_provider_configs(self) -> dict[str, dict[str, str]]:
        return {"gemini-aistudio": {"api_key": "key-aistudio"}}

    async def get_all_providers_status(self) -> list[ProviderStatus]:
        if self._ready_providers is not None:
            return self._ready_providers
        return [_make_ready_provider("gemini-aistudio", ["text", "image", "video"])]


async def _video_caps(db_factory, project: dict, *, generation_type: VideoGenerationType | None = None) -> dict:
    """Resolve video capabilities from project dicts; persistence is outside these criteria, so project_manager is only a placeholder."""
    with patch("lib.config.resolver.get_project_manager"):
        return await ConfigResolver(db_factory).video_capabilities_for_project(project, generation_type=generation_type)


class TestVideoGenerateAudio:
    """Verify video_generate_audio defaults, global configuration, and project-override priority."""

    async def test_default_is_true_when_db_empty(self, tmp_path):
        """Absent DB values return True, matching Seedance/Grok audio-enabled defaults."""
        resolver = ConfigResolver.__new__(ConfigResolver)
        fake_svc = _FakeConfigService(settings={})
        result = await resolver._resolve_video_generate_audio(fake_svc, project_name=None)
        assert result is True

    async def test_global_true(self, tmp_path):
        """DB "true" returns True."""
        resolver = ConfigResolver.__new__(ConfigResolver)
        fake_svc = _FakeConfigService(settings={"video_generate_audio": "true"})
        result = await resolver._resolve_video_generate_audio(fake_svc, project_name=None)
        assert result is True

    async def test_global_false(self, tmp_path):
        """DB "false" returns False."""
        resolver = ConfigResolver.__new__(ConfigResolver)
        fake_svc = _FakeConfigService(settings={"video_generate_audio": "false"})
        result = await resolver._resolve_video_generate_audio(fake_svc, project_name=None)
        assert result is False

    async def test_bool_parsing_variants(self, tmp_path):
        """Verify parsing of boolean string variants."""
        resolver = ConfigResolver.__new__(ConfigResolver)
        for val, expected in [("TRUE", True), ("1", True), ("yes", True), ("0", False), ("no", False), ("", True)]:
            fake_svc = _FakeConfigService(settings={"video_generate_audio": val} if val else {})
            result = await resolver._resolve_video_generate_audio(fake_svc, project_name=None)
            assert result is expected, f"Failed for {val!r}: got {result}"

    async def test_project_override_true_over_global_false(self, tmp_path):
        """Project True overrides global False."""
        resolver = ConfigResolver.__new__(ConfigResolver)
        fake_svc = _FakeConfigService(settings={"video_generate_audio": "false"})
        with patch("lib.config.resolver.get_project_manager") as mock_pm:
            mock_pm.return_value.load_project.return_value = {"video_generate_audio": True}
            result = await resolver._resolve_video_generate_audio(fake_svc, project_name="demo")
        assert result is True

    async def test_project_override_false_over_global_true(self, tmp_path):
        """Project False overrides global True."""
        resolver = ConfigResolver.__new__(ConfigResolver)
        fake_svc = _FakeConfigService(settings={"video_generate_audio": "true"})
        with patch("lib.config.resolver.get_project_manager") as mock_pm:
            mock_pm.return_value.load_project.return_value = {"video_generate_audio": False}
            result = await resolver._resolve_video_generate_audio(fake_svc, project_name="demo")
        assert result is False

    async def test_project_none_skips_override(self, tmp_path):
        """With project_name=None, do not read project configuration.

        Prepare an outcome-changing project override (global False, project True): missing the project_name
        short-circuit would return True. Asserting False proves the project layer was not read.
        """
        resolver = ConfigResolver.__new__(ConfigResolver)
        fake_svc = _FakeConfigService(settings={"video_generate_audio": "false"})
        with patch("lib.config.resolver.get_project_manager") as mock_pm:
            mock_pm.return_value.load_project.return_value = {"video_generate_audio": True}
            result = await resolver._resolve_video_generate_audio(fake_svc, project_name=None)
        assert result is False

    async def test_project_override_string_value(self, tmp_path):
        """Parse project-level string overrides correctly too."""
        resolver = ConfigResolver.__new__(ConfigResolver)
        fake_svc = _FakeConfigService(settings={"video_generate_audio": "true"})
        with patch("lib.config.resolver.get_project_manager") as mock_pm:
            mock_pm.return_value.load_project.return_value = {"video_generate_audio": "false"}
            result = await resolver._resolve_video_generate_audio(fake_svc, project_name="demo")
        assert result is False


class TestDefaultBackends:
    """Verify explicit versus automatically resolved video/image backends."""

    async def test_video_backend_explicit(self):
        """Return explicit DB values directly."""
        resolver = ConfigResolver.__new__(ConfigResolver)
        fake_svc = _FakeConfigService(
            settings={"default_video_backend": "ark/doubao-seedance-1-5-pro"},
        )
        result = await resolver._resolve_default_video_backend(fake_svc, None)
        assert result == ("ark", "doubao-seedance-1-5-pro")

    async def test_video_backend_auto_resolve(self, db_factory):
        """Without a DB value, automatically select the first ready provider with a default video model."""
        resolver = ConfigResolver.__new__(ConfigResolver)
        fake_svc = _FakeConfigService(settings={}, ready_providers=_ready_with_vertex_video())
        # Automatic resolution finds a ready PROVIDER_REGISTRY provider before the custom-provider branch.
        async with db_factory() as session:
            result = await resolver._resolve_default_video_backend(fake_svc, session)
        assert result == ("gemini-vertex", "veo-3.1-fast-generate-001")

    async def test_video_backend_auto_resolve_no_ready_provider(self, db_factory):
        """Without ready or custom providers, raise ValueError."""
        resolver = ConfigResolver.__new__(ConfigResolver)
        fake_svc = _FakeConfigService(settings={}, ready_providers=[])
        async with db_factory() as session:
            with pytest.raises(ValueError, match="未找到可用的 video 供应商"):
                await resolver._resolve_default_video_backend(fake_svc, session)

    async def test_image_backend_explicit(self):
        """Return explicit DB values directly."""
        resolver = ConfigResolver.__new__(ConfigResolver)
        fake_svc = _FakeConfigService(
            settings={"default_image_backend": "grok/grok-2-image"},
        )
        result = await resolver._resolve_default_image_backend(fake_svc, None)
        assert result == ("grok", "grok-2-image")

    async def test_image_backend_auto_resolve(self, db_factory):
        """Without a DB value, resolve automatically."""
        resolver = ConfigResolver.__new__(ConfigResolver)
        fake_svc = _FakeConfigService(settings={})
        async with db_factory() as session:
            result = await resolver._resolve_default_image_backend(fake_svc, session)
        assert result[0] in ("gemini-aistudio", "gemini-vertex", "ark", "grok")

    async def test_image_backend_auto_resolve_no_ready_provider(self, db_factory):
        """Without ready or custom providers, raise ValueError."""
        resolver = ConfigResolver.__new__(ConfigResolver)
        fake_svc = _FakeConfigService(settings={}, ready_providers=[])
        async with db_factory() as session:
            with pytest.raises(ValueError, match="未找到可用的 image 供应商"):
                await resolver._resolve_default_image_backend(fake_svc, session)

    @pytest.mark.parametrize(("generation_type", "expected_model"), [("t2i", "t2i-m"), ("i2i", "i2i-m")])
    async def test_image_backend_auto_resolve_picks_the_custom_default_of_the_bucket(
        self, db_factory, generation_type, expected_model
    ):
        """Without ready built-ins, custom-provider fallback selects bucket defaults; separate t2i/i2i defaults must not choose the wrong bucket."""
        from lib.custom_provider import make_provider_id
        from lib.db.models.custom_provider import CustomProvider, CustomProviderModel

        resolver = ConfigResolver.__new__(ConfigResolver)
        fake_svc = _FakeConfigService(settings={}, ready_providers=[])
        async with db_factory() as session:
            provider = CustomProvider(
                display_name="Prov", discovery_format="openai", base_url="https://api.example.com", api_key="k"
            )
            session.add(provider)
            await session.flush()
            session.add_all(
                [
                    # openai-images-generations declares only t2i; openai-images-edits only i2i.
                    CustomProviderModel(
                        provider_id=provider.id,
                        model_id="t2i-m",
                        display_name="T2I",
                        endpoint="openai-images-generations",
                        is_default=True,
                        is_enabled=True,
                    ),
                    CustomProviderModel(
                        provider_id=provider.id,
                        model_id="i2i-m",
                        display_name="I2I",
                        endpoint="openai-images-edits",
                        is_default=True,
                        is_enabled=True,
                    ),
                ]
            )
            await session.flush()

            result = await resolver._resolve_default_image_backend(fake_svc, session, generation_type)
        assert result == (make_provider_id(provider.id), expected_model)

    async def test_default_image_backend_t2i_bucket_overrides_default_layer(self):
        """Global bucket default_image_backend_t2i overrides global default_image_backend."""
        resolver = ConfigResolver.__new__(ConfigResolver)
        fake_svc = _FakeConfigService(
            settings={
                "default_image_backend": "grok/grok-2-image",
                "default_image_backend_t2i": "ark/stable-diffusion-3",
            },
        )
        result = await resolver._resolve_default_image_backend(fake_svc, None, "t2i")
        assert result == ("ark", "stable-diffusion-3")

    async def test_default_image_backend_i2i_bucket_overrides_default_layer(self):
        """Symmetric i2i case: global bucket overrides the global default layer."""
        resolver = ConfigResolver.__new__(ConfigResolver)
        fake_svc = _FakeConfigService(
            settings={
                "default_image_backend": "grok/grok-2-image",
                "default_image_backend_i2i": "ark/kolors-img2img",
            },
        )
        result = await resolver._resolve_default_image_backend(fake_svc, None, "i2i")
        assert result == ("ark", "kolors-img2img")

    @pytest.mark.parametrize("generation_type", ["t2i", "i2i"])
    async def test_default_image_backend_empty_bucket_falls_back_to_default_layer(
        self, db_factory, generation_type: str
    ):
        """Empty bucket keys fall back to the default layer (docs/adr/0054).

        Semantic guard: buckets are optional overrides; empty no longer means no default / automatic choice. ready_providers=[]
        makes automatic selection fail, distinguishing expected default-layer fallback from jumping to automatic selection.
        """
        resolver = ConfigResolver.__new__(ConfigResolver)
        fake_svc = _FakeConfigService(
            settings={
                "default_image_backend": "grok/grok-2-image",
                f"default_image_backend_{generation_type}": "",
            },
            ready_providers=[],
        )
        async with db_factory() as session:
            result = await resolver._resolve_default_image_backend(fake_svc, session, generation_type)
        assert result == ("grok", "grok-2-image")

    @pytest.mark.parametrize("generation_type", ["t2i", "i2i"])
    async def test_default_image_backend_only_default_layer_covers_all_buckets(self, generation_type: str):
        """With only default_image_backend and neither bucket configured, all image paths resolve to that default model."""
        resolver = ConfigResolver.__new__(ConfigResolver)
        fake_svc = _FakeConfigService(settings={"default_image_backend": "grok/grok-2-image"})
        result = await resolver._resolve_default_image_backend(fake_svc, None, generation_type)
        assert result == ("grok", "grok-2-image")


class TestProviderConfig:
    """Verify provider configuration methods delegate to ConfigService."""

    async def test_provider_config(self, db_factory):
        resolver = ConfigResolver.__new__(ConfigResolver)
        fake_svc = _FakeConfigService()
        async with db_factory() as session:
            result = await resolver._resolve_provider_config(fake_svc, session, "gemini-aistudio")
        assert result == {"api_key": "key-gemini-aistudio"}

    async def test_all_provider_configs(self, db_factory):
        resolver = ConfigResolver.__new__(ConfigResolver)
        fake_svc = _FakeConfigService()
        async with db_factory() as session:
            result = await resolver._resolve_all_provider_configs(fake_svc, session)
        assert "gemini-aistudio" in result


class TestSessionReuse:
    """Verify session reuse in the session() context manager."""

    async def test_session_context_manager_reuses_single_session(self, db_factory):
        """Multiple calls under resolver.session() create only 1 session."""
        call_count = 0
        real_call = db_factory.__call__

        def counting_factory():
            nonlocal call_count
            call_count += 1
            return real_call()

        resolver = ConfigResolver(db_factory)
        fake_backend = ("gemini-aistudio", "test-model")

        # Without session(), each call creates a new session.
        call_count = 0
        with (
            patch.object(resolver, "_session_factory", side_effect=counting_factory),
            patch.object(resolver, "_resolve_default_video_backend", return_value=fake_backend),
            patch.object(resolver, "_resolve_default_image_backend", return_value=fake_backend),
        ):
            await resolver.default_video_backend()
            await resolver.default_image_backend()
        assert call_count == 2, f"不使用 session() 应创建 2 个 session，实际 {call_count}"

        # With session(), create only 1 session.
        call_count = 0
        with patch.object(resolver, "_session_factory", side_effect=counting_factory):
            async with resolver.session() as r:
                with (
                    patch.object(r, "_resolve_default_video_backend", return_value=fake_backend),
                    patch.object(r, "_resolve_default_image_backend", return_value=fake_backend),
                    patch.object(r, "_resolve_video_generate_audio", return_value=False),
                ):
                    await r.default_video_backend()
                    await r.default_image_backend()
                    await r.video_generate_audio()
        # session() creates one; internal calls reuse the bound session.
        assert call_count == 1, f"使用 session() 应只创建 1 个 session，实际 {call_count}"

    async def test_bound_resolver_shares_session_object(self, db_factory):
        """Bound resolver _open_session returns the same session object."""
        resolver = ConfigResolver(db_factory)
        sessions_seen = []

        async with resolver.session() as r:
            async with r._open_session() as (s1, _):
                sessions_seen.append(s1)
            async with r._open_session() as (s2, _):
                sessions_seen.append(s2)

        assert sessions_seen[0] is sessions_seen[1]

    async def test_unbound_resolver_creates_separate_sessions(self, db_factory):
        """Unbound resolver _open_session creates distinct sessions per call."""
        resolver = ConfigResolver(db_factory)
        sessions_seen = []

        async with resolver._open_session() as (s1, _):
            sessions_seen.append(s1)
        async with resolver._open_session() as (s2, _):
            sessions_seen.append(s2)

        assert sessions_seen[0] is not sessions_seen[1]


class TestVideoBackendThreeLevelPriority:
    """Verify three-level video_backend priority: project > system > automatic resolution."""

    async def test_project_override_wins_over_system_setting(self):
        resolver = ConfigResolver.__new__(ConfigResolver)
        fake_svc = _FakeConfigService(
            settings={"default_video_backend": "grok/grok-imagine-video"},
        )
        with patch("lib.config.resolver.get_project_manager") as mock_pm:
            mock_pm.return_value.load_project.return_value = {
                "video_backend": "gemini-aistudio/veo-3.1-generate-preview",
            }
            result = await resolver._resolve_video_backend(fake_svc, None, "demo")
        assert result == ("gemini-aistudio", "veo-3.1-generate-preview")

    async def test_project_empty_falls_back_to_system_setting(self):
        resolver = ConfigResolver.__new__(ConfigResolver)
        fake_svc = _FakeConfigService(
            settings={"default_video_backend": "grok/grok-imagine-video"},
        )
        with patch("lib.config.resolver.get_project_manager") as mock_pm:
            mock_pm.return_value.load_project.return_value = {}
            result = await resolver._resolve_video_backend(fake_svc, None, "demo")
        assert result == ("grok", "grok-imagine-video")

    async def test_no_project_name_uses_system_setting(self):
        resolver = ConfigResolver.__new__(ConfigResolver)
        fake_svc = _FakeConfigService(
            settings={"default_video_backend": "ark/doubao-seedance-2-0-260128"},
        )
        result = await resolver._resolve_video_backend(fake_svc, None, None)
        assert result == ("ark", "doubao-seedance-2-0-260128")


class TestVideoCapabilitiesBucketing:
    """Read-side generation_mode bucket selection queries capabilities of the model that current configuration will actually execute."""

    def test_generation_mode_maps_to_bucket(self):
        assert video_bucket_for_generation_mode("storyboard") == "i2v"
        assert video_bucket_for_generation_mode("reference_video") == "r2v"
        # Missing/unknown values, retired three-valued grid, and invalid nonstrings all select the default bucket.
        assert video_bucket_for_generation_mode(None) == "i2v"
        assert video_bucket_for_generation_mode("bogus") == "i2v"
        assert video_bucket_for_generation_mode("grid") == "i2v"
        assert video_bucket_for_generation_mode(cast(str, ["reference_video"])) == "i2v"

    async def test_i2v_bucket_shadows_project_default(self, db_factory):
        """Image-to-video projects read i2v, shadowing project defaults."""
        caps = await _video_caps(
            db_factory,
            {
                "video_backend": "grok/grok-imagine-video",
                "video_provider_i2v": "kling/kling-v3",
                "generation_mode": "storyboard",
            },
        )
        assert (caps["provider_id"], caps["model"]) == ("kling", "kling-v3")

    async def test_r2v_bucket_shadows_project_default(self, db_factory):
        """Reference-to-video projects read r2v, shadowing project defaults."""
        caps = await _video_caps(
            db_factory,
            {
                "video_backend": "grok/grok-imagine-video",
                "video_provider_r2v": "dashscope/happyhorse-1.1-r2v",
                "generation_mode": "reference_video",
            },
        )
        assert (caps["provider_id"], caps["model"]) == ("dashscope", "happyhorse-1.1-r2v")

    async def test_same_config_follows_generation_mode(self, db_factory):
        """Changing generation_mode under the same configuration switches capability queries to the other bucket's model."""
        project = {
            "video_provider_i2v": "kling/kling-v3",
            "video_provider_r2v": "dashscope/happyhorse-1.1-r2v",
        }
        i2v_caps = await _video_caps(db_factory, {**project, "generation_mode": "storyboard"})
        r2v_caps = await _video_caps(db_factory, {**project, "generation_mode": "reference_video"})
        assert i2v_caps["model"] == "kling-v3"
        assert r2v_caps["model"] == "happyhorse-1.1-r2v"
        # Capability dicts change too: the i2v model rejects reference images.
        assert i2v_caps["max_reference_images"] == 0
        assert r2v_caps["max_reference_images"] == 9

    async def test_reference_video_project_errors_when_model_lacks_reference_support(self, db_factory):
        """Reference-to-video resolution to a model without reference images raises structured errors, without silently changing models."""
        with pytest.raises(VideoBucketCapabilityError) as excinfo:
            await _video_caps(db_factory, {"video_backend": "kling/kling-v3", "generation_mode": "reference_video"})
        assert excinfo.value.code == "video_capability_missing_r2v"
        assert excinfo.value.params == {"provider": "kling", "model": "kling-v3"}

    async def test_storyboard_project_errors_when_model_lacks_first_frame(self, db_factory):
        """Image-to-video resolution without first-frame support also errors, using the i2v bucket."""
        with pytest.raises(VideoBucketCapabilityError) as excinfo:
            await _video_caps(
                db_factory, {"video_backend": "dashscope/happyhorse-1.1-r2v", "generation_mode": "storyboard"}
            )
        assert excinfo.value.code == "video_capability_missing_i2v"

    async def test_reference_project_reads_the_r2v_bucket_model(self, db_factory):
        """Reference-to-video project capabilities come from the effective r2v model declaration."""
        project = {
            "video_provider_i2v": "kling/kling-v3",
            "video_provider_r2v": "gemini-vertex/veo-3.1-generate-001",
            "generation_mode": "reference_video",
        }
        caps = await _video_caps(db_factory, project)
        assert caps["model"] == "veo-3.1-generate-001"
        assert caps["supported_durations"] == [4, 6, 8]

    async def test_max_reference_images_follows_backend_declaration(self, db_factory):
        """viduq3-pro is not allowlisted for /reference2video; capability queries report 0, not parallel registry declarations."""
        caps = await _video_caps(db_factory, {"video_backend": "vidu/viduq3-pro", "generation_mode": "storyboard"})
        assert caps["max_reference_images"] == 0


class TestVideoCapabilities:
    """Verify video_capabilities_for_project: model selection first, then model capability lookup."""

    async def test_reads_project_default_duration_and_modes(self, db_factory):
        caps = await _video_caps(
            db_factory,
            {
                "video_backend": "grok/grok-imagine-video",
                "default_duration": 6,
                "content_mode": "narration",
                "generation_mode": "reference_video",
            },
        )
        assert caps["default_duration"] == 6
        assert caps["content_mode"] == "narration"
        assert caps["generation_mode"] == "reference_video"

    async def test_missing_default_duration_is_null(self, db_factory):
        caps = await _video_caps(db_factory, {"video_backend": "grok/grok-imagine-video"})
        assert caps["default_duration"] is None

    async def test_video_capabilities_for_project_uses_passed_dict(self, db_factory):
        """video_capabilities_for_project(dict) consumes its dict directly without load_project.

        Guard the Codex-review risk that a second directory-name-based load could read a wrong same-named project.
        """
        resolver = ConfigResolver(db_factory)
        with patch("lib.config.resolver.get_project_manager") as mock_pm:
            caps = await resolver.video_capabilities_for_project(
                {
                    "video_backend": "grok/grok-imagine-video",
                    "default_duration": 9,
                }
            )
            # Critical assertion: never call load_project.
            mock_pm.return_value.load_project.assert_not_called()
        assert caps["provider_id"] == "grok"
        assert caps["max_duration"] == 15
        assert caps["default_duration"] == 9
        assert caps["max_reference_images"] == 7

    async def test_max_reference_images_reads_backend_caps_for_ark_seedance(self, db_factory):
        """Ark Seedance max_reference_images comes from backend declarations (=9)."""
        resolver = ConfigResolver(db_factory)
        with patch("lib.config.resolver.get_project_manager"):
            caps = await resolver.video_capabilities_for_project({"video_backend": "ark/doubao-seedance-2-0-260128"})
        assert caps["max_reference_images"] == 9

    async def test_max_reference_images_reads_backend_caps_for_kling_v3_omni(self, db_factory):
        """kling-v3-omni multi-image subject R2V max_reference_images comes from backend declarations (=4, conservative).

        Orchestration trims references using the same number as runtime gate_video_request.
        """
        resolver = ConfigResolver(db_factory)
        with patch("lib.config.resolver.get_project_manager"):
            caps = await resolver.video_capabilities_for_project({"video_backend": "kling/kling-v3-omni"})
        assert caps["max_reference_images"] == 4

    async def test_max_reference_images_reads_backend_caps_for_kling_video_o1(self, db_factory):
        """kling-video-o1 multi-image subject R2V max_reference_images comes from backend declarations (=4, conservative)."""
        resolver = ConfigResolver(db_factory)
        with patch("lib.config.resolver.get_project_manager"):
            caps = await resolver.video_capabilities_for_project({"video_backend": "kling/kling-video-o1"})
        assert caps["max_reference_images"] == 4

    async def test_kling_v3_non_reference_model_has_zero_max_refs(self, db_factory):
        """kling-v3 declares 4K + first/last frames but not multi-image subjects; max_reference_images=0 avoids false reference support."""
        resolver = ConfigResolver(db_factory)
        with patch("lib.config.resolver.get_project_manager"):
            caps = await resolver.video_capabilities_for_project({"video_backend": "kling/kling-v3"})
        assert caps["max_reference_images"] == 0

    @staticmethod
    async def _seed_comfyui_video_model(db_factory, definition: dict, durations: list[int] | None = None) -> str:
        """Persist a ComfyUI video-model row and return ``provider/model``."""
        from lib.db.models.custom_endpoint import CustomEndpoint
        from lib.db.models.custom_provider import CustomProvider, CustomProviderModel

        async with db_factory() as session:
            endpoint = CustomEndpoint(
                definition=definition,
                kind="comfyui",
                schema_version="1.0.0",
                media_type="video",
                display_name="示例 ComfyUI 端点",
            )
            provider = CustomProvider(
                display_name="Comfy", discovery_format="comfyui", base_url="http://comfy.test:8188", api_key=""
            )
            session.add_all([endpoint, provider])
            await session.flush()
            session.add(
                CustomProviderModel(
                    provider_id=provider.id,
                    model_id="my-wan-workflow",
                    display_name="My Workflow",
                    endpoint=f"ce-{endpoint.id}",
                    capability_overrides=None if durations is None else {"durations": durations},
                )
            )
            await session.commit()
            return f"custom-{provider.id}/my-wan-workflow"

    async def test_a_stale_override_is_dropped_when_the_endpoint_lost_its_frames_binding(self, db_factory):
        """When endpoints yield no duration tiers, ignore every row duration override; endpoint declarations determine empty-set exemptions.

        Retaining a row's ``[5]`` would make capability APIs and planning claim 5 seconds while the endpoint catalog
        disables duration controls, giving two answers to one question.
        """
        from tests.factories import comfyui_endpoint_definition

        definition = comfyui_endpoint_definition()
        definition["bindings"]["start_image"] = [{"node": "11", "input": "image", "class_type": "LoadImage"}]
        assert "frames" not in definition["bindings"]
        # The stored row override diverges from the endpoint's current answer.
        backend = await self._seed_comfyui_video_model(db_factory, definition, [5])

        caps = await _video_caps(db_factory, {"video_backend": backend})

        assert caps["supported_durations"] == []
        assert caps["max_duration"] == 0
        assert caps["duration_endpoint_fixed"] is True

    @staticmethod
    def _drivable_definition() -> dict:
        from tests.factories import comfyui_endpoint_definition

        definition = comfyui_endpoint_definition()
        definition["workflow"]["5"]["inputs"]["length"] = 81
        definition["bindings"]["frames"] = [{"node": "5", "input": "length", "class_type": "EmptyLatentImage"}]
        definition["bindings"]["start_image"] = [{"node": "11", "input": "image", "class_type": "LoadImage"}]
        return definition

    async def test_without_an_override_the_tier_is_the_endpoint_native_duration(self, db_factory):
        """Without row overrides, use the endpoint's inferable native duration tier."""
        backend = await self._seed_comfyui_video_model(db_factory, self._drivable_definition())

        caps = await _video_caps(db_factory, {"video_backend": backend})

        assert caps["supported_durations"] == [5]
        assert caps["max_duration"] == 5
        assert caps["duration_endpoint_fixed"] is False

    async def test_a_durations_override_wins_once_the_endpoint_can_drive_duration(self, db_factory):
        """When endpoints can drive duration, user-added/removed row duration tiers take priority (docs/adr/0082, 0104 exception)."""
        backend = await self._seed_comfyui_video_model(db_factory, self._drivable_definition(), [5, 10])

        caps = await _video_caps(db_factory, {"video_backend": backend})

        assert caps["supported_durations"] == [5, 10]
        assert caps["max_duration"] == 10
        assert caps["duration_endpoint_fixed"] is False


@pytest.fixture
def active_aistudio_veo(monkeypatch):
    """AI Studio Veo pricing/audio semantics still come from gemini-video; it is delisted from the factory catalog, so enable it in a catalog copy here."""
    install_catalog(
        monkeypatch,
        with_entry_changes(
            factory_snapshot(), "gemini-aistudio", "veo-3.1-generate-preview", discontinued=False, shutdown_date=None
        ),
    )


class TestVoiceConsistency:
    """Three-dimensional voice_consistency derivation: model capability × generation_mode × character voice binding. All cases use
    `db_factory` real in-memory DBs, classified as integration under docs/standards/testing.md.
    """

    async def test_seedance_2_reference_video_is_native(self, db_factory):
        """reference_audio_mode=direct + generation_mode=reference_video + project reference-audio binding → native."""
        caps = await _video_caps(
            db_factory,
            {
                "video_backend": "ark/doubao-seedance-2-0-260128",
                "generation_mode": "reference_video",
                "character_voice_binding": "reference_audio",
            },
        )
        assert caps["voice_consistency"] == "native"

    async def test_prompt_binding_downgrades_native_to_soft(self, db_factory):
        """Project soft-prompt binding downgrades even native-audio-reference models to soft; only voice_style constrains voices."""
        caps = await _video_caps(
            db_factory,
            {
                "video_backend": "ark/doubao-seedance-2-0-260128",
                "generation_mode": "reference_video",
                "character_voice_binding": "prompt",
            },
        )
        assert caps["voice_consistency"] == "soft"

    async def test_missing_binding_defaults_to_prompt_and_downgrades(self, db_factory):
        """Absent fields mean default soft-prompt binding; new projects lacking them must not resolve native."""
        caps = await _video_caps(
            db_factory,
            {
                "video_backend": "ark/doubao-seedance-2-0-260128",
                "generation_mode": "reference_video",
            },
        )
        assert caps["voice_consistency"] == "soft"

    async def test_unreadable_binding_falls_back_to_prompt(self, db_factory):
        """Invalid hand-edited project.json values mean the default tier, never an upgrade to direct reference audio."""
        caps = await _video_caps(
            db_factory,
            {
                "video_backend": "ark/doubao-seedance-2-0-260128",
                "generation_mode": "reference_video",
                "character_voice_binding": ["reference_audio"],
            },
        )
        assert caps["voice_consistency"] == "soft"

    async def test_requested_generate_audio_is_exposed_separately_from_pricing_value(
        self, db_factory, active_aistudio_veo
    ):
        """caps exposes silence intent alongside pricing semantics: AI Studio Veo always bills with audio, but disabling project audio
        must give orchestration False, or silent video would still upload reference audio."""
        caps = await _video_caps(
            db_factory,
            {
                "video_backend": "gemini-aistudio/veo-3.1-generate-preview",
                "generation_mode": "reference_video",
                "video_generate_audio": False,
            },
        )
        assert caps["requested_generate_audio"] is False
        assert caps["generate_audio"] is True

    async def test_requested_generate_audio_defaults_to_true(self, db_factory):
        caps = await _video_caps(db_factory, {"video_backend": "ark/doubao-seedance-2-0-260128"})
        assert caps["requested_generate_audio"] is True

    async def test_seedance_2_non_reference_mode_downgrades_to_soft(self, db_factory):
        """Outside reference-to-video, the same model downgrades to soft, not none, because native implies an audio track."""
        caps = await _video_caps(
            db_factory,
            {
                "video_backend": "ark/doubao-seedance-2-0-260128",
                "generation_mode": "storyboard",
            },
        )
        assert caps["voice_consistency"] == "soft"

    async def test_seedance_2_missing_generation_mode_downgrades_to_soft(self, db_factory):
        """Absent generation_mode, outside reference_video, also downgrades to soft."""
        caps = await _video_caps(db_factory, {"video_backend": "ark/doubao-seedance-2-0-260128"})
        assert caps["voice_consistency"] == "soft"

    async def test_aistudio_veo_always_soft_regardless_of_generation_mode(self, db_factory, active_aistudio_veo):
        """AI Studio Veo lacks reference audio, so even reference-to-video only supports soft; always-audible output does not imply none."""
        caps = await _video_caps(
            db_factory,
            {
                "video_backend": "gemini-aistudio/veo-3.1-generate-preview",
                "generation_mode": "reference_video",
            },
        )
        assert caps["voice_consistency"] == "soft"

    async def test_grok_imagine_soft(self, db_factory):
        """Grok Imagine has controllable audio without reference audio → soft."""
        caps = await _video_caps(db_factory, {"video_backend": "grok/grok-imagine-video"})
        assert caps["voice_consistency"] == "soft"

    async def test_kling_v3_audio_models_are_soft(self, db_factory):
        """Kling v3 declares audio capability → soft, injecting Voice_Profiles."""
        for model_id in ("kling-v3", "kling-v3-omni"):
            caps = await _video_caps(db_factory, {"video_backend": f"kling/{model_id}"})
            assert caps["voice_consistency"] == "soft"

    async def test_kling_turbo_true_silent_is_none(self, db_factory):
        """Kling v2-5-turbo has no audio switch → none."""
        caps = await _video_caps(db_factory, {"video_backend": "kling/kling-v2-5-turbo"})
        assert caps["voice_consistency"] == "none"

    async def test_minimax_true_silent_is_none(self, db_factory):
        """Truly silent MiniMax models → none."""
        caps = await _video_caps(db_factory, {"video_backend": "minimax/MiniMax-Hailuo-2.3"})
        assert caps["voice_consistency"] == "none"

    async def test_discontinued_model_is_rejected_instead_of_read(self, db_factory):
        """A project selecting delisted Agnes v2.0 gets the same delisting error from capability reads as calls, without returning capabilities."""
        with pytest.raises(ModelNotInCatalogError) as excinfo:
            await _video_caps(db_factory, {"video_backend": "agnes/agnes-video-v2.0"})
        assert excinfo.value.code == "model_discontinued"

    async def test_custom_provider_without_overrides_defaults_to_soft(self, db_factory):
        """Custom providers without generate_audio catalog declarations follow default_tier_generates_audio:
        no signal assumes audio, without inventing a truly silent model."""
        from lib.db.models.custom_provider import CustomProvider, CustomProviderModel

        async with db_factory() as session:
            provider = CustomProvider(
                display_name="Custom Voice",
                discovery_format="openai",
                base_url="https://example.com",
                api_key="xxx",
            )
            session.add(provider)
            await session.flush()
            model = CustomProviderModel(
                provider_id=provider.id,
                model_id="sora-like",
                display_name="Sora-like",
                endpoint="openai-video",
            )
            session.add(model)
            await session.commit()
            project_backend = f"custom-{provider.id}/sora-like"

        caps = await _video_caps(db_factory, {"video_backend": project_backend, "generation_mode": "reference_video"})
        # openai-video lacks reference_audio_capable; overrides cannot claim direct, so it is not native;
        # without catalog audio declarations, assume audio → soft, not none.
        assert caps["voice_consistency"] == "soft"

    async def test_custom_provider_with_direct_override_and_reference_video_is_native(self, db_factory):
        """Custom reference_audio_mode=direct override + limit > 0 + reference-to-video path + reference-audio binding → native."""
        from lib.db.models.custom_provider import CustomProvider, CustomProviderModel

        async with db_factory() as session:
            provider = CustomProvider(
                display_name="Custom Seedance",
                discovery_format="openai",
                base_url="https://example.com",
                api_key="xxx",
            )
            session.add(provider)
            await session.flush()
            model = CustomProviderModel(
                provider_id=provider.id,
                model_id="seedance-like",
                display_name="Seedance-like",
                endpoint="ark-seedance-2",
                # max_reference_images override is required for this model to enter r2v; reference-to-video projects resolve
                # that bucket, while ark-seedance-2 conservatively gives unlisted models 0, blocking resolution before capability queries without an override.
                capability_overrides={
                    "reference_audio_mode": "direct",
                    "max_reference_audio_count": 2,
                    "max_reference_images": 4,
                },
            )
            session.add(model)
            await session.commit()
            project_backend = f"custom-{provider.id}/seedance-like"

        caps = await _video_caps(
            db_factory,
            {
                "video_backend": project_backend,
                "generation_mode": "reference_video",
                "character_voice_binding": "reference_audio",
            },
        )
        assert caps["voice_consistency"] == "native"


class TestUnmatchedCustomVideoModelOverrides:
    """An unmatched provider video model with one overridden dimension replaces that whole dimension; untouched dimensions use fallbacks (docs/adr/0104, 0105).

    Without entries, inputs follow only ark-seedance-2 declarations: text-to-video + first frame, no last frame, and unlimited Ark reference images,
    recorded as unlisted. Durations use name-inferred presets, falling back to [4, 8] without any family match. Never persist fallback results to model rows.
    """

    @staticmethod
    async def _seed(db_factory, model_id: str, overrides: dict) -> str:
        from lib.db.models.custom_provider import CustomProvider, CustomProviderModel

        async with db_factory() as session:
            provider = CustomProvider(
                display_name="Relay", discovery_format="openai", base_url="https://relay.test", api_key="k"
            )
            session.add(provider)
            await session.flush()
            session.add(
                CustomProviderModel(
                    provider_id=provider.id,
                    model_id=model_id,
                    display_name=model_id,
                    endpoint="ark-seedance-2",
                    capability_overrides=overrides,
                )
            )
            await session.commit()
            return f"custom-{provider.id}/{model_id}"

    async def test_overridden_durations_replace_the_fallback_and_channel_facts_fall_back(self, db_factory):
        backend = await self._seed(db_factory, "relay-video-x1", {"durations": [5, 10]})

        caps = await _video_caps(db_factory, {"video_backend": backend})

        assert caps["supported_durations"] == [5, 10]
        assert (caps["text_to_video"], caps["first_frame"], caps["last_frame"]) == (True, True, False)
        # Unmatched entries follow only channel reference limits; Ark is unlimited, recorded as unlisted.
        assert caps["max_reference_images"] is None

    @pytest.mark.parametrize(
        ("model_id", "durations"),
        [("relay-video-x1", [4, 8]), ("relay-kling-v2-turbo", [5, 10])],
        ids=["no-family", "name-preset"],
    )
    async def test_overridden_channel_fact_replaces_and_durations_fall_back(self, db_factory, model_id, durations):
        backend = await self._seed(db_factory, model_id, {"max_reference_images": 3})

        caps = await _video_caps(db_factory, {"video_backend": backend})

        assert caps["max_reference_images"] == 3
        assert (caps["text_to_video"], caps["first_frame"], caps["last_frame"]) == (True, True, False)
        assert caps["supported_durations"] == durations

        from lib.db.models.custom_provider import CustomProviderModel

        async with db_factory() as session:
            row = (await session.execute(select(CustomProviderModel))).scalar_one()
        assert row.capability_overrides == {"max_reference_images": 3}

    async def test_audio_track_override_turns_voice_consistency_off(self, db_factory):
        backend = await self._seed(db_factory, "relay-video-x1", {"audio_track": "always_off"})

        caps = await _video_caps(db_factory, {"video_backend": backend})

        assert caps["voice_consistency"] == "none"


class TestVideoPricingGenerateAudio:
    """video_pricing_generate_audio fallback pricing semantics when capability APIs cannot resolve."""

    async def test_falls_back_to_provider_rule_for_registry_unknown_model(self, db_factory):
        """Delisted Veo model IDs still quote audio-inclusive tiers; failed capability resolution must not underestimate them as silent."""
        resolver = ConfigResolver(db_factory)
        # veo-3.1-generate-001 remains only in gemini-vertex; capability lookup raises, but pricing
        # still falls back to Gemini-family rates.
        result = await resolver.video_pricing_generate_audio("gemini-aistudio", "veo-3.1-generate-001")
        assert result is True

    async def test_falls_back_to_requested_value_for_unknown_provider(self, db_factory):
        """Providers not always including audio retain requested values on capability failure; pricing still falls back to Gemini-family audio-inclusive rates."""
        resolver = ConfigResolver(db_factory)
        result = await resolver.video_pricing_generate_audio("unknown", "unknown")
        assert result is True

    async def test_keeps_requested_audio_for_vertex_unknown_model(self, db_factory):
        """Unregistered gemini-vertex models send/settle requested values; estimates must not downgrade to silent tiers."""
        resolver = ConfigResolver(db_factory)
        # Lite is registered only in gemini-aistudio; switching to vertex while retaining its model ID makes
        # capabilities raise model not found, but pricing still quotes Gemini-family audio-inclusive rates.
        result = await resolver.video_pricing_generate_audio("gemini-vertex", "veo-3.1-lite-generate-preview")
        assert result is True

    async def test_project_audio_off_survives_capability_failure(self, db_factory):
        """With project audio disabled, degraded pricing also quotes silent tiers without inventing audio inclusion."""
        resolver = ConfigResolver(db_factory)
        result = await resolver.video_pricing_generate_audio(
            "gemini-vertex",
            "veo-3.1-lite-generate-preview",
            {"video_generate_audio": False},
        )
        assert result is False


class TestResolveImageBackend:
    """resolve_image_backend: payload > project bucket > project default > global bucket > global default > automatic selection."""

    async def test_payload_capability_slot_is_not_a_payload_layer_key(self):
        """Image tasks do not lock execution identities: ``image_provider_<cap>`` is project-only; matching payload keys do not participate in resolution."""
        resolver = ConfigResolver.__new__(ConfigResolver)
        fake_svc = _FakeConfigService(settings={})
        project = {"image_provider_t2i": "ark/proj-t2i"}
        payload = {"image_provider_t2i": "openai/pay-t2i"}
        resolved = await resolver._resolve_image_provider_model(fake_svc, None, project, payload, "t2i")
        assert (resolved.provider_id, resolved.model_id) == ("ark", "proj-t2i")

    async def test_payload_legacy_fields_for_historical_tasks(self):
        resolver = ConfigResolver.__new__(ConfigResolver)
        fake_svc = _FakeConfigService(settings={})
        payload = {"image_provider": "openai", "image_model": "legacy"}
        resolved = await resolver._resolve_image_provider_model(fake_svc, None, {}, payload, "t2i")
        assert (resolved.provider_id, resolved.model_id) == ("openai", "legacy")

    async def test_project_capability_slot_when_no_payload(self):
        resolver = ConfigResolver.__new__(ConfigResolver)
        fake_svc = _FakeConfigService(settings={})
        project = {"image_provider_t2i": "ark/proj-t2i", "image_provider_i2i": "ark/proj-i2i"}
        t2i = await resolver._resolve_image_provider_model(fake_svc, None, project, {}, "t2i")
        i2i = await resolver._resolve_image_provider_model(fake_svc, None, project, {}, "i2i")
        assert (t2i.provider_id, t2i.model_id) == ("ark", "proj-t2i")
        assert (i2i.provider_id, i2i.model_id) == ("ark", "proj-i2i")

    async def test_project_bucket_wins_over_project_default(self):
        """Project buckets override project default_image_backend."""
        resolver = ConfigResolver.__new__(ConfigResolver)
        fake_svc = _FakeConfigService(settings={})
        project = {"image_provider_t2i": "ark/proj-t2i", "default_image_backend": "openai/proj-default"}
        resolved = await resolver._resolve_image_provider_model(fake_svc, None, project, {}, "t2i")
        assert (resolved.provider_id, resolved.model_id) == ("ark", "proj-t2i")

    async def test_project_default_wins_over_global_layers(self):
        """Absent project buckets use project defaults, shadowing global buckets/defaults."""
        resolver = ConfigResolver.__new__(ConfigResolver)
        fake_svc = _FakeConfigService(
            settings={
                "default_image_backend_t2i": "grok/global-t2i",
                "default_image_backend": "grok/global-default",
            }
        )
        project = {"default_image_backend": "openai/proj-default"}
        for generation_type in ("t2i", "i2i"):
            resolved = await resolver._resolve_image_provider_model(fake_svc, None, project, {}, generation_type)
            assert (resolved.provider_id, resolved.model_id) == ("openai", "proj-default")

    async def test_empty_project_bucket_falls_through_to_project_default(self):
        """Empty-string project buckets fall back to project defaults without skipping that layer."""
        resolver = ConfigResolver.__new__(ConfigResolver)
        fake_svc = _FakeConfigService(settings={})
        project = {"image_provider_i2i": "", "default_image_backend": "openai/proj-default"}
        resolved = await resolver._resolve_image_provider_model(fake_svc, None, project, {}, "i2i")
        assert (resolved.provider_id, resolved.model_id) == ("openai", "proj-default")

    async def test_only_global_default_covers_both_capabilities(self):
        """With only global defaults and empty buckets, both t2i / i2i resolve to that model."""
        resolver = ConfigResolver.__new__(ConfigResolver)
        fake_svc = _FakeConfigService(settings={"default_image_backend": "grok/grok-2-image"})
        for generation_type in ("t2i", "i2i"):
            resolved = await resolver._resolve_image_provider_model(fake_svc, None, None, None, generation_type)
            assert (resolved.provider_id, resolved.model_id) == ("grok", "grok-2-image")

    async def test_falls_through_to_global_default(self):
        """Absent payload/project configuration reaches the explicit global default_image_backend_t2i bucket."""
        resolver = ConfigResolver.__new__(ConfigResolver)
        fake_svc = _FakeConfigService(settings={"default_image_backend_t2i": "grok/grok-2-image"})
        resolved = await resolver._resolve_image_provider_model(fake_svc, None, None, None, "t2i")
        assert (resolved.provider_id, resolved.model_id) == ("grok", "grok-2-image")

    async def test_no_legacy_image_backend_fallback(self):
        """Resolution no longer recognizes legacy image_backend (migrated to canonical fields); use global defaults directly."""
        resolver = ConfigResolver.__new__(ConfigResolver)
        fake_svc = _FakeConfigService(settings={"default_image_backend_t2i": "grok/grok-2-image"})
        project = {"image_backend": "openai/legacy"}
        resolved = await resolver._resolve_image_provider_model(fake_svc, None, project, {}, "t2i")
        assert resolved.provider_id == "grok"

    async def test_project_bare_provider_pins_provider_with_default_model(self, db_session):
        """Bare project provider overrides, allowed at writes, pin that provider and complete its default model without silent global fallback."""
        resolver = ConfigResolver.__new__(ConfigResolver)
        fake_svc = _FakeConfigService(settings={"default_image_backend_t2i": "grok/grok-2-image"})
        project = {"image_provider_t2i": "openai"}  # Bare provider without model.
        resolved = await resolver._resolve_image_provider_model(fake_svc, db_session, project, {}, "t2i")
        assert resolved.provider_id == "openai"
        assert resolved.model_id == "gpt-image-2"  # OpenAI default image model from registry.

    async def test_project_unknown_bare_provider_falls_through(self):
        """Bare providers absent from registries have no default model to complete; fall back to global defaults."""
        resolver = ConfigResolver.__new__(ConfigResolver)
        fake_svc = _FakeConfigService(settings={"default_image_backend_t2i": "grok/grok-2-image"})
        project = {"image_provider_t2i": "does-not-exist"}
        resolved = await resolver._resolve_image_provider_model(fake_svc, None, project, {}, "t2i")
        assert resolved.provider_id == "grok"

    async def test_project_provider_with_trailing_slash_uses_provider_default(self, db_session):
        """Malformed "openai/" without model, accepted by write validation, uses OpenAI defaults instead of sending an empty model downstream."""
        resolver = ConfigResolver.__new__(ConfigResolver)
        fake_svc = _FakeConfigService(settings={"default_image_backend_t2i": "grok/grok-2-image"})
        project = {"image_provider_t2i": "openai/"}
        resolved = await resolver._resolve_image_provider_model(fake_svc, db_session, project, {}, "t2i")
        assert (resolved.provider_id, resolved.model_id) == ("openai", "gpt-image-2")

    async def test_payload_legacy_provider_not_trusted_falls_through_to_project(self):
        """Legacy names in in-flight historical payloads bypass write validation; do not trust them, falling back to migrated projects."""
        resolver = ConfigResolver.__new__(ConfigResolver)
        fake_svc = _FakeConfigService(settings={})
        project = {"image_provider_t2i": "openai/gpt-image-2"}  # Already migrated to canonical names at startup.
        payload = {"image_provider": "vertex", "image_model": "legacy"}  # Unrecognized legacy value.
        resolved = await resolver._resolve_image_provider_model(fake_svc, None, project, payload, "t2i")
        assert (resolved.provider_id, resolved.model_id) == ("openai", "gpt-image-2")

    async def test_payload_known_provider_missing_model_uses_provider_default(self, db_session):
        """Partial payloads with known providers but no model complete that provider's default instead of sending empty models to execution."""
        resolver = ConfigResolver.__new__(ConfigResolver)
        fake_svc = _FakeConfigService(settings={"default_image_backend_t2i": "grok/grok-2-image"})
        payload = {"image_provider": "openai"}  # Provider only, without image_model.
        resolved = await resolver._resolve_image_provider_model(fake_svc, db_session, {}, payload, "t2i")
        assert (resolved.provider_id, resolved.model_id) == ("openai", "gpt-image-2")

    async def test_disabled_default_model_is_not_filled_in(self, db_session):
        """Disabled provider defaults must not complete bare project providers or modelless payloads; treat them as having no default model."""
        from lib.catalog.errors import ModelNotInCatalogError
        from lib.db.models.builtin_provider_model import BuiltinProviderModelState

        db_session.add(BuiltinProviderModelState(provider_id="openai", model_id="gpt-image-2", enabled=False))
        await db_session.flush()
        resolver = ConfigResolver.__new__(ConfigResolver)
        fake_svc = _FakeConfigService(settings={"default_image_backend_t2i": "grok/grok-2-image"})

        # Payload cannot complete a default model; fall back to configuration.
        resolved = await resolver._resolve_image_provider_model(
            fake_svc, db_session, {}, {"image_provider": "openai"}, "t2i"
        )
        assert (resolved.provider_id, resolved.model_id) == ("grok", "grok-2-image")
        # Bare built-in project provider lacks a default: ask users to reselect instead of switching providers.
        with pytest.raises(ModelNotInCatalogError) as exc_info:
            await resolver._resolve_image_provider_model(
                fake_svc, db_session, {"image_provider_t2i": "openai"}, {}, "t2i"
            )
        assert exc_info.value.code == "provider_unsupported_media"


class TestLayeredBackendSkeleton:
    """Default-plus-task-bucket four-layer resolution: project bucket > project default > global bucket > global default > automatic selection.

    Test skeleton contracts directly with synthetic declarations containing all four layers, independent of media-specific keys;
    media buckets add key declarations without changing this skeleton.
    """

    @staticmethod
    def _keys(**overrides):
        from lib.config.resolver import _LayeredBackendKeys

        params = {
            "media_type": "image",
            "project_bucket_key": "img_bucket",
            "project_default_key": "img_default",
            "global_bucket_key": "global_bucket",
            "global_default_key": "global_default",
        }
        params.update(overrides)
        return _LayeredBackendKeys(**params)

    @pytest.mark.parametrize("p_bucket", [False, True])
    @pytest.mark.parametrize("p_def", [False, True])
    @pytest.mark.parametrize("g_bucket", [False, True])
    @pytest.mark.parametrize("g_def", [False, True])
    async def test_four_level_priority_all_combinations(self, p_bucket, p_def, g_bucket, g_def):
        settings = {}
        if g_bucket:
            settings["global_bucket"] = "g-bucket/m"
        if g_def:
            settings["global_default"] = "g-def/m"
        project = {}
        if p_bucket:
            project["img_bucket"] = "p-bucket/m"
        if p_def:
            project["img_default"] = "p-def/m"

        resolver = ConfigResolver.__new__(ConfigResolver)
        fake_svc = _FakeConfigService(settings=settings)
        result = await resolver._resolve_layered_backend(fake_svc, None, project, self._keys())

        if p_bucket:
            assert result == ("p-bucket", "m")
        elif p_def:
            assert result == ("p-def", "m")
        elif g_bucket:
            assert result == ("g-bucket", "m")
        elif g_def:
            assert result == ("g-def", "m")
        else:
            assert result[0] == "gemini-aistudio"  # Every layer absent → automatically choose a ready provider.

    async def test_none_keys_skip_that_level(self):
        """Skip layers whose key is None; undeclared project defaults cannot activate same-named project fields."""
        resolver = ConfigResolver.__new__(ConfigResolver)
        fake_svc = _FakeConfigService(settings={"global_default": "g-def/m"})
        project = {"img_default": "p-def/m"}
        keys = self._keys(project_bucket_key=None, project_default_key=None)
        result = await resolver._resolve_layered_backend(fake_svc, None, project, keys)
        assert result == ("g-def", "m")

    async def test_empty_global_bucket_follows_default(self):
        """Existing global bucket keys without valid values fall back to global defaults (docs/adr/0054)."""
        resolver = ConfigResolver.__new__(ConfigResolver)
        fake_svc = _FakeConfigService(settings={"global_bucket": "", "global_default": "g-def/m"})
        result = await resolver._resolve_layered_backend(fake_svc, None, None, self._keys())
        assert result == ("g-def", "m")

    async def test_project_bare_provider_supported(self, db_session):
        """Project layers support bare-provider overrides with default-model completion, matching existing image/video project fields."""
        resolver = ConfigResolver.__new__(ConfigResolver)
        fake_svc = _FakeConfigService(settings={"global_default": "g-def/m"})
        result = await resolver._resolve_layered_backend(fake_svc, db_session, {"img_bucket": "openai"}, self._keys())
        assert result == ("openai", "gpt-image-2")


class TestResolveVideoBackend:
    """resolve_video_backend: locked payload keys > project > global defaults."""

    async def test_project_video_backend_when_no_payload(self):
        resolver = ConfigResolver.__new__(ConfigResolver)
        fake_svc = _FakeConfigService(settings={})
        project = {"video_backend": "ark/doubao-seedance-2-0-260128"}
        resolved = await resolver._resolve_video_provider_model(fake_svc, None, project, {})
        assert (resolved.provider_id, resolved.model_id) == ("ark", "doubao-seedance-2-0-260128")

    async def test_disabled_custom_model_resolves_to_runtime_default(self, db_factory):
        """Identity resolution returns the actual execution model, without leaving disabled identities for construction to repair."""
        from lib.db.models.custom_provider import CustomProvider, CustomProviderModel

        async with db_factory() as session:
            provider = CustomProvider(
                display_name="Custom Fallback",
                discovery_format="openai",
                base_url="https://example.com",
                api_key="xxx",
            )
            session.add(provider)
            await session.flush()
            session.add_all(
                [
                    CustomProviderModel(
                        provider_id=provider.id,
                        model_id="disabled-model",
                        display_name="Disabled",
                        endpoint="openai-video",
                        is_enabled=False,
                    ),
                    CustomProviderModel(
                        provider_id=provider.id,
                        model_id="runtime-model",
                        display_name="Runtime",
                        endpoint="openai-video",
                        is_enabled=True,
                        is_default=True,
                    ),
                ]
            )
            await session.commit()

            resolver = ConfigResolver(db_factory)
            resolved = await resolver.resolve_video_backend(
                {"video_backend": f"custom-{provider.id}/disabled-model"}, None
            )

        assert (resolved.provider_id, resolved.model_id) == (f"custom-{provider.id}", "runtime-model")

    @pytest.mark.parametrize(
        ("project_key", "generation_type"), [("video_backend", None), ("video_provider_r2v", "r2v")]
    )
    async def test_discontinued_custom_model_is_rejected_instead_of_converging(
        self, db_factory, project_key, generation_type
    ):
        """Custom video models name-matching delisted sora-2 raise delisting errors instead of converging to defaults; disabled models do likewise."""
        from lib.catalog.errors import ModelDiscontinuedError
        from lib.db.models.custom_provider import CustomProvider, CustomProviderModel

        async with db_factory() as session:
            provider = CustomProvider(
                display_name="Relay", discovery_format="openai", base_url="https://example.com", api_key="xxx"
            )
            session.add(provider)
            await session.flush()
            session.add_all(
                [
                    CustomProviderModel(
                        provider_id=provider.id,
                        model_id="sora-2",
                        display_name="Sora 2",
                        endpoint="openai-video",
                        is_enabled=False,
                    ),
                    CustomProviderModel(
                        provider_id=provider.id,
                        model_id="runtime-model",
                        display_name="Runtime",
                        endpoint="openai-video",
                        is_enabled=True,
                        is_default=True,
                    ),
                ]
            )
            await session.commit()

        resolver = ConfigResolver(db_factory)
        with pytest.raises(ModelDiscontinuedError) as excinfo:
            await resolver.resolve_video_backend(
                {project_key: f"custom-{provider.id}/sora-2"}, None, generation_type=generation_type
            )
        # The factory catalog records Sora 2 shutdown on 2026-09-24; delisting errors include the date.
        assert excinfo.value.params == {
            "provider": f"custom-{provider.id}",
            "model": "sora-2",
            "shutdown_date": "2026-09-24",
        }

    async def test_auto_resolve_skips_a_discontinued_custom_default(self, db_factory):
        """Without any default settings or ready built-ins, never automatically select delisted custom-provider defaults."""
        from lib.db.models.custom_provider import CustomProvider, CustomProviderModel

        resolver = ConfigResolver.__new__(ConfigResolver)
        fake_svc = _FakeConfigService(settings={}, ready_providers=[])
        async with db_factory() as session:
            provider = CustomProvider(
                display_name="Relay", discovery_format="openai", base_url="https://example.com", api_key="xxx"
            )
            session.add(provider)
            await session.flush()
            session.add(
                CustomProviderModel(
                    provider_id=provider.id,
                    model_id="sora-2",
                    display_name="Sora 2",
                    endpoint="openai-video",
                    is_enabled=True,
                    is_default=True,
                )
            )
            await session.flush()
            with pytest.raises(ValueError, match="未找到可用的 video 供应商"):
                await resolver._auto_resolve_backend(fake_svc, session, "video")

    async def test_falls_through_to_global_default(self):
        resolver = ConfigResolver.__new__(ConfigResolver)
        fake_svc = _FakeConfigService(settings={"default_video_backend": "ark/doubao-seedance-1-5-pro"})
        resolved = await resolver._resolve_video_provider_model(fake_svc, None, None, None)
        assert (resolved.provider_id, resolved.model_id) == ("ark", "doubao-seedance-1-5-pro")

    async def test_project_bare_provider_pins_provider_with_default_model(self, db_session):
        """Bare video_backend such as "ark" pins Ark and completes its default video model, without selecting another global-default provider."""
        resolver = ConfigResolver.__new__(ConfigResolver)
        fake_svc = _FakeConfigService(settings={"default_video_backend": "grok/grok-imagine-video"})
        project = {"video_backend": "ark"}  # Bare provider.
        resolved = await resolver._resolve_video_provider_model(fake_svc, db_session, project, {})
        assert resolved.provider_id == "ark"
        assert resolved.model_id == "doubao-seedance-2-0-mini-260615"  # Ark default video model from registry.

    async def test_project_bare_provider_without_video_models_is_rejected(self):
        """Bare providers without catalog video models (OpenAI removed Sora) report not in model list, without switching to another global-default provider."""
        from lib.catalog.errors import ModelNotInCatalogError

        resolver = ConfigResolver.__new__(ConfigResolver)
        fake_svc = _FakeConfigService(settings={"default_video_backend": "grok/grok-imagine-video"})
        project = {"video_backend": "openai"}
        with pytest.raises(ModelNotInCatalogError) as exc_info:
            await resolver._resolve_video_provider_model(fake_svc, None, project, {})
        assert (exc_info.value.provider_id, exc_info.value.media_type) == ("openai", "video")
        # Without a model name, say this provider cannot generate this media; never render incomplete references such as "openai/".
        assert (exc_info.value.code, exc_info.value.params) == (
            "provider_unsupported_media",
            {"provider_id": "openai", "media_type": "video"},
        )

    async def test_payload_without_pinned_bucket_key_falls_through_to_project(self):
        """Payload resolution recognizes only locked task-bucket keys; nonbucket provider fields always fall back to configuration."""
        resolver = ConfigResolver.__new__(ConfigResolver)
        fake_svc = _FakeConfigService(settings={})
        project = {"video_backend": "ark/doubao-seedance-2-0-260128"}
        payload = {"video_provider": "grok", "video_provider_settings": {"model": "grok-imagine-video"}}
        resolved = await resolver._resolve_video_provider_model(fake_svc, None, project, payload)
        assert (resolved.provider_id, resolved.model_id) == ("ark", "doubao-seedance-2-0-260128")


class TestResolveVideoBackendBuckets:
    """Video four-layer resolution with generation_type (project bucket > project default > global bucket > global default > automatic selection), plus capability gates.

    Gate fixtures use real backend capabilities: vidu/viduq3-pro is i2v-only, dashscope/happyhorse-1.0-r2v
    r2v-only, and all Ark models have both buckets (see lib/backends/generation_type_buckets.py).
    """

    async def test_project_bucket_wins_over_project_default(self):
        resolver = ConfigResolver.__new__(ConfigResolver)
        fake_svc = _FakeConfigService(settings={})
        project = {"video_provider_i2v": "vidu/viduq3-pro", "video_backend": "grok/grok-imagine-video"}
        resolved = await resolver._resolve_video_provider_model(fake_svc, None, project, None, "i2v")
        assert (resolved.provider_id, resolved.model_id) == ("vidu", "viduq3-pro")

    async def test_project_default_wins_over_global_bucket(self):
        resolver = ConfigResolver.__new__(ConfigResolver)
        fake_svc = _FakeConfigService(settings={"default_video_backend_i2v": "grok/grok-imagine-video"})
        project = {"video_backend": "ark/doubao-seedance-2-0-mini-260615"}
        resolved = await resolver._resolve_video_provider_model(fake_svc, None, project, None, "i2v")
        assert (resolved.provider_id, resolved.model_id) == ("ark", "doubao-seedance-2-0-mini-260615")

    async def test_global_bucket_wins_over_global_default(self):
        resolver = ConfigResolver.__new__(ConfigResolver)
        fake_svc = _FakeConfigService(
            settings={
                "default_video_backend_r2v": "dashscope/happyhorse-1.1-r2v",
                "default_video_backend": "grok/grok-imagine-video",
            }
        )
        resolved = await resolver._resolve_video_provider_model(fake_svc, None, None, None, "r2v")
        assert (resolved.provider_id, resolved.model_id) == ("dashscope", "happyhorse-1.1-r2v")

    async def test_empty_global_bucket_falls_back_to_global_default(self):
        """Empty video buckets (docs/adr/0054) fall back to global defaults, not directly to automatic selection."""
        resolver = ConfigResolver.__new__(ConfigResolver)
        fake_svc = _FakeConfigService(
            settings={
                "default_video_backend_r2v": "",
                "default_video_backend": "grok/grok-imagine-video",
            }
        )
        resolved = await resolver._resolve_video_provider_model(fake_svc, None, None, None, "r2v")
        assert (resolved.provider_id, resolved.model_id) == ("grok", "grok-imagine-video")

    async def test_auto_resolve_when_nothing_configured(self):
        resolver = ConfigResolver.__new__(ConfigResolver)
        fake_svc = _FakeConfigService(settings={}, ready_providers=_ready_with_vertex_video())
        resolved = await resolver._resolve_video_provider_model(fake_svc, None, None, None, "i2v")
        assert (resolved.provider_id, resolved.model_id) == ("gemini-vertex", "veo-3.1-fast-generate-001")

    async def test_missing_i2v_capability_raises_structured_error(self):
        from lib.config.resolver import VideoBucketCapabilityError

        resolver = ConfigResolver.__new__(ConfigResolver)
        fake_svc = _FakeConfigService(settings={"default_video_backend": "dashscope/happyhorse-1.0-r2v"})
        with pytest.raises(VideoBucketCapabilityError) as exc_info:
            await resolver._resolve_video_provider_model(fake_svc, None, None, None, "i2v")
        exc = exc_info.value
        assert exc.code == "video_capability_missing_i2v"
        assert exc.generation_type == "i2v"
        assert exc.params == {"provider": "dashscope", "model": "happyhorse-1.0-r2v"}

    async def test_missing_r2v_capability_raises_structured_error(self):
        from lib.config.resolver import VideoBucketCapabilityError

        resolver = ConfigResolver.__new__(ConfigResolver)
        fake_svc = _FakeConfigService(settings={"default_video_backend": "minimax/MiniMax-Hailuo-2.3"})
        with pytest.raises(VideoBucketCapabilityError) as exc_info:
            await resolver._resolve_video_provider_model(fake_svc, None, None, None, "r2v")
        assert exc_info.value.code == "video_capability_missing_r2v"

    async def test_bucket_reference_to_unknown_model_asks_to_reselect(self):
        """Dangling model references absent from catalogs use the same resolution gate to request reselection, without silent substitution."""
        resolver = ConfigResolver.__new__(ConfigResolver)
        fake_svc = _FakeConfigService(settings={"default_video_backend_i2v": "ark/removed-model"})
        with pytest.raises(ModelNotInCatalogError) as exc_info:
            await resolver._resolve_video_provider_model(fake_svc, None, None, None, "i2v")
        assert exc_info.value.code == "model_not_in_provider_catalog"
        assert exc_info.value.params == {"provider": "ark", "model": "removed-model"}

    async def test_bucket_reference_to_non_video_model_asks_to_reselect(self):
        """Nonvideo models are also dangling references: backend caps functions return static values even for same-provider images
        (``gemini-aistudio/gemini-3.1-flash-image`` gets first_frame=True); membership checks alone
        would admit them and defer failure to execution."""

        resolver = ConfigResolver.__new__(ConfigResolver)
        fake_svc = _FakeConfigService(settings={"default_video_backend_i2v": "gemini-aistudio/gemini-3.1-flash-image"})
        with pytest.raises(ModelNotInCatalogError) as exc_info:
            await resolver._resolve_video_provider_model(fake_svc, None, None, None, "i2v")
        assert exc_info.value.code == "model_not_in_provider_catalog"

    async def test_capability_none_keeps_legacy_resolution_without_gate(self):
        """Unbucketed calls (cost estimates/rate-limit route fallback) retain three-level resolution, ignoring bucket keys and capability gates."""
        resolver = ConfigResolver.__new__(ConfigResolver)
        fake_svc = _FakeConfigService(
            settings={
                "default_video_backend_i2v": "ark/doubao-seedance-2-0-mini-260615",
                "default_video_backend": "dashscope/happyhorse-1.0-r2v",
            }
        )
        resolved = await resolver._resolve_video_provider_model(fake_svc, None, None, None, None)
        assert (resolved.provider_id, resolved.model_id) == ("dashscope", "happyhorse-1.0-r2v")


class TestVideoBucketCapabilityGateCustomProvider:
    """Custom-provider capability gates error on dangling references instead of converging to defaults, while valid models pass normally."""

    async def _seed_provider(self, factory, *, models):
        from lib.db.models.custom_provider import CustomProvider, CustomProviderModel

        async with factory() as session:
            provider = CustomProvider(
                display_name="Custom Video",
                discovery_format="openai",
                base_url="https://example.com",
                api_key="xxx",
            )
            session.add(provider)
            await session.flush()
            for m in models:
                session.add(CustomProviderModel(provider_id=provider.id, **m))
            await session.commit()
            return provider.id

    async def test_bucket_reference_to_missing_custom_model_raises(self, db_factory):
        from lib.config.resolver import VideoBucketCapabilityError

        provider_id = await self._seed_provider(db_factory, models=[])
        resolver = ConfigResolver(db_factory)
        with pytest.raises(VideoBucketCapabilityError) as exc_info:
            await resolver.resolve_video_backend(
                {"video_provider_r2v": f"custom-{provider_id}/ghost-model"}, None, generation_type="r2v"
            )
        assert exc_info.value.code == "video_capability_reference_unavailable"

    async def test_disabled_custom_model_raises_instead_of_converging(self, db_factory):
        """Bucketed resolution does not converge identities; disabled models become dangling errors instead of silently using provider defaults."""
        from lib.config.resolver import VideoBucketCapabilityError

        provider_id = await self._seed_provider(
            db_factory,
            models=[
                {
                    "model_id": "disabled-model",
                    "display_name": "Disabled",
                    "endpoint": "openai-video",
                    "is_enabled": False,
                },
                {
                    "model_id": "runtime-model",
                    "display_name": "Runtime",
                    "endpoint": "openai-video",
                    "is_enabled": True,
                    "is_default": True,
                },
            ],
        )
        resolver = ConfigResolver(db_factory)
        with pytest.raises(VideoBucketCapabilityError) as exc_info:
            await resolver.resolve_video_backend(
                {"video_backend": f"custom-{provider_id}/disabled-model"}, None, generation_type="i2v"
            )
        assert exc_info.value.code == "video_capability_reference_unavailable"

    async def test_enabled_custom_video_model_passes_gate(self, db_factory):
        provider_id = await self._seed_provider(
            db_factory,
            models=[
                {
                    "model_id": "live-model",
                    "display_name": "Live",
                    "endpoint": "openai-video",
                    "is_enabled": True,
                    "is_default": True,
                }
            ],
        )
        resolver = ConfigResolver(db_factory)
        resolved = await resolver.resolve_video_backend(
            {"video_provider_i2v": f"custom-{provider_id}/live-model"}, None, generation_type="i2v"
        )
        assert (resolved.provider_id, resolved.model_id) == (f"custom-{provider_id}", "live-model")


def test_parse_int_variants():
    from lib.config.resolver import _parse_int

    assert _parse_int("100", 7) == 100
    assert _parse_int("", 7) == 7
    assert _parse_int("abc", 7) == 7
    assert _parse_int("0", 7) == 7  # Nonpositive values use default.
    assert _parse_int("-5", 7) == 7  # "-5".isdigit() == False
    assert _parse_int(None, 7) == 7
    assert _parse_int(50, 7) == 50
    assert _parse_int(True, 7) == 7  # Explicitly exclude bool, preventing True→1.


class TestReferencePayloadLimits:
    """Verify reference_payload_limits defaults, provider overrides, tolerance, and None short-circuiting."""

    async def test_none_provider_returns_default_without_db(self):
        # No DB required: provider_id=None directly returns conservative generic defaults.
        resolver = ConfigResolver.__new__(ConfigResolver)
        total, single = await resolver.reference_payload_limits(None)
        from lib.config.service import (
            DEFAULT_REFERENCE_SINGLE_MAX_BYTES,
            DEFAULT_REFERENCE_TOTAL_MAX_BYTES,
        )

        assert total == DEFAULT_REFERENCE_TOTAL_MAX_BYTES
        assert single == DEFAULT_REFERENCE_SINGLE_MAX_BYTES

    async def test_default_when_unset(self, db_factory):
        from lib.config.service import (
            DEFAULT_REFERENCE_SINGLE_MAX_BYTES,
            DEFAULT_REFERENCE_TOTAL_MAX_BYTES,
        )

        resolver = ConfigResolver(db_factory)
        total, single = await resolver.reference_payload_limits("gemini-aistudio")
        assert total == DEFAULT_REFERENCE_TOTAL_MAX_BYTES
        assert single == DEFAULT_REFERENCE_SINGLE_MAX_BYTES

    async def test_provider_override_applies(self, db_factory):
        from lib.config.service import ConfigService

        async with db_factory() as session:
            svc = ConfigService(session)
            await svc.set_provider_config("gemini-aistudio", "reference_total_max_bytes", "1000000")
            await svc.set_provider_config("gemini-aistudio", "reference_single_max_bytes", "500000")
            await session.commit()
        resolver = ConfigResolver(db_factory)
        total, single = await resolver.reference_payload_limits("gemini-aistudio")
        assert (total, single) == (1000000, 500000)

    async def test_unknown_provider_falls_back_to_default(self, db_factory):
        from lib.config.service import DEFAULT_REFERENCE_TOTAL_MAX_BYTES

        resolver = ConfigResolver(db_factory)
        # Unknown providers cause get_provider_config ValueError; catch and use defaults.
        total, _single = await resolver.reference_payload_limits("totally-unknown-provider")
        assert total == DEFAULT_REFERENCE_TOTAL_MAX_BYTES

    async def test_non_numeric_override_falls_back(self, db_factory):
        from lib.config.service import (
            DEFAULT_REFERENCE_SINGLE_MAX_BYTES,
            DEFAULT_REFERENCE_TOTAL_MAX_BYTES,
            ConfigService,
        )

        async with db_factory() as session:
            svc = ConfigService(session)
            await svc.set_provider_config("gemini-aistudio", "reference_total_max_bytes", "not-a-number")
            await session.commit()
        resolver = ConfigResolver(db_factory)
        total, single = await resolver.reference_payload_limits("gemini-aistudio")
        assert total == DEFAULT_REFERENCE_TOTAL_MAX_BYTES  # Nonnumeric fallback.
        assert single == DEFAULT_REFERENCE_SINGLE_MAX_BYTES


class TestTextBackendTierResolution:
    """Five-stage text resolution: project tier > project default > global tier > global default > automatic selection."""

    _AUTO = ("gemini-aistudio", "gemini-3-flash-preview")  # Ready Gemini registry default text model.

    @pytest.mark.parametrize("p_tier", [False, True])
    @pytest.mark.parametrize("p_def", [False, True])
    @pytest.mark.parametrize("g_tier", [False, True])
    @pytest.mark.parametrize("g_def", [False, True])
    async def test_five_level_priority_all_combinations(self, p_tier, p_def, g_tier, g_def):
        from unittest.mock import MagicMock

        from lib.backends.text_backends.base import TextTaskType

        settings = {}
        if g_tier:
            settings["text_backend_complex"] = "g-tier/m"
        if g_def:
            settings["default_text_backend"] = "g-def/m"
        project = {}
        if p_tier:
            project["text_backend_complex"] = "p-tier/m"
        if p_def:
            project["default_text_backend"] = "p-def/m"

        if p_tier:
            expected = ("p-tier", "m")
        elif p_def:
            expected = ("p-def", "m")
        elif g_tier:
            expected = ("g-tier", "m")
        elif g_def:
            expected = ("g-def", "m")
        else:
            expected = self._AUTO

        resolver = ConfigResolver.__new__(ConfigResolver)
        fake_svc = _FakeConfigService(settings=settings)
        with patch("lib.config.resolver.get_project_manager") as mock_pm:
            mock_pm.return_value.load_project.return_value = project
            result = await resolver._resolve_text_backend(fake_svc, MagicMock(), TextTaskType.SCRIPT, "demo")
        assert result == expected

    async def test_no_project_name_skips_project_levels(self):
        from unittest.mock import MagicMock

        from lib.backends.text_backends.base import TextTaskType

        resolver = ConfigResolver.__new__(ConfigResolver)
        fake_svc = _FakeConfigService(settings={"text_backend_complex": "g-tier/m", "default_text_backend": "g-def/m"})
        result = await resolver._resolve_text_backend(fake_svc, MagicMock(), TextTaskType.SCRIPT, None)
        assert result == ("g-tier", "m")

    async def test_simple_tier_tasks_read_simple_key(self):
        """OVERVIEW / STYLE_ANALYSIS use simple-tier text_backend_simple, not complex-tier keys."""
        from unittest.mock import MagicMock

        from lib.backends.text_backends.base import TextTaskType

        resolver = ConfigResolver.__new__(ConfigResolver)
        fake_svc = _FakeConfigService(settings={"text_backend_simple": "simple/m", "text_backend_complex": "complex/m"})
        for task in (TextTaskType.OVERVIEW, TextTaskType.STYLE_ANALYSIS):
            result = await resolver._resolve_text_backend(fake_svc, MagicMock(), task, None)
            assert result == ("simple", "m")

    async def test_script_task_reads_complex_key(self):
        from unittest.mock import MagicMock

        from lib.backends.text_backends.base import TextTaskType

        resolver = ConfigResolver.__new__(ConfigResolver)
        fake_svc = _FakeConfigService(settings={"text_backend_simple": "simple/m", "text_backend_complex": "complex/m"})
        result = await resolver._resolve_text_backend(fake_svc, MagicMock(), TextTaskType.SCRIPT, None)
        assert result == ("complex", "m")

    async def test_malformed_value_without_slash_falls_through(self):
        """Malformed values without "/" count as unset and proceed to the next level."""
        from unittest.mock import MagicMock

        from lib.backends.text_backends.base import TextTaskType

        resolver = ConfigResolver.__new__(ConfigResolver)
        fake_svc = _FakeConfigService(settings={"text_backend_complex": "no-slash", "default_text_backend": "g-def/m"})
        result = await resolver._resolve_text_backend(fake_svc, MagicMock(), TextTaskType.SCRIPT, None)
        assert result == ("g-def", "m")

    async def test_project_bare_provider_pins_its_default_model(self, db_session):
        """Bare-provider project tiers, valid at write boundaries, pin that provider and complete its default model
        without switching to another global-default provider, matching image/video project layers."""
        from lib.backends.text_backends.base import TextTaskType

        resolver = ConfigResolver.__new__(ConfigResolver)
        fake_svc = _FakeConfigService(settings={"default_text_backend": "g-def/m"})
        with patch("lib.config.resolver.get_project_manager") as mock_pm:
            mock_pm.return_value.load_project.return_value = {"text_backend_complex": "openai"}
            result = await resolver._resolve_text_backend(fake_svc, db_session, TextTaskType.SCRIPT, "demo")
        assert result == ("openai", "gpt-5.4-mini")


class TestStyleAnalysisVisionGuard:
    """If simple-tier models lack image input, style-analysis resolution errors without silent model substitution."""

    async def test_rejects_registry_model_without_vision(self):
        from unittest.mock import MagicMock

        from lib.backends.text_backends.base import TextTaskType

        resolver = ConfigResolver.__new__(ConfigResolver)
        # qwen3-max has no registry vision declaration.
        fake_svc = _FakeConfigService(settings={"text_backend_simple": "dashscope/qwen3-max"})
        with pytest.raises(ValueError, match="vision"):
            await resolver._resolve_text_backend(fake_svc, MagicMock(), TextTaskType.STYLE_ANALYSIS, None)

    @pytest.mark.parametrize("provider_id", ["gemini-aistudio", "gemini-vertex"])
    async def test_accepts_multimodal_lite_model(self, provider_id):
        from unittest.mock import MagicMock

        from lib.backends.text_backends.base import TextTaskType
        from lib.catalog import query as catalog

        assert "gemini-3.1-flash-lite" in catalog.list_models(provider_id)
        resolver = ConfigResolver.__new__(ConfigResolver)
        fake_svc = _FakeConfigService(settings={"text_backend_simple": f"{provider_id}/gemini-3.1-flash-lite"})
        result = await resolver._resolve_text_backend(fake_svc, MagicMock(), TextTaskType.STYLE_ANALYSIS, None)
        assert result == (provider_id, "gemini-3.1-flash-lite")

    async def test_accepts_registry_model_with_vision(self):
        from unittest.mock import MagicMock

        from lib.backends.text_backends.base import TextTaskType

        resolver = ConfigResolver.__new__(ConfigResolver)
        fake_svc = _FakeConfigService(settings={"text_backend_simple": "gemini-aistudio/gemini-3-flash-preview"})
        result = await resolver._resolve_text_backend(fake_svc, MagicMock(), TextTaskType.STYLE_ANALYSIS, None)
        assert result == ("gemini-aistudio", "gemini-3-flash-preview")

    async def test_unknown_model_passes_without_guess(self):
        """Outside registries, such as custom providers, absent per-model facts pass without guessing."""
        from unittest.mock import MagicMock

        from lib.backends.text_backends.base import TextTaskType

        resolver = ConfigResolver.__new__(ConfigResolver)
        fake_svc = _FakeConfigService(settings={"text_backend_simple": "custom-abc/some-model"})
        result = await resolver._resolve_text_backend(fake_svc, MagicMock(), TextTaskType.STYLE_ANALYSIS, None)
        assert result == ("custom-abc", "some-model")

    async def test_custom_model_follows_image_input_override(self, db_factory):
        """Custom text models reject image input disabled by overrides; unmatched entries without overrides pass."""
        from lib.backends.text_backends.base import TextTaskType
        from lib.db.models.custom_provider import CustomProvider, CustomProviderModel

        async with db_factory() as session:
            provider = CustomProvider(display_name="R", discovery_format="openai", base_url="https://r", api_key="k")
            session.add(provider)
            await session.flush()
            session.add_all(
                [
                    CustomProviderModel(
                        provider_id=provider.id,
                        model_id="blind-llm",
                        display_name="blind",
                        endpoint="openai-chat",
                        capability_overrides={"image_input": False},
                    ),
                    CustomProviderModel(
                        provider_id=provider.id, model_id="relay-llm", display_name="relay", endpoint="openai-chat"
                    ),
                ]
            )
            await session.commit()
            pid = f"custom-{provider.id}"

        resolver = ConfigResolver(db_factory)
        async with db_factory() as session:
            with pytest.raises(ValueError, match="vision"):
                await resolver._resolve_text_backend(
                    _FakeConfigService(settings={"text_backend_simple": f"{pid}/blind-llm"}),
                    session,
                    TextTaskType.STYLE_ANALYSIS,
                    None,
                )
            result = await resolver._resolve_text_backend(
                _FakeConfigService(settings={"text_backend_simple": f"{pid}/relay-llm"}),
                session,
                TextTaskType.STYLE_ANALYSIS,
                None,
            )
        assert result == (pid, "relay-llm")

    @pytest.mark.parametrize("provider_id", ["gemini-aistudio", "gemini-vertex"])
    async def test_complex_tier_task_not_vision_checked(self, provider_id):
        """Vision checks apply only to tasks needing image input; SCRIPT is unaffected."""
        from unittest.mock import MagicMock

        from lib.backends.text_backends.base import TextTaskType

        resolver = ConfigResolver.__new__(ConfigResolver)
        fake_svc = _FakeConfigService(settings={"text_backend_complex": f"{provider_id}/gemini-3.1-flash-lite"})
        result = await resolver._resolve_text_backend(fake_svc, MagicMock(), TextTaskType.SCRIPT, None)
        assert result == (provider_id, "gemini-3.1-flash-lite")


class TestProjectGenerationModeCaps:
    """Capabilities use project generation mode as their axis, fixed at creation for the entire project without episode context."""

    def test_caps_generation_mode_reads_project_field(self):
        assert caps_generation_mode({"generation_mode": "reference_video"}) == "reference_video"
        assert caps_generation_mode({"generation_mode": "storyboard"}) == "storyboard"

    def test_caps_generation_mode_none_without_project_context(self):
        """No project context yields None; undeclared differs from explicitly choosing a generation mode."""
        assert caps_generation_mode(None) is None
        assert caps_generation_mode({}) is None
        assert caps_generation_mode({"generation_mode": ""}) is None
        assert caps_generation_mode({"generation_mode": {"nested": "dict"}}) is None

    async def test_bucket_follows_project_route(self, db_factory):
        """Bucketed resolution takes the model at the bucket key corresponding to project generation mode."""
        storyboard_project = {
            "generation_mode": "storyboard",
            "video_provider_i2v": "kling/kling-v3",
            "video_provider_r2v": "dashscope/happyhorse-1.1-r2v",
        }
        reference_project = {**storyboard_project, "generation_mode": "reference_video"}
        assert (await _video_caps(db_factory, storyboard_project))["model"] == "kling-v3"
        assert (await _video_caps(db_factory, reference_project))["model"] == "happyhorse-1.1-r2v"

    async def test_voice_consistency_follows_project_route(self, db_factory):
        """Reference-to-video with project reference-audio binding resolves native; storyboard-image-to-video downgrades to soft."""
        project = {
            "generation_mode": "reference_video",
            "character_voice_binding": "reference_audio",
            "video_provider_r2v": "ark/doubao-seedance-2-0-260128",
            "video_backend": "ark/doubao-seedance-2-0-260128",
        }
        caps = await _video_caps(db_factory, project)
        assert caps["voice_consistency"] == "native"
        assert caps["generation_mode"] == "reference_video"
        assert (await _video_caps(db_factory, {**project, "generation_mode": "storyboard"}))[
            "voice_consistency"
        ] == "soft"

    async def test_generation_mode_follows_project_route(self, db_factory):
        """caps generation_mode and flags follow project routes; reference-to-video reads r2v models."""
        caps = await _video_caps(
            db_factory, {"generation_mode": "reference_video", "video_provider_r2v": "dashscope/happyhorse-1.1-r2v"}
        )
        assert caps["generation_mode"] == "reference_video"
        assert caps["max_reference_images"] == 9


class TestPayloadPinnedVideoModel:
    """Enqueue-locked task-bucket payload identities take highest priority and remain readable to unbucketed callers such as resume."""

    async def test_pinned_bucket_key_wins_over_project(self):
        resolver = ConfigResolver.__new__(ConfigResolver)
        fake_svc = _FakeConfigService(settings={})
        project = {"video_provider_i2v": "vidu/viduq3-pro", "video_backend": "grok/grok-imagine-video"}
        payload = {"video_provider_i2v": "ark/doubao-seedance-2-0-260128"}
        resolved = await resolver._resolve_video_provider_model(fake_svc, None, project, payload, "i2v")
        assert (resolved.provider_id, resolved.model_id) == ("ark", "doubao-seedance-2-0-260128")

    async def test_pinned_bucket_key_skips_capability_gate(self):
        """Locked identities pass availability checks only, not capability gates; already-enqueued tasks execute payloads without retrospective validation.

        Lock i2v-only viduq3-pro while resolving r2v; a capability gate would reject it.
        """
        resolver = ConfigResolver.__new__(ConfigResolver)
        fake_svc = _FakeConfigService(settings={})
        payload = {"video_provider_r2v": "vidu/viduq3-pro"}
        resolved = await resolver._resolve_video_provider_model(fake_svc, None, None, payload, "r2v")
        assert (resolved.provider_id, resolved.model_id) == ("vidu", "viduq3-pro")

    async def test_pinned_bucket_key_hit_without_capability(self):
        """Resume semantics (generation_type=None): enqueue writes one bucket key; fixed bucket order finds the first match."""
        resolver = ConfigResolver.__new__(ConfigResolver)
        fake_svc = _FakeConfigService(settings={})
        project = {"video_backend": "grok/grok-imagine-video"}
        payload = {"video_provider_r2v": "ark/doubao-seedance-2-0-260128"}
        resolved = await resolver._resolve_video_provider_model(fake_svc, None, project, payload)
        assert (resolved.provider_id, resolved.model_id) == ("ark", "doubao-seedance-2-0-260128")

    async def test_pin_of_other_bucket_ignored_when_capability_given(self):
        """Explicit generation_type accepts only that bucket's key; locks in other buckets do not cross over."""
        resolver = ConfigResolver.__new__(ConfigResolver)
        fake_svc = _FakeConfigService(settings={})
        project = {"video_backend": "grok/grok-imagine-video"}
        payload = {"video_provider_r2v": "ark/doubao-seedance-2-0-260128"}
        resolved = await resolver._resolve_video_provider_model(fake_svc, None, project, payload, "i2v")
        assert (resolved.provider_id, resolved.model_id) == ("grok", "grok-imagine-video")

    async def test_pin_of_unavailable_provider_raises_instead_of_falling_back(self):
        """Delisted locked providers error instead of falling back to configuration.

        Fallback would execute another provider; resume would even poll the old provider_job_id with a different backend.
        """
        resolver = ConfigResolver.__new__(ConfigResolver)
        fake_svc = _FakeConfigService(settings={})
        project = {"video_backend": "grok/grok-imagine-video"}
        payload = {"video_provider_i2v": "seedance/legacy"}
        with pytest.raises(ModelNotInCatalogError) as excinfo:
            await resolver._resolve_video_provider_model(fake_svc, None, project, payload, "i2v")
        assert excinfo.value.provider_id == "seedance"

    async def test_pin_of_deleted_builtin_model_raises(self):
        """Locked built-in models removed by registry upgrades error instead of continuing with dangling identities."""
        resolver = ConfigResolver.__new__(ConfigResolver)
        fake_svc = _FakeConfigService(settings={})
        project = {"video_backend": "grok/grok-imagine-video"}
        payload = {"video_provider_i2v": "ark/retired-model"}
        with pytest.raises(ModelNotInCatalogError) as excinfo:
            await resolver._resolve_video_provider_model(fake_svc, None, project, payload, "i2v")
        assert excinfo.value.model_id == "retired-model"

    async def test_pin_of_non_video_builtin_model_raises(self):
        """Locked built-in nonvideo models also error, without silently using configured video models."""
        resolver = ConfigResolver.__new__(ConfigResolver)
        fake_svc = _FakeConfigService(settings={})
        project = {"video_backend": "grok/grok-imagine-video"}
        payload = {"video_provider_i2v": "ark/doubao-seedream-4-0-250828"}
        with pytest.raises(ModelNotInCatalogError):
            await resolver._resolve_video_provider_model(fake_svc, None, project, payload, "i2v")

    async def test_malformed_pin_falls_through_to_config(self):
        """Noncomposite bucket keys missing models count as unlocked and fall back to configuration."""
        resolver = ConfigResolver.__new__(ConfigResolver)
        fake_svc = _FakeConfigService(settings={})
        project = {"video_backend": "grok/grok-imagine-video"}
        resolved = await resolver._resolve_video_provider_model(fake_svc, None, project, {"video_provider_i2v": "ark"})
        assert (resolved.provider_id, resolved.model_id) == ("grok", "grok-imagine-video")

    async def test_custom_provider_pin_survives_resume_resolution(self, db_factory):
        """Custom-provider video resume retains checkpoint-replayed models, without project-configuration fallback changing models."""
        from lib.db.models.custom_provider import CustomProvider, CustomProviderModel

        async with db_factory() as session:
            provider = CustomProvider(
                display_name="Custom Pinned",
                discovery_format="openai",
                base_url="https://example.com",
                api_key="xxx",
            )
            session.add(provider)
            await session.flush()
            session.add(
                CustomProviderModel(
                    provider_id=provider.id,
                    model_id="pinned-model",
                    display_name="Pinned",
                    endpoint="openai-video",
                    is_enabled=True,
                    is_default=True,
                )
            )
            await session.commit()
            provider_id = f"custom-{provider.id}"

            resolver = ConfigResolver(db_factory)
            resolved = await resolver.resolve_video_backend(
                {"video_backend": "grok/grok-imagine-video"},
                {"video_provider_i2v": f"{provider_id}/pinned-model"},
            )

        assert (resolved.provider_id, resolved.model_id) == (provider_id, "pinned-model")

    async def test_disabled_pinned_custom_model_raises_instead_of_switching(self, db_factory):
        """Checkpoint-locked custom models disabled before resume error instead of converging to defaults.

        Switching would silently change models; resume would also poll the original model's provider_job_id with a different backend.
        """
        from lib.db.models.custom_provider import CustomProvider, CustomProviderModel

        async with db_factory() as session:
            provider = CustomProvider(
                display_name="Custom Pinned",
                discovery_format="openai",
                base_url="https://example.com",
                api_key="xxx",
            )
            session.add(provider)
            await session.flush()
            session.add_all(
                [
                    CustomProviderModel(
                        provider_id=provider.id,
                        model_id="pinned-model",
                        display_name="Pinned",
                        endpoint="openai-video",
                        is_enabled=False,
                        is_default=False,
                    ),
                    CustomProviderModel(
                        provider_id=provider.id,
                        model_id="other-model",
                        display_name="Other",
                        endpoint="openai-video",
                        is_enabled=True,
                        is_default=True,
                    ),
                ]
            )
            await session.commit()
            provider_id = f"custom-{provider.id}"

            resolver = ConfigResolver(db_factory)
            with pytest.raises(VideoBucketCapabilityError) as excinfo:
                await resolver.resolve_video_backend(
                    {"video_backend": "grok/grok-imagine-video"},
                    {"video_provider_i2v": f"{provider_id}/pinned-model"},
                )

        assert excinfo.value.model_id == "pinned-model"
        assert excinfo.value.generation_type == "i2v"


class TestAutoResolvedBackendsPreview:
    """Read-only previews of automatic selection with every layer empty match execution resolution (``_auto_resolve_backend`` layer)."""

    @staticmethod
    async def _execution_pairs(resolver: ConfigResolver, svc, session) -> dict[str, tuple[str, str] | None]:
        """Execution-resolved models for each purpose with every layer empty; None when none can be chosen."""
        from lib.backends.text_backends.base import TextTaskType

        async def _or_none(coro):
            try:
                return await coro
            except ValueError:
                return None

        return {
            "t2i": await _or_none(resolver._resolve_default_image_backend(svc, session, "t2i")),
            "i2i": await _or_none(resolver._resolve_default_image_backend(svc, session, "i2i")),
            "i2v": await _or_none(resolver._resolve_layered_backend(svc, session, None, _VIDEO_LAYERED_KEYS["i2v"])),
            "r2v": await _or_none(resolver._resolve_layered_backend(svc, session, None, _VIDEO_LAYERED_KEYS["r2v"])),
            "text": await _or_none(resolver._resolve_text_backend(svc, session, TextTaskType.SCRIPT, None)),
        }

    async def test_builtin_provider_preview_matches_execution(self, db_factory):
        resolver = ConfigResolver.__new__(ConfigResolver)
        fake_svc = _FakeConfigService(settings={}, ready_providers=_ready_with_vertex_video())
        async with db_factory() as session:
            preview = await resolver._resolve_auto_backends(fake_svc, session)
            executed = await self._execution_pairs(resolver, fake_svc, session)
        assert preview == executed
        assert {purpose: pair[0] if pair else None for purpose, pair in preview.items()} == {
            "t2i": "gemini-aistudio",
            "i2i": "gemini-aistudio",
            "i2v": "gemini-vertex",
            "r2v": "gemini-vertex",
            "text": "gemini-aistudio",
        }

    async def test_custom_fallback_filters_by_bucket_and_reports_missing_media(self, db_factory):
        """Without ready built-ins, use bucketed custom defaults; no media candidates means None, not error."""
        from lib.custom_provider import make_provider_id
        from lib.db.models.custom_provider import CustomProvider, CustomProviderModel

        resolver = ConfigResolver.__new__(ConfigResolver)
        fake_svc = _FakeConfigService(settings={}, ready_providers=[])
        async with db_factory() as session:
            provider = CustomProvider(
                display_name="Prov", discovery_format="openai", base_url="https://api.example.com", api_key="k"
            )
            session.add(provider)
            await session.flush()
            session.add_all(
                [
                    CustomProviderModel(
                        provider_id=provider.id,
                        model_id="t2i-m",
                        display_name="T2I",
                        endpoint="openai-images-generations",
                        is_default=True,
                        is_enabled=True,
                    ),
                    CustomProviderModel(
                        provider_id=provider.id,
                        model_id="i2i-m",
                        display_name="I2I",
                        endpoint="openai-images-edits",
                        is_default=True,
                        is_enabled=True,
                    ),
                ]
            )
            await session.flush()

            preview = await resolver._resolve_auto_backends(fake_svc, session)
            executed = await self._execution_pairs(resolver, fake_svc, session)
        pid = make_provider_id(provider.id)
        assert preview == {"t2i": (pid, "t2i-m"), "i2i": (pid, "i2i-m"), "i2v": None, "r2v": None, "text": None}
        assert preview == executed
