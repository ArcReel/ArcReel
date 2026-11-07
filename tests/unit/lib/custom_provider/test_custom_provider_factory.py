"""Unit tests for create_custom_backend(provider, model_id, endpoint).

Replace real backend classes in endpoints with recording factories: each output records which class and constructor arguments were used.
Assert complete construction records to catch extra/missing arguments; delegates remain usable objects for wrapper construction.
"""

from __future__ import annotations

from collections.abc import Generator
from contextlib import ExitStack, contextmanager
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from lib.backends.image_backends.ark import SEEDREAM_BACKENDS
from lib.backends.image_backends.dashscope import DASHSCOPE_IMAGE_BACKENDS
from lib.backends.image_backends.kling import KLING_IMAGE_BACKENDS
from lib.backends.provider_assets import InferenceCredentialRef
from lib.backends.video_backends.ark import SEEDANCE_BACKENDS
from lib.backends.video_backends.dashscope import DASHSCOPE_VIDEO_BACKENDS
from lib.custom_provider.backends import (
    CustomAudioBackend,
    CustomImageBackend,
    CustomTextBackend,
    CustomVideoBackend,
)
from lib.custom_provider.factory import create_custom_backend

# All backend classes directly constructed by build_backend closures in endpoints.
_ENDPOINT_BACKEND_CLASSES = (
    "AnthropicTextBackend",
    "DeclarativeVideoBackend",
    "GeminiAudioBackend",
    "GeminiImageBackend",
    "GeminiTextBackend",
    "MiniMaxImageBackend",
    "OpenAIAudioBackend",
    "OpenAIImageBackend",
    "OpenAITextBackend",
    "OpenAIResponsesTextBackend",
    "OpenAIVideoBackend",
)


@contextmanager
def _endpoint_backends() -> Generator[list[dict[str, Any]]]:
    """Record endpoint backend class names and constructor arguments without creating SDK clients."""
    records: list[dict[str, Any]] = []

    def _recorder(cls_name: str):
        def _build(**kwargs: Any) -> Any:
            records.append({"backend": cls_name, "kwargs": kwargs})
            return MagicMock()

        return _build

    with ExitStack() as stack:
        for cls_name in _ENDPOINT_BACKEND_CLASSES:
            stack.enter_context(patch(f"lib.custom_provider.endpoints.{cls_name}", _recorder(cls_name)))
        # Seedance generation endpoints select channel classes by endpoint name.
        seedance = {key: _recorder(backend.__name__) for key, backend in SEEDANCE_BACKENDS.items()}
        stack.enter_context(patch("lib.custom_provider.endpoints.SEEDANCE_BACKENDS", seedance))
        for mapping_name, backends in (
            ("KLING_IMAGE_BACKENDS", KLING_IMAGE_BACKENDS),
            ("SEEDREAM_BACKENDS", SEEDREAM_BACKENDS),
            ("DASHSCOPE_IMAGE_BACKENDS", DASHSCOPE_IMAGE_BACKENDS),
        ):
            stack.enter_context(
                patch(
                    f"lib.custom_provider.endpoints.{mapping_name}",
                    {key: _recorder(backend.__name__) for key, backend in backends.items()},
                )
            )
        from lib.backends.video_backends.kling import KLING_BACKENDS
        from lib.backends.video_backends.vidu import VIDU_BACKENDS

        for name, backends, label in (
            ("VIDU_BACKENDS", VIDU_BACKENDS, "ViduVideoBackend"),
            ("KLING_BACKENDS", KLING_BACKENDS, "KlingVideoBackend"),
        ):
            stack.enter_context(
                patch(f"lib.custom_provider.endpoints.{name}", {key: _recorder(label) for key in backends})
            )

        dashscope = {key: _recorder(backend.__name__) for key, backend in DASHSCOPE_VIDEO_BACKENDS.items()}
        stack.enter_context(patch("lib.custom_provider.endpoints.DASHSCOPE_VIDEO_BACKENDS", dashscope))
        yield records


