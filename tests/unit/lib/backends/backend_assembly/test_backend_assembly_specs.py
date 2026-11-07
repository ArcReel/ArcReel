"""Synchronous built-in ProviderSpec/_build_simple construction tests.

Assembly decides media registry/backend name/arguments. Replace four registries with recording
factories and assert complete provider/media construction records: simple-family base_url
precedence, Gemini/Kling branches, and parameter decoupling.
"""

from __future__ import annotations

import dataclasses
from typing import Any

import pytest

from lib.backends.backend_assembly.loaded_config import LoadedConfig
from lib.backends.backend_assembly.specs import (
    PROVIDER_SPEC_REGISTRY,
    ProviderSpec,
    _validate_provider_specs,
    builtin_model_spec,
    get_provider_spec,
)
from lib.backends.provider_assets import InferenceCredentialRef
from lib.config.registry import PROVIDER_REGISTRY
from lib.custom_provider.declarative_backend import DeclarativeVideoBackend
from lib.custom_provider.endpoints import ENDPOINT_REGISTRY
from tests.fakes import captured_backend_construction


def _loaded(*, credentials: dict, provider_id: str) -> LoadedConfig:
    return LoadedConfig(
        credentials=credentials,
        provider_meta=PROVIDER_REGISTRY.get(provider_id),
        rate_limiter=None,
    )


def _built(spec: ProviderSpec, config: LoadedConfig, model: str | None) -> dict[str, Any]:
    """Assemble once and return the sole media/backend/arguments construction record."""
    with captured_backend_construction() as records:
        spec.build_backend(config, model)
    assert len(records) == 1
    return records[0]


@pytest.mark.parametrize(
    ("provider_id", "model", "endpoint"),
    [
        ("minimax", "MiniMax-Hailuo-2.3", "minimax-hailuo-v1"),
        ("minimax", "MiniMax-Hailuo-2.3-Fast", "minimax-hailuo-v1-fast"),
        ("minimax", "S2V-01", "minimax-s2v-01"),
        ("minimax", "proxy/minimax-h3", "minimax-h3"),
    ],
)
def test_migrated_builtin_video_providers_use_shipped_definitions(provider_id: str, model: str, endpoint: str):
    backend = get_provider_spec(provider_id, "video", endpoint).build_backend(
        _loaded(credentials={"api_key": "sk-test"}, provider_id=provider_id), model
    )

    assert isinstance(backend, DeclarativeVideoBackend)
    assert backend.model == model
    assert backend.name == provider_id
    assert backend._definition is ENDPOINT_REGISTRY[endpoint].definition