def _make_provider(*, base_url: str = "https://api.example.com/v1", api_key: str = "sk-test") -> MagicMock:
    p = MagicMock()
    p.base_url = base_url
    p.api_key = api_key
    p.provider_id = "custom-42"
    p.id = 42
    return p


def _built(provider: MagicMock, model_id: str, endpoint: str, **kwargs: Any) -> tuple[Any, dict[str, Any]]:
    """Run the factory once and return (wrapper result, sole delegate-construction record)."""
    with _endpoint_backends() as records:
        result = create_custom_backend(provider=provider, model_id=model_id, endpoint=endpoint, **kwargs)
    assert len(records) == 1
    return result, records[0]


class TestEndpointDispatch:
    def test_openai_chat(self):
        # Host-only base_url: cover OpenAI endpoint wiring that appends /v1.
        provider = _make_provider(base_url="https://api.example.com")
        result, built = _built(provider, "gpt-4o", "openai-chat")
        assert isinstance(result, CustomTextBackend)
        assert result.model == "gpt-4o"
        assert built == {
            "backend": "OpenAITextBackend",
            "kwargs": {
                "api_key": "sk-test",
                "base_url": "https://api.example.com/v1",
                "model": "gpt-4o",
                "transport_layer": None,
            },
        }

    def test_gemini_generate(self):
        provider = _make_provider(base_url="https://generativelanguage.googleapis.com")
        _result, built = _built(provider, "gemini-2.5-flash", "gemini-generate")
        assert built == {
            "backend": "GeminiTextBackend",
            "kwargs": {
                "api_key": "sk-test",
                "base_url": "https://generativelanguage.googleapis.com/",
                "model": "gemini-2.5-flash",
                "transport_layer": None,
            },
        }

    def test_openai_images(self):
        result, built = _built(_make_provider(), "dall-e-3", "openai-images")
        assert isinstance(result, CustomImageBackend)
        assert built == {
            "backend": "OpenAIImageBackend",
            "kwargs": {
                "api_key": "sk-test",
                "base_url": "https://api.example.com/v1",
                "model": "dall-e-3",
                "transport_layer": None,
            },
        }

    @pytest.mark.parametrize(
        ("endpoint", "backend_name"),
        [
            ("ark-seedream-3", "ArkSeedream3ImageBackend"),
            ("ark-seedream-4", "ArkImageBackend"),
            ("dashscope-qwen-image", "DashScopeImageBackend"),
            ("dashscope-wan-image", "DashScopeWanImageBackend"),
            ("dashscope-qwen-image-edit", "DashScopeQwenEditImageBackend"),
        ],
    )
    def test_split_image_endpoint_builds_its_request_contract(self, endpoint: str, backend_name: str):
        result, built = _built(_make_provider(base_url="https://api.example.com"), "opaque", endpoint)
        assert isinstance(result, CustomImageBackend)
        assert built == {
            "backend": backend_name,
            "kwargs": {
                "api_key": "sk-test",
                "base_url": "https://api.example.com/api/v3"
                if endpoint.startswith("ark-")
                else "https://api.example.com",
                "model": "opaque",
                "transport_layer": None,
            },
        }

    def test_gemini_image(self):
        provider = _make_provider(base_url="https://generativelanguage.googleapis.com")
        _result, built = _built(provider, "imagen-4", "gemini-image")
        assert built == {
            "backend": "GeminiImageBackend",
            "kwargs": {
                "api_key": "sk-test",
                "base_url": "https://generativelanguage.googleapis.com/",
                "image_model": "imagen-4",
                "transport_layer": None,
            },
        }

    def test_gemini_tts(self):
        provider = _make_provider(base_url="https://generativelanguage.googleapis.com")
        result, built = _built(provider, "gemini-3.8-flash-tts", "gemini-tts")
        assert isinstance(result, CustomAudioBackend)
        assert built == {
            "backend": "GeminiAudioBackend",
            "kwargs": {
                "api_key": "sk-test",
                "base_url": "https://generativelanguage.googleapis.com/",
                "model": "gemini-3.8-flash-tts",
                "provider_name": "custom-42",
                "transport_layer": None,
            },
        }

    def test_openai_video(self):
        result, built = _built(_make_provider(), "sora-2", "openai-video")
        assert isinstance(result, CustomVideoBackend)
        assert built == {
            "backend": "OpenAIVideoBackend",
            "kwargs": {
                "api_key": "sk-test",
                "base_url": "https://api.example.com/v1",
                "model": "sora-2",
                "transport_layer": None,
            },
        }

    def test_newapi_video(self):
        _result, built = _built(_make_provider(), "kling-v2", "newapi-video")
        assert built["backend"] == "DeclarativeVideoBackend"
        assert built["kwargs"]["model"] == "kling-v2"
        assert built["kwargs"]["provider"] == "custom-42"

    def test_v2_video_generations(self):
        provider = _make_provider(base_url="https://api.aimlapi.com")
        result, built = _built(provider, "bytedance/seedance-1-0-lite-i2v", "v2-video-generations")
        assert isinstance(result, CustomVideoBackend)
        assert built["backend"] == "DeclarativeVideoBackend"
        assert built["kwargs"]["base_url"] == "https://api.aimlapi.com"
        assert built["kwargs"]["model"] == "bytedance/seedance-1-0-lite-i2v"

    def test_ark_seedance(self):
        provider = _make_provider(base_url="https://relay.example.com")
        result, built = _built(provider, "doubao-seedance-2-0", "ark-seedance-2")
        assert isinstance(result, CustomVideoBackend)
        # Host only: append Ark mount /api/v3; material-library bindings compare provider records with the original configured Base URL.
        assert built == {
            "backend": "Seedance2VideoBackend",
            "kwargs": {
                "api_key": "sk-test",
                "base_url": "https://relay.example.com/api/v3",
                "model": "doubao-seedance-2-0",
                "transport_layer": None,
                "inference_credential": InferenceCredentialRef(
                    credential_type="custom", credential_id=42, api_key="sk-test", base_url="https://relay.example.com"
                ),
            },
        }

    @pytest.mark.parametrize(
        ("endpoint", "backend"),
        [
            ("ark-seedance-1", "Seedance1VideoBackend"),
            ("ark-seedance-2", "Seedance2VideoBackend"),
            ("ark-seedance-2-5", "Seedance25VideoBackend"),
        ],
    )
    def test_each_seedance_endpoint_builds_its_own_generation(self, endpoint: str, backend: str):
        """The same model name on different Seedance endpoints constructs the channel selected by each endpoint."""
        _result, built = _built(_make_provider(base_url="https://relay.example.com"), "relay-video", endpoint)
        assert built["backend"] == backend

    def test_vidu_video(self):
        provider = _make_provider(base_url="https://relay.example.com")
        result, built = _built(provider, "viduq3-turbo", "vidu-video-q3-turbo")
        assert isinstance(result, CustomVideoBackend)
        # Host only: append Vidu protocol mount /ent/v2.
        assert built == {
            "backend": "ViduVideoBackend",
            "kwargs": {
                "api_key": "sk-test",
                "base_url": "https://relay.example.com/ent/v2",
                "model": "viduq3-turbo",
                "transport_layer": None,
            },
        }

    def test_openai_tts(self):
        result, built = _built(_make_provider(), "tts-1", "openai-tts")
        assert isinstance(result, CustomAudioBackend)
        assert result.name == "custom-42"
        assert result.model == "tts-1"
        # provider_name attributes delegate accounting/logging to the actual provider instead of built-in openai.
        assert built == {
            "backend": "OpenAIAudioBackend",
            "kwargs": {
                "api_key": "sk-test",
                "base_url": "https://api.example.com/v1",
                "model": "tts-1",
                "provider_name": "custom-42",
                "transport_layer": None,
            },
        }

    def test_openai_tts_appends_v1(self):
        provider = _make_provider(base_url="https://relay.example.com")
        _result, built = _built(provider, "speech-1.5", "openai-tts")
        assert built["kwargs"]["base_url"] == "https://relay.example.com/v1"

    def test_minimax_image(self):
        provider = _make_provider(base_url="https://api.minimaxi.com/v1")
        result, built = _built(provider, "image-01", "minimax-image")
        assert isinstance(result, CustomImageBackend)
        assert result.model == "image-01"
        # Pass base_url unchanged; MiniMaxImageBackend normalizes host→{host}/v1 internally.
        assert built == {
            "backend": "MiniMaxImageBackend",
            "kwargs": {
                "api_key": "sk-test",
                "base_url": "https://api.minimaxi.com/v1",
                "model": "image-01",
                "transport_layer": None,
            },
        }

    def test_minimax_video(self):
        provider = _make_provider(base_url="https://api.minimaxi.com/v1")
        result, built = _built(provider, "MiniMax-Hailuo-2.3", "minimax-hailuo-v1")
        assert isinstance(result, CustomVideoBackend)
        assert result.model == "MiniMax-Hailuo-2.3"
        assert built["backend"] == "DeclarativeVideoBackend"
        assert built["kwargs"]["base_url"] == "https://api.minimaxi.com/v1"

    def test_kling_image(self):
        provider = _make_provider(base_url="https://relay.example.com/v1")
        result, built = _built(provider, "kling-image-o1", "kling-image")
        assert isinstance(result, CustomImageBackend)
        assert result.model == "kling-image-o1"
        # Bearer mode: static api_key bypasses JWT; trust explicit /v1 unchanged and pass native model_name through.
        assert built == {
            "backend": "KlingImageBackend",
            "kwargs": {
                "auth_mode": "bearer",
                "api_key": "sk-test",
                "base_url": "https://relay.example.com/v1",
                "model": "kling-image-o1",
                "transport_layer": None,
            },
        }

    def test_kling_image_host_only_mounts_v1(self):
        provider = _make_provider(base_url="https://relay.example.com")
        _result, built = _built(provider, "kling-image-o1", "kling-image")
        # Host only: append Kling protocol mount /v1.
        assert built["kwargs"]["base_url"] == "https://relay.example.com/v1"

    def test_kling_video(self):
        provider = _make_provider(base_url="https://relay.example.com/v1")
        result, built = _built(provider, "kling-v2-5-turbo", "kling-video-pro-silent")
        assert isinstance(result, CustomVideoBackend)
        assert result.model == "kling-v2-5-turbo"
        assert built == {
            "backend": "KlingVideoBackend",
            "kwargs": {
                "auth_mode": "bearer",
                "api_key": "sk-test",
                "base_url": "https://relay.example.com/v1",
                "model": "kling-v2-5-turbo",
                "transport_layer": None,
            },
        }

    def test_kling_video_host_only_mounts_v1(self):
        provider = _make_provider(base_url="relay.example.com")
        _result, built = _built(provider, "kling-v3", "kling-video-pro-silent")
        # Bare host without scheme: add https://, then mount /v1.
        assert built["kwargs"]["base_url"] == "https://relay.example.com/v1"

    def test_openai_images_generations(self):
        result, built = _built(_make_provider(), "dall-e-3", "openai-images-generations")
        assert isinstance(result, CustomImageBackend)
        assert built == {
            "backend": "OpenAIImageBackend",
            "kwargs": {
                "api_key": "sk-test",
                "base_url": "https://api.example.com/v1",
                "model": "dall-e-3",
                "mode": "generations_only",
                "transport_layer": None,
            },
        }

    def test_openai_images_edits(self):
        result, built = _built(_make_provider(), "dall-e-3", "openai-images-edits")
        assert isinstance(result, CustomImageBackend)
        assert built["kwargs"]["mode"] == "edits_only"