class TestBuildSimpleBaseUrlPriority:
    """Simple base_url precedence: explicit user > registry default > omission."""

    def test_ark_image_falls_back_to_registry_default(self):
        config = _loaded(credentials={"api_key": "sk-test"}, provider_id="ark")
        assert _built(get_provider_spec("ark", "image"), config, "doubao-seed-2-0-pro-260215") == {
            "media": "image",
            "backend": "ark-seedream-4",
            "kwargs": {
                "api_key": "sk-test",
                "model": "doubao-seed-2-0-pro-260215",
                "base_url": "https://ark.cn-beijing.volces.com/api/v3",
            },
        }

    def test_user_base_url_wins_over_registry_default(self):
        config = _loaded(
            credentials={"api_key": "sk-test", "base_url": "https://custom.example.com/v3"},
            provider_id="ark",
        )
        assert _built(get_provider_spec("ark", "image"), config, "model-x")["kwargs"] == {
            "api_key": "sk-test",
            "model": "model-x",
            "base_url": "https://custom.example.com/v3",
        }

    def test_ark_agent_plan_uses_own_plan_base_url(self):
        # ark-agent-plan media reuses Ark backends, but defaults independently to /api/plan/v3,
        # not Ark /api/v3.
        config = _loaded(credentials={"api_key": "sk-test"}, provider_id="ark-agent-plan")
        built = _built(get_provider_spec("ark-agent-plan", "video"), config, "doubao-seedance-2.0")
        assert built == {
            "media": "video",
            "backend": "ark-seedance-2",
            "kwargs": {
                "api_key": "sk-test",
                "model": "doubao-seedance-2.0",
                "base_url": "https://ark.cn-beijing.volces.com/api/plan/v3",
            },
        }

    @pytest.mark.parametrize("provider_id", ["ark", "ark-agent-plan"])
    def test_ark_video_reports_the_inference_credential_record(self, provider_id: str):
        """Ark video reports actual credential records for library binding checks, omitting nonrecord credentials."""
        meta = PROVIDER_REGISTRY.get(provider_id)
        assert meta is not None
        config = dataclasses.replace(
            _loaded(credentials={"api_key": "TEST-ark-key"}, provider_id=provider_id), credential_id=11
        )
        kwargs = _built(get_provider_spec(provider_id, "video"), config, "doubao-seedance-2-0-260128")["kwargs"]
        assert kwargs["inference_credential"] == InferenceCredentialRef(
            credential_type="builtin",
            credential_id=11,
            api_key="TEST-ark-key",
            base_url=None,
            default_base_url=meta.default_base_url,
        )
        plain = _loaded(credentials={"api_key": "TEST-ark-key"}, provider_id=provider_id)
        assert "inference_credential" not in _built(get_provider_spec(provider_id, "video"), plain, "m")["kwargs"]

    @pytest.mark.parametrize(
        ("provider_id", "model_id", "endpoint"),
        [
            ("ark", "doubao-seedance-2-0-260128", "ark-seedance-2"),
            ("ark", "doubao-seedance-2-0-mini-260615", "ark-seedance-2"),
            ("ark", "doubao-seedance-2-5-260628", "ark-seedance-2-5"),
            ("ark-agent-plan", "doubao-seedance-2.0-fast", "ark-seedance-2"),
        ],
    )
    def test_ark_video_builds_through_the_seedance_endpoint_of_the_model(
        self, provider_id: str, model_id: str, endpoint: str
    ):
        """Official Ark catalog models assemble using data-declared Seedance endpoints."""
        config = _loaded(credentials={"api_key": "TEST-ark-key"}, provider_id=provider_id)
        spec = builtin_model_spec(provider_id, "video", model_id)
        assert spec.endpoint == endpoint
        assert _built(spec, config, model_id)["backend"] == endpoint

    def test_grok_image_no_default_no_user_omits_base_url(self):
        # Grok has no registry default/user URL, so omit unsupported base_url arguments.
        config = _loaded(credentials={"api_key": "sk-test"}, provider_id="grok")
        assert _built(get_provider_spec("grok", "image"), config, "grok-2-image")["kwargs"] == {
            "api_key": "sk-test",
            "model": "grok-2-image",
        }

    def test_missing_api_key_omitted_so_sdk_env_fallback_survives(self):
        # Missing user api_key is omitted, never None, leaving backend environment fallback
        # such as OpenAI SDK OPENAI_API_KEY or fail-loud behavior; explicit None would override fallback.
        config = _loaded(credentials={}, provider_id="openai")
        assert _built(get_provider_spec("openai", "image"), config, "gpt-image-1")["kwargs"] == {"model": "gpt-image-1"}


class TestMediaRegistryRouting:
    """_build_simple’s sole branching chooses registry create_backend by media_type."""

    def test_dashscope_video_uses_video_registry_and_default(self):
        config = _loaded(credentials={"api_key": "sk-test"}, provider_id="dashscope")
        assert _built(get_provider_spec("dashscope", "video"), config, "wan2.7-r2v") == {
            "media": "video",
            "backend": "dashscope-always-on-video",
            "kwargs": {
                "api_key": "sk-test",
                "model": "wan2.7-r2v",
                "base_url": "https://dashscope.aliyuncs.com",
            },
        }

    def test_dashscope_video_passes_wan3_base_url(self):
        # Dedicated wan3 maas host has its own key consumed only by video; omit empty kwargs.
        config = _loaded(
            credentials={"api_key": "sk-test", "wan3_base_url": "https://maas.example.com/ws-1/api/v1"},
            provider_id="dashscope",
        )
        built = _built(get_provider_spec("dashscope", "video"), config, "wan3.0-video")
        assert built["kwargs"]["wan3_base_url"] == "https://maas.example.com/ws-1/api/v1"

    def test_dashscope_video_omits_unset_wan3_base_url(self):
        config = _loaded(credentials={"api_key": "sk-test"}, provider_id="dashscope")
        built = _built(get_provider_spec("dashscope", "video"), config, "wan3.0-video")
        assert "wan3_base_url" not in built["kwargs"]

    def test_dashscope_audio_uses_audio_registry(self):
        config = _loaded(credentials={"api_key": "sk-test"}, provider_id="dashscope")
        # Default endpoint remains multimodal-generation; registry names are endpoint names.
        assert _built(get_provider_spec("dashscope", "audio"), config, "qwen3-tts-flash") == {
            "media": "audio",
            "backend": "dashscope-tts",
            "kwargs": {
                "api_key": "sk-test",
                "model": "qwen3-tts-flash",
                "base_url": "https://dashscope.aliyuncs.com",
            },
        }

    def test_dashscope_speech_synthesizer_endpoint_uses_its_own_registry_name(self):
        config = _loaded(credentials={"api_key": "sk-test"}, provider_id="dashscope")
        spec = get_provider_spec("dashscope", "audio", "dashscope-speech-synthesizer")
        assert _built(spec, config, "qwen-audio-3.1-tts-flash") == {
            "media": "audio",
            "backend": "dashscope-speech-synthesizer",
            "kwargs": {
                "api_key": "sk-test",
                "model": "qwen-audio-3.1-tts-flash",
                "base_url": "https://dashscope.aliyuncs.com",
            },
        }