class TestUrlNormalization:
    """Mount-path completion (_ensure_url_path_suffix) is covered only here. Pure normalization such as OpenAI /v1 addition
    and Google version-segment stripping is covered by tests/unit/lib/config/test_normalize_base_url.py, not repeated here."""

    def test_ark_explicit_path_passthrough(self):
        """Trust an existing explicit path (/api/v3) unchanged without appending it again."""
        provider = _make_provider(base_url="https://relay.example.com/api/v3")
        _result, built = _built(provider, "doubao-seedance-2-0", "ark-seedance-2")
        assert built["kwargs"]["base_url"] == "https://relay.example.com/api/v3"

    def test_ark_mounted_base_url_appends_api_v3(self):
        provider = _make_provider(base_url="https://relay.example.com/seedance")
        _result, built = _built(provider, "doubao-seedance-2-0", "ark-seedance-2")
        assert built["kwargs"]["base_url"] == "https://relay.example.com/seedance/api/v3"

    def test_vidu_explicit_path_passthrough(self):
        provider = _make_provider(base_url="https://api.vidu.cn/ent/v2")
        _result, built = _built(provider, "viduq3-turbo", "vidu-video-q3-turbo")
        assert built["kwargs"]["base_url"] == "https://api.vidu.cn/ent/v2"

    def test_ark_host_only_no_scheme(self):
        """Bare host without scheme: add https://, then mount /api/v3."""
        provider = _make_provider(base_url="relay.example.com")
        _result, built = _built(provider, "doubao-seedance-2-0", "ark-seedance-2")
        assert built["kwargs"]["base_url"] == "https://relay.example.com/api/v3"

    def test_vidu_host_only_no_scheme(self):
        provider = _make_provider(base_url="relay.example.com")
        _result, built = _built(provider, "viduq3-turbo", "vidu-video-q3-turbo")
        assert built["kwargs"]["base_url"] == "https://relay.example.com/ent/v2"

    def test_anthropic_messages_strips_pasted_endpoint_suffix(self):
        # The SDK appends /v1/messages itself: strip that suffix from pasted endpoints, preserve prefixes, and do not add /v1.
        provider = _make_provider(base_url="https://relay.example.com/anthropic/v1/messages")
        result, built = _built(provider, "claude-x", "anthropic-messages")
        assert isinstance(result, CustomTextBackend)
        assert built == {
            "backend": "AnthropicTextBackend",
            "kwargs": {
                "api_key": "sk-test",
                "base_url": "https://relay.example.com/anthropic",
                "model": "claude-x",
                "transport_layer": None,
            },
        }

    def test_ark_empty_base_url_normalizes_to_none(self):
        """Empty base_url normalizes to None through _ensure_url_path_suffix, without forcing a mount path."""
        provider = _make_provider(base_url="")
        _result, built = _built(provider, "doubao-seedance-2-0", "ark-seedance-2")
        assert built["kwargs"]["base_url"] is None

    def test_vidu_empty_base_url_normalizes_to_none(self):
        provider = _make_provider(base_url="")
        _result, built = _built(provider, "viduq3-turbo", "vidu-video-q3-turbo")
        assert built["kwargs"]["base_url"] is None