class TestGeminiSpec:
    """Gemini branches backend_type by provider_id, separate aistudio/vertex rows; image/video both
    forward base_url/shared rate_limiter with image_model/video_model names. Pass api_key/base_url
    unconditionally, including None, for backend resolve_gemini_api_key/normalize_base_url environment/
    omission handling. Vertex structurally ignores URLs through vertexai=True/service accounts, with no injection point."""

    def test_aistudio_image_sets_base_url_and_image_model(self):
        spec = get_provider_spec("gemini-aistudio", "image")
        assert spec.registry_backend == "gemini"
        limiter = object()
        config = LoadedConfig(
            credentials={"api_key": "sk-aistudio", "base_url": "https://custom.example.com"},
            provider_meta=PROVIDER_REGISTRY.get("gemini-aistudio"),
            rate_limiter=limiter,
        )
        assert _built(spec, config, "gemini-3.1-flash-image-preview") == {
            "media": "image",
            "backend": "gemini",
            "kwargs": {
                "backend_type": "aistudio",
                "api_key": "sk-aistudio",
                "base_url": "https://custom.example.com",
                "rate_limiter": limiter,
                "image_model": "gemini-3.1-flash-image-preview",
            },
        }

    def test_vertex_image_backend_type_vertex(self):
        config = LoadedConfig(
            credentials={"api_key": None, "base_url": None, "credentials_path": "/legacy/vertex.json"},
            provider_meta=PROVIDER_REGISTRY.get("gemini-vertex"),
            rate_limiter=None,
        )
        # Vertex still forwards absent api_key/base_url as None, for credential-file fallback/URL omission.
        assert _built(get_provider_spec("gemini-vertex", "image"), config, None)["kwargs"] == {
            "backend_type": "vertex",
            "api_key": None,
            "base_url": None,
            "rate_limiter": None,
            "image_model": None,
            "credentials_path": "/legacy/vertex.json",
        }

    def test_aistudio_video_sets_base_url_uses_video_model(self):
        spec = get_provider_spec("gemini-aistudio", "video")
        assert spec.registry_backend == "gemini"
        limiter = object()
        config = LoadedConfig(
            credentials={"api_key": "sk-aistudio", "base_url": "https://custom.example.com"},
            provider_meta=PROVIDER_REGISTRY.get("gemini-aistudio"),
            rate_limiter=limiter,
        )
        # Video matches image: AI Studio forwards user URLs using video_model, not image_model.
        assert _built(spec, config, "veo-3.1-lite-generate-preview") == {
            "media": "video",
            "backend": "gemini",
            "kwargs": {
                "backend_type": "aistudio",
                "api_key": "sk-aistudio",
                "base_url": "https://custom.example.com",
                "rate_limiter": limiter,
                "video_model": "veo-3.1-lite-generate-preview",
            },
        }

    def test_vertex_video_backend_type_vertex(self):
        config = LoadedConfig(
            credentials={"api_key": None, "base_url": None, "credentials_path": "/legacy/vertex.json"},
            provider_meta=PROVIDER_REGISTRY.get("gemini-vertex"),
            rate_limiter=None,
        )
        # Vertex forwards None symmetrically, structurally ignored inside backends.
        assert _built(get_provider_spec("gemini-vertex", "video"), config, "veo-3.1-generate-preview")["kwargs"] == {
            "backend_type": "vertex",
            "api_key": None,
            "base_url": None,
            "rate_limiter": None,
            "video_model": "veo-3.1-generate-preview",
            "credentials_path": "/legacy/vertex.json",
        }

    @pytest.mark.parametrize(
        ("provider_id", "backend_type", "credentials", "extra"),
        [
            ("gemini-aistudio", "aistudio", {"api_key": "sk-aistudio", "base_url": "https://custom.example.com"}, {}),
            (
                "gemini-vertex",
                "vertex",
                {"credentials_path": "/legacy/vertex.json"},
                {"credentials_path": "/legacy/vertex.json"},
            ),
        ],
    )
    def test_audio_both_modes_build_gemini_tts(self, provider_id, backend_type, credentials, extra):
        config = LoadedConfig(
            credentials=credentials, provider_meta=PROVIDER_REGISTRY.get(provider_id), rate_limiter=None
        )
        assert _built(get_provider_spec(provider_id, "audio"), config, "gemini-3.8-flash-tts") == {
            "media": "audio",
            "backend": "gemini",
            "kwargs": {
                "backend_type": backend_type,
                "api_key": credentials.get("api_key"),
                "base_url": credentials.get("base_url"),
                "model": "gemini-3.8-flash-tts",
                **extra,
            },
        }

    @pytest.mark.parametrize(
        ("provider_id", "endpoint", "backend_type", "credentials", "extra"),
        [
            ("gemini-aistudio", "gemini-interactions-video", "aistudio", {"api_key": "sk-aistudio"}, {}),
            (
                "gemini-vertex",
                "gemini-vertex-interactions-video",
                "vertex",
                {"credentials_path": "/legacy/vertex.json"},
                {"credentials_path": "/legacy/vertex.json"},
            ),
        ],
    )
    def test_interactions_video_endpoint_builds_its_own_backend_with_the_same_credentials(
        self, provider_id, endpoint, backend_type, credentials, extra
    ):
        # Veo remains default; Interactions uses endpoint-named backends with the same credentials/backend_type.
        assert get_provider_spec(provider_id, "video").endpoint != endpoint
        config = LoadedConfig(
            credentials=credentials, provider_meta=PROVIDER_REGISTRY.get(provider_id), rate_limiter=None
        )
        assert _built(get_provider_spec(provider_id, "video", endpoint), config, "gemini-omni-1.1-flash") == {
            "media": "video",
            "backend": endpoint,
            "kwargs": {
                "backend_type": backend_type,
                "api_key": credentials.get("api_key"),
                "base_url": credentials.get("base_url"),
                "rate_limiter": None,
                "video_model": "gemini-omni-1.1-flash",
                **extra,
            },
        }

    def test_bare_gemini_not_registered(self):
        # Bare gemini lacks resolver-produced aistudio/vertex suffixes and is unreachable;
        # fail loudly, never register fallback rows for dead paths.
        assert ("gemini", "image") not in PROVIDER_SPEC_REGISTRY
        assert ("gemini", "video") not in PROVIDER_SPEC_REGISTRY


class TestKlingSpec:
    """Kling chooses api_key bearer first, otherwise access_key/secret_key JWT; image api_model_name
    decouples multimodal registry aliases, with base_url DB > registry defaults (domestic domains
    moved to api-beijing). Video does not accept api_model_name, so its closure deliberately omits it."""

    @staticmethod
    def _kling_config(**credentials) -> LoadedConfig:
        return LoadedConfig(
            credentials=credentials,
            provider_meta=PROVIDER_REGISTRY.get("kling"),
            rate_limiter=None,
        )

    def test_image_dual_secret_and_jwt(self):
        spec = get_provider_spec("kling", "image")
        assert spec.registry_backend == "kling"
        config = self._kling_config(access_key="ak-1", secret_key="sk-1")
        assert _built(spec, config, "kling-image-o1") == {
            "media": "image",
            "backend": "kling",
            "kwargs": {
                "auth_mode": "jwt",
                "access_key": "ak-1",
                "secret_key": "sk-1",
                "model": "kling-image-o1",
                "base_url": "https://api-beijing.klingai.com/v1",
            },
        }

    def test_image_api_model_name_decoupled_for_amphibious_alias(self):
        # Registry kling-v3-omni-image aliases real API model kling-v3-omni.
        config = self._kling_config(access_key="ak-1", secret_key="sk-1")
        assert _built(get_provider_spec("kling", "image"), config, "kling-v3-omni-image")["kwargs"] == {
            "auth_mode": "jwt",
            "access_key": "ak-1",
            "secret_key": "sk-1",
            "model": "kling-v3-omni-image",
            "api_model_name": "kling-v3-omni",
            "base_url": "https://api-beijing.klingai.com/v1",
        }

    def test_image_user_base_url_wins_over_registry_default(self):
        config = self._kling_config(access_key="ak-1", secret_key="sk-1", base_url="https://relay.example.com")
        assert _built(get_provider_spec("kling", "image"), config, "kling-image-o1")["kwargs"] == {
            "auth_mode": "jwt",
            "access_key": "ak-1",
            "secret_key": "sk-1",
            "model": "kling-image-o1",
            "base_url": "https://relay.example.com",
        }

    def test_video_dual_secret_no_api_model_name(self):
        spec = get_provider_spec("kling", "video")
        assert spec.registry_backend == "kling-video-pro-silent"
        config = self._kling_config(access_key="ak-1", secret_key="sk-1")
        # Video omits unsupported api_model_name even for aliases.
        assert _built(spec, config, "kling-v3") == {
            "media": "video",
            "backend": "kling-video-pro-silent",
            "kwargs": {
                "auth_mode": "jwt",
                "access_key": "ak-1",
                "secret_key": "sk-1",
                "model": "kling-v3",
                "base_url": "https://api-beijing.klingai.com/v1",
            },
        }

    def test_video_api_key_dispatches_bearer_mode(self):
        """api_key alone chooses bearer without forwarding access_key/secret_key."""
        config = self._kling_config(api_key="sk-api-1")
        assert _built(get_provider_spec("kling", "video"), config, "kling-v3")["kwargs"] == {
            "auth_mode": "bearer",
            "api_key": "sk-api-1",
            "model": "kling-v3",
            "base_url": "https://api-beijing.klingai.com/v1",
        }

    def test_image_api_key_dispatches_bearer_mode_with_api_model_name(self):
        """Image bearer also decouples api_model_name for multimodal aliases."""
        config = self._kling_config(api_key="sk-api-1")
        assert _built(get_provider_spec("kling", "image"), config, "kling-v3-omni-image")["kwargs"] == {
            "auth_mode": "bearer",
            "api_key": "sk-api-1",
            "model": "kling-v3-omni-image",
            "api_model_name": "kling-v3-omni",
            "base_url": "https://api-beijing.klingai.com/v1",
        }

    def test_model_path_endpoint_uses_api_key_without_version_segment(self):
        from lib.backends.video_backends.kling_model_path import KlingModelPathVideoBackend

        spec = get_provider_spec("kling", "video", "kling-video-model-path")
        backend = spec.build_backend(self._kling_config(api_key="sk-api-1"), "kling-4.0")
        assert isinstance(backend, KlingModelPathVideoBackend)
        # Built-in defaults include /v1; newer API paths join directly to domains.
        assert backend._base_url == "https://api-beijing.klingai.com"

    def test_model_path_endpoint_refuses_dual_secret(self):
        spec = get_provider_spec("kling", "video", "kling-video-model-path")
        with pytest.raises(ValueError, match="只支持 API Key"):
            spec.build_backend(self._kling_config(access_key="ak-1", secret_key="sk-1"), "kling-4.0")

    def test_api_key_takes_priority_over_dual_secret_when_both_set(self):
        """When both auth forms exist, api_key wins and omits access_key/secret_key."""
        config = self._kling_config(api_key="sk-api-1", access_key="ak-1", secret_key="sk-1")
        assert _built(get_provider_spec("kling", "video"), config, "kling-v3")["kwargs"] == {
            "auth_mode": "bearer",
            "api_key": "sk-api-1",
            "model": "kling-v3",
            "base_url": "https://api-beijing.klingai.com/v1",
        }