class TestErrors:
    def test_unknown_endpoint(self):
        provider = _make_provider()
        with pytest.raises(ValueError, match="unknown endpoint"):
            create_custom_backend(provider=provider, model_id="claude-4", endpoint="acme-unknown")

    def test_v2_empty_base_url_raises(self):
        """v2-video-generations requires base_url (no default host); empty values fail loudly."""
        provider = _make_provider(base_url="")
        with pytest.raises(ValueError, match="需要 base_url"):
            create_custom_backend(provider=provider, model_id="some-model", endpoint="v2-video-generations")


class TestVideoEndpointRecorded:
    """The factory retains the construction endpoint in video wrappers for resume protocol comparison (`docs/adr/0054`)."""

    def test_video_backend_records_endpoint(self):
        result, _built_record = _built(_make_provider(), "sora-2", "openai-video")
        assert isinstance(result, CustomVideoBackend)
        assert result.endpoint == "openai-video"
        assert result.name == "custom-42"

    def test_endpoint_survives_capability_injection(self):
        """Capability injection returns a new instance; endpoint must survive chained construction."""
        result, _built_record = _built(
            _make_provider(),
            "MiniMax-Hailuo-02",
            "minimax-hailuo-v1",
            capability_overrides={"last_frame": True},
        )
        assert isinstance(result, CustomVideoBackend)
        assert result.endpoint == "minimax-hailuo-v1"