class TestTextSimpleSpec:
    """Simple text ark/ark-agent-plan/grok/agnes forwards model, unconditional api_key, and nonempty
    base_url (user > registry). Use text create_backend with registry_backend equal to provider_id."""

    def test_ark_falls_back_to_registry_default(self):
        spec = get_provider_spec("ark", "text")
        assert spec.registry_backend == "ark"
        config = _loaded(credentials={"api_key": "ark-key"}, provider_id="ark")
        assert _built(spec, config, "doubao-seed-2-0-lite-260215") == {
            "media": "text",
            "backend": "ark",
            "kwargs": {
                "model": "doubao-seed-2-0-lite-260215",
                "api_key": "ark-key",
                "base_url": "https://ark.cn-beijing.volces.com/api/v3",
            },
        }

    def test_ark_agent_plan_uses_plan_base_url(self):
        spec = get_provider_spec("ark-agent-plan", "text")
        assert spec.registry_backend == "ark-agent-plan"
        config = _loaded(credentials={"api_key": "k"}, provider_id="ark-agent-plan")
        assert _built(spec, config, "doubao-seed-2.0-lite")["kwargs"] == {
            "model": "doubao-seed-2.0-lite",
            "api_key": "k",
            "base_url": "https://ark.cn-beijing.volces.com/api/plan/v3",
        }

    def test_user_base_url_wins(self):
        config = _loaded(credentials={"api_key": "k", "base_url": "https://relay.test/v3"}, provider_id="ark")
        built = _built(get_provider_spec("ark", "text"), config, "m")
        assert built["kwargs"]["base_url"] == "https://relay.test/v3"

    def test_grok_no_default_no_user_omits_base_url(self):
        config = _loaded(credentials={"api_key": "grok-key"}, provider_id="grok")
        assert _built(get_provider_spec("grok", "text"), config, "grok-4")["kwargs"] == {
            "model": "grok-4",
            "api_key": "grok-key",
        }

    def test_agnes_falls_back_to_registry_default_base_url(self):
        # Agnes uses simple text, registry_backend=provider_id, with absent-user registry URL fallback.
        spec = get_provider_spec("agnes", "text")
        assert spec.registry_backend == "agnes"
        config = _loaded(credentials={"api_key": "ag-key"}, provider_id="agnes")
        assert _built(spec, config, "agnes-2.0-flash")["kwargs"] == {
            "model": "agnes-2.0-flash",
            "api_key": "ag-key",
            "base_url": "https://apihub.agnes-ai.com/v1",
        }

    def test_api_key_passed_unconditionally_even_when_missing(self):
        # Text api_key always forwards, including None, unlike omitted defaults for simple media.
        config = _loaded(credentials={}, provider_id="grok")
        assert _built(get_provider_spec("grok", "text"), config, "grok-4")["kwargs"] == {
            "model": "grok-4",
            "api_key": None,
        }


class TestTextGeminiSpec:
    """Gemini text branches AI Studio (unconditional user base_url) / Vertex (backend=vertex + gcs_bucket)
    by provider_id, sharing registry_backend gemini. Text Gemini rejects rate_limiter."""

    def test_aistudio_passes_user_base_url_unconditionally(self):
        spec = get_provider_spec("gemini-aistudio", "text")
        assert spec.registry_backend == "gemini"
        config = _loaded(credentials={"api_key": "g-key", "base_url": ""}, provider_id="gemini-aistudio")
        # Forward base_url unconditionally, including empty strings, without registry fallback.
        assert _built(spec, config, "gemini-3-flash-preview") == {
            "media": "text",
            "backend": "gemini",
            "kwargs": {"model": "gemini-3-flash-preview", "api_key": "g-key", "base_url": ""},
        }

    def test_vertex_uses_gcs_bucket_no_api_key(self):
        spec = get_provider_spec("gemini-vertex", "text")
        assert spec.registry_backend == "gemini"
        config = _loaded(
            credentials={"gcs_bucket": "my-bucket", "credentials_path": "/legacy/vertex.json"},
            provider_id="gemini-vertex",
        )
        assert _built(spec, config, "gemini-3-flash-preview")["kwargs"] == {
            "model": "gemini-3-flash-preview",
            "backend": "vertex",
            "gcs_bucket": "my-bucket",
            "credentials_path": "/legacy/vertex.json",
        }


class TestTextOpenAICompatSpec:
    """OpenAI-compatible text maps openai to openai-responses, forwarding user URLs; dashscope/minimax
    map to openai Chat Completions, derive URLs from hosts, and forward provider_name so usage
    bills their CNY rates rather than OpenAI USD."""

    def test_openai_uses_responses_and_passes_user_base_url(self):
        spec = get_provider_spec("openai", "text")
        assert spec.registry_backend == "openai-responses"
        config = _loaded(credentials={"api_key": "oa", "base_url": "https://relay.test/v1"}, provider_id="openai")
        assert _built(spec, config, "gpt-5") == {
            "media": "text",
            "backend": "openai-responses",
            "kwargs": {"model": "gpt-5", "api_key": "oa", "base_url": "https://relay.test/v1"},
        }

    def test_dashscope_derives_base_url_and_passes_provider_name(self):
        spec = get_provider_spec("dashscope", "text")
        assert spec.registry_backend == "openai"
        config = _loaded(credentials={"api_key": "ds"}, provider_id="dashscope")
        assert _built(spec, config, "qwen-max")["kwargs"] == {
            "model": "qwen-max",
            "api_key": "ds",
            "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
            "provider_name": "dashscope",
        }

    def test_dashscope_user_host_derives_compatible_mode_path(self):
        # Custom hosts still gain helper /compatible-mode/v1 suffixes.
        config = _loaded(
            credentials={"api_key": "ds", "base_url": "https://dashscope-intl.aliyuncs.com"},
            provider_id="dashscope",
        )
        built = _built(get_provider_spec("dashscope", "text"), config, "qwen-max")
        assert built["kwargs"]["base_url"] == "https://dashscope-intl.aliyuncs.com/compatible-mode/v1"

    def test_minimax_derives_base_url_and_passes_provider_name(self):
        spec = get_provider_spec("minimax", "text")
        assert spec.registry_backend == "openai"
        config = _loaded(credentials={"api_key": "mm"}, provider_id="minimax")
        assert _built(spec, config, "minimax-text-01")["kwargs"] == {
            "model": "minimax-text-01",
            "api_key": "mm",
            "base_url": "https://api.minimaxi.com/v1",
            "provider_name": "minimax",
        }


class TestRegistryShape:
    def test_unknown_provider_media_fails_loud(self):
        with pytest.raises(ValueError, match="no builtin ProviderSpec"):
            get_provider_spec("ark", "audio")  # Ark has no audio backend registration.

    def test_audio_registered(self):
        audio_keys = {k for k in PROVIDER_SPEC_REGISTRY if k[1] == "audio"}
        assert audio_keys == {
            ("dashscope", "audio"),
            ("minimax", "audio"),
            ("gemini-aistudio", "audio"),
            ("gemini-vertex", "audio"),
        }

    def test_simple_family_image_video_complete(self):
        for provider in ("ark", "ark-agent-plan", "grok", "openai", "vidu", "dashscope", "minimax"):
            assert (provider, "image") in PROVIDER_SPEC_REGISTRY
            assert (provider, "video") in PROVIDER_SPEC_REGISTRY

    def test_text_family_complete(self):
        # Nine text pairs: seven providers plus Gemini AI Studio/Vertex IDs.
        text_keys = {k for k in PROVIDER_SPEC_REGISTRY if k[1] == "text"}
        assert text_keys == {
            ("ark", "text"),
            ("ark-agent-plan", "text"),
            ("grok", "text"),
            ("agnes", "text"),
            ("gemini-aistudio", "text"),
            ("gemini-vertex", "text"),
            ("openai", "text"),
            ("dashscope", "text"),
            ("minimax", "text"),
        }

    def test_bare_gemini_text_not_registered(self):
        # Bare gemini is unreachable like media; resolvers produce suffixes, so fail loudly without fallback rows.
        assert ("gemini", "text") not in PROVIDER_SPEC_REGISTRY


class TestValidateProviderSpecs:
    """Import-time invariants: callable build, matching keys/spec fields, valid media_type; misconfigurations fail fast."""

    def test_passes_on_real_registry(self):
        _validate_provider_specs()  # Real tables do not raise.
        assert PROVIDER_SPEC_REGISTRY  # Empty tables also pass, so validation must establish real-table nonemptiness.

    def test_non_callable_build_rejected(self, monkeypatch: pytest.MonkeyPatch):
        bad = dataclasses.replace(PROVIDER_SPEC_REGISTRY[("ark", "image")], build_backend="not-callable")
        monkeypatch.setitem(PROVIDER_SPEC_REGISTRY, ("ark", "image"), bad)
        with pytest.raises(ValueError, match="non-callable build_backend"):
            _validate_provider_specs()

    def test_key_field_mismatch_rejected(self, monkeypatch: pytest.MonkeyPatch):
        # provider_id/media_type differing from dictionary keys fail fast.
        bad = dataclasses.replace(PROVIDER_SPEC_REGISTRY[("ark", "image")], provider_id="drifted")
        monkeypatch.setitem(PROVIDER_SPEC_REGISTRY, ("ark", "image"), bad)
        with pytest.raises(ValueError, match=r"key .* does not match spec"):
            _validate_provider_specs()

    def test_registry_backend_names_are_registered(self):
        """Every registry name exists in its backend registry; test during unit imports, never import-time validation."""
        from lib.backends.audio_backends import get_registered_backends as audio_names
        from lib.backends.image_backends import get_registered_backends as image_names
        from lib.backends.text_backends import get_registered_backends as text_names
        from lib.backends.video_backends import get_registered_backends as video_names

        registered = {
            "image": set(image_names()),
            "video": set(video_names()),
            "audio": set(audio_names()),
            "text": set(text_names()),
        }
        for (_provider, media), spec in PROVIDER_SPEC_REGISTRY.items():
            if spec.registry_backend == "declarative":
                continue
            assert spec.registry_backend in registered[media], (
                f"{spec.registry_backend!r} 未注册到 {media} backend registry"
            )


class TestBuiltinEffectiveGenerateAudio:
    """Declarative built-in billing ``generate_audio`` shares declared output-audio facts."""

    def test_an_always_off_declarative_model_reports_no_audio(self):
        from lib.backends.backend_assembly.specs import builtin_effective_generate_audio_for_model

        assert builtin_effective_generate_audio_for_model("minimax", "MiniMax-Hailuo-2.3") is False

    def test_an_always_on_declarative_model_reports_audio(self):
        from lib.backends.backend_assembly.specs import builtin_effective_generate_audio_for_model

        assert builtin_effective_generate_audio_for_model("minimax", "MiniMax-H3") is True
