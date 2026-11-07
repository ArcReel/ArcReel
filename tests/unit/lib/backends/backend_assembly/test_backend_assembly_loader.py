"""assemble_backend simple built-in async loading: in-memory SQLite and real ConfigResolver.

Mirror test_config_resolver.py without resolver mocks: credential overlays reach LoadedConfig,
assembly constructs simple-family backends end-to-end, and unknown provider/media pairs fail loudly.
"""

from __future__ import annotations

from datetime import date
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from lib.backends.backend_assembly import assemble_backend
from lib.backends.backend_assembly.assembler import OutputLimitFacts, _load_builtin_config, output_limit_facts
from lib.backends.backend_assembly.specs import PROVIDER_SPEC_REGISTRY
from lib.backends.text_backends.base import TextCapability, TextGenerationRequest
from lib.catalog import query as catalog
from lib.catalog.errors import ModelDiscontinuedError, ModelNotInCatalogError
from lib.catalog.query import factory_snapshot
from lib.config.resolver import ConfigResolver
from lib.config.service import ConfigService
from tests.catalog_support import install_catalog, set_catalog_today, with_entry_changes, with_min_app_version
from tests.fakes import (
    captured_backend_construction,
    captured_openai_clients,
    captured_vertex_credentials,
    patched_instructor_from_openai,
)

SEEDREAM_LITE = "doubao-seedream-5-0-lite-260128"


async def _seed_provider_config(factory, provider: str, **kv: str) -> None:
    async with factory() as session:
        svc = ConfigService(session)
        for key, value in kv.items():
            await svc.set_provider_config(provider, key, value)
        await session.commit()


class TestLoadBuiltinConfig:
    async def test_credential_overlay_enters_envelope(self, db_factory):
        await _seed_provider_config(db_factory, "ark", api_key="ark-secret", base_url="https://relay.test/api/v3")
        resolver = ConfigResolver(db_factory)
        config = await _load_builtin_config(resolver, "ark", rate_limiter=None)
        assert config.credentials.get("api_key") == "ark-secret"
        assert config.credentials.get("base_url") == "https://relay.test/api/v3"
        # Registry metadata enters envelopes for default_base_url fallback.
        assert config.provider_meta is not None
        assert config.provider_meta.default_base_url == "https://ark.cn-beijing.volces.com/api/v3"

    async def test_active_credential_record_id_enters_envelope(self, db_factory):
        """Effective credential-record IDs accompany credentials; provider-config-only credentials have no record IDs."""
        from lib.db.repositories.credential_repository import CredentialRepository

        await _seed_provider_config(db_factory, "ark", api_key="TEST-config-key")
        resolver = ConfigResolver(db_factory)
        assert (await _load_builtin_config(resolver, "ark", rate_limiter=None)).credential_id is None

        async with db_factory() as session:
            cred = await CredentialRepository(session).create(provider="ark", name="主 Key", api_key="TEST-ark-key")
            await session.commit()
        config = await _load_builtin_config(resolver, "ark", rate_limiter=None)
        assert config.credentials.get("api_key") == "TEST-ark-key"
        assert config.credential_id == cred.id

    async def test_rate_limiter_carried_into_envelope(self, db_factory):
        sentinel = object()
        resolver = ConfigResolver(db_factory)
        config = await _load_builtin_config(resolver, "grok", rate_limiter=sentinel)
        assert config.rate_limiter is sentinel


class TestAssembleBuiltinEndToEnd:
    async def test_simple_image_end_to_end(self, db_factory):
        await _seed_provider_config(db_factory, "ark", api_key="ark-secret")
        resolver = ConfigResolver(db_factory)
        with captured_backend_construction() as built:
            await assemble_backend(
                provider_id="ark", media_type="image", model_id="doubao-seedream-5-0-lite-260128", resolver=resolver
            )
        # Absent user base_url falls back to registry defaults; loaded overlays reach constructor arguments.
        assert built == [
            {
                "media": "image",
                "backend": "ark-seedream-4",
                "kwargs": {
                    "api_key": "ark-secret",
                    "model": "doubao-seedream-5-0-lite-260128",
                    "base_url": "https://ark.cn-beijing.volces.com/api/v3",
                },
            }
        ]

    async def test_model_outside_provider_listing_is_rejected_without_fallback(self, db_factory):
        """Models absent from provider lists/local entries error for reselection, never construct or silently switch models."""
        await _seed_provider_config(db_factory, "openai", api_key="sk")
        resolver = ConfigResolver(db_factory)
        with captured_backend_construction() as built, pytest.raises(ModelNotInCatalogError) as excinfo:
            await assemble_backend(
                provider_id="openai", media_type="video", model_id="never-listed-video", resolver=resolver
            )
        assert built == []
        assert (excinfo.value.code, excinfo.value.params) == (
            "model_not_in_provider_catalog",
            {"provider": "openai", "model": "never-listed-video"},
        )

    async def test_unlisted_model_with_a_discontinued_local_entry_reports_the_lifecycle(self, db_factory):
        """Removed provider-list models matching retired local entries, sora-2, report retirement without backend construction."""
        await _seed_provider_config(db_factory, "openai", api_key="sk")
        resolver = ConfigResolver(db_factory)
        with captured_backend_construction() as built, pytest.raises(ModelNotInCatalogError) as excinfo:
            await assemble_backend(provider_id="openai", media_type="video", model_id="sora-2", resolver=resolver)
        assert built == []
        assert (excinfo.value.code, excinfo.value.params) == (
            "model_discontinued",
            {"provider": "openai", "model": "sora-2", "shutdown_date": "2026-09-24"},
        )

    async def test_discontinued_model_is_rejected_with_its_successor(self, db_factory, monkeypatch):
        """Retired built-in models error with dates/official successors, never constructing backends."""
        install_catalog(
            monkeypatch,
            with_entry_changes(
                factory_snapshot(),
                "ark",
                SEEDREAM_LITE,
                shutdown_date=date(2026, 11, 24),
                successor="bytedance/doubao-seedream-5-0-pro",
            ),
        )
        set_catalog_today(monkeypatch, date(2026, 11, 24))
        await _seed_provider_config(db_factory, "ark", api_key="ark-secret")
        resolver = ConfigResolver(db_factory)
        with captured_backend_construction() as built, pytest.raises(ModelDiscontinuedError) as excinfo:
            await assemble_backend(provider_id="ark", media_type="image", model_id=SEEDREAM_LITE, resolver=resolver)
        assert built == []
        assert excinfo.value.params == {
            "provider": "ark",
            "model": SEEDREAM_LITE,
            "shutdown_date": "2026-11-24",
            "successor": "bytedance/doubao-seedream-5-0-pro",
        }

    async def test_default_resolution_skips_a_discontinued_default(self, db_factory, monkeypatch):
        """Retired catalog defaults never assemble or fall back to hardcoded backend names; automatic inference skips their providers."""
        from lib.db.repositories.credential_repository import CredentialRepository

        async with db_factory() as session:
            await CredentialRepository(session).create(provider="ark", name="主 Key", api_key="TEST-ark-key")
            await session.commit()
        resolver = ConfigResolver(db_factory)
        snapshot = factory_snapshot()
        default = snapshot.builtin_providers["ark"].defaults["image"]
        resolved = await resolver.resolve_image_backend(None, None, generation_type="t2i")
        assert (resolved.provider_id, resolved.model_id) == ("ark", default)

        install_catalog(monkeypatch, with_entry_changes(snapshot, "ark", default, shutdown_date=date(2026, 1, 1)))
        with captured_backend_construction() as built, pytest.raises(ModelNotInCatalogError) as excinfo:
            await assemble_backend(provider_id="ark", media_type="image", model_id=None, resolver=resolver)
        assert built == []
        assert excinfo.value.code == "provider_unsupported_media"
        # Only ready provider lacks usable default images; inference has no candidate and never selects retired models.
        with pytest.raises(ValueError, match="未找到可用的 image 供应商"):
            await resolver.resolve_image_backend(None, None, generation_type="t2i")

    async def test_version_gated_default_is_neither_assembled_nor_resolved(self, db_factory, monkeypatch):
        """Catalog defaults requiring newer app versions never assemble, explicit or defaulted; treat as absent defaults and skip inference."""
        from lib.db.repositories.credential_repository import CredentialRepository

        async with db_factory() as session:
            await CredentialRepository(session).create(provider="ark", name="主 Key", api_key="TEST-ark-key")
            await session.commit()
        resolver = ConfigResolver(db_factory)
        snapshot = factory_snapshot()
        default = snapshot.builtin_providers["ark"].defaults["image"]
        install_catalog(monkeypatch, with_min_app_version(snapshot, "ark", default, "999.0.0"))

        with captured_backend_construction() as built, pytest.raises(ModelNotInCatalogError) as named:
            await assemble_backend(provider_id="ark", media_type="image", model_id=default, resolver=resolver)
        with captured_backend_construction() as built_default, pytest.raises(ModelNotInCatalogError) as missing:
            await assemble_backend(provider_id="ark", media_type="image", model_id=None, resolver=resolver)
        assert (built, built_default) == ([], [])
        assert (named.value.code, missing.value.code) == ("model_not_in_provider_catalog", "provider_unsupported_media")
        with pytest.raises(ValueError, match="未找到可用的 image 供应商"):
            await resolver.resolve_image_backend(None, None, generation_type="t2i")

    async def test_missing_model_takes_the_catalog_default(self, db_factory):
        await _seed_provider_config(db_factory, "ark", api_key="ark-secret")
        resolver = ConfigResolver(db_factory)
        with captured_backend_construction() as built:
            await assemble_backend(provider_id="ark", media_type="video", model_id=None, resolver=resolver)
        assert built[0]["kwargs"]["model"] == "doubao-seedance-2-0-mini-260615"

    async def test_missing_model_skips_a_disabled_default(self, db_factory):
        """User-disabled catalog defaults error as no default when model is omitted, never constructing backends."""
        from lib.db.models.builtin_provider_model import BuiltinProviderModelState

        await _seed_provider_config(db_factory, "ark", api_key="ark-secret")
        async with db_factory() as session:
            session.add(
                BuiltinProviderModelState(provider_id="ark", model_id="doubao-seedance-2-0-mini-260615", enabled=False)
            )
            await session.commit()
        resolver = ConfigResolver(db_factory)
        with captured_backend_construction() as built, pytest.raises(ModelNotInCatalogError) as excinfo:
            await assemble_backend(provider_id="ark", media_type="video", model_id=None, resolver=resolver)
        assert built == []
        assert excinfo.value.code == "provider_unsupported_media"

    async def test_unknown_provider_media_fails_loud(self, db_factory):
        resolver = ConfigResolver(db_factory)
        with pytest.raises(ValueError, match="no builtin ProviderSpec"):
            await assemble_backend(provider_id="ark", media_type="audio", model_id="x", resolver=resolver)

    async def test_gemini_aistudio_image_end_to_end(self, db_factory):
        # Gemini provider_id selects backend_type; loaded overlays/shared rate_limiter enter closures.
        await _seed_provider_config(db_factory, "gemini-aistudio", api_key="g-secret", base_url="https://g.relay.test")
        sentinel = object()
        resolver = ConfigResolver(db_factory)
        with captured_backend_construction() as built:
            await assemble_backend(
                provider_id="gemini-aistudio",
                media_type="image",
                model_id="gemini-3.1-flash-image",
                resolver=resolver,
                rate_limiter=sentinel,
            )
        assert built == [
            {
                "media": "image",
                "backend": "gemini",
                "kwargs": {
                    "backend_type": "aistudio",
                    "api_key": "g-secret",
                    "base_url": "https://g.relay.test",
                    "rate_limiter": sentinel,
                    "image_model": "gemini-3.1-flash-image",
                },
            }
        ]

    async def test_text_simple_end_to_end(self, db_factory):
        # Simple text overlays reach constructor arguments; base_url falls back to registry defaults.
        await _seed_provider_config(db_factory, "ark", api_key="ark-secret")
        resolver = ConfigResolver(db_factory)
        with captured_backend_construction() as built:
            await assemble_backend(
                provider_id="ark", media_type="text", model_id="doubao-seed-2-1-lite-260915", resolver=resolver
            )
        assert built == [
            {
                "media": "text",
                "backend": "ark",
                "kwargs": {
                    "model": "doubao-seed-2-1-lite-260915",
                    "api_key": "ark-secret",
                    "base_url": "https://ark.cn-beijing.volces.com/api/v3",
                },
            }
        ]

    async def test_text_dashscope_compat_end_to_end(self, db_factory):
        # DashScope OpenAI-compatible text derives helper URLs and provider_name billing through the unified seam.
        await _seed_provider_config(db_factory, "dashscope", api_key="ds-secret")
        resolver = ConfigResolver(db_factory)
        with captured_backend_construction() as built:
            await assemble_backend(provider_id="dashscope", media_type="text", model_id="qwen-plus", resolver=resolver)
        assert built == [
            {
                "media": "text",
                "backend": "openai",
                "kwargs": {
                    "model": "qwen-plus",
                    "api_key": "ds-secret",
                    "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
                    "provider_name": "dashscope",
                },
            }
        ]

    async def test_dashscope_qwen_long_resolved_backend_skips_native_structured_output(self, db_factory):
        from pydantic import BaseModel

        class Person(BaseModel):
            name: str

        await _seed_provider_config(db_factory, "dashscope", api_key="ds-secret")
        instructor_result = Person(name="Alice")
        instructor_completion = MagicMock()
        instructor_completion.usage = MagicMock()
        instructor_completion.usage.prompt_tokens = 50
        instructor_completion.usage.completion_tokens = 20

        mock_client = AsyncMock()
        mock_patched = AsyncMock(on=MagicMock())
        mock_patched.chat.completions.create_with_completion = AsyncMock(
            return_value=(instructor_result, instructor_completion)
        )

        with (
            captured_openai_clients(mock_client),
            patched_instructor_from_openai(return_value=mock_patched),
        ):
            backend = await assemble_backend(
                provider_id="dashscope",
                media_type="text",
                model_id="qwen-long",
                resolver=ConfigResolver(db_factory),
            )
            assert TextCapability.STRUCTURED_OUTPUT not in backend.capabilities
            result = await backend.generate(TextGenerationRequest(prompt="Extract", response_schema=Person))

        assert result.text == instructor_result.model_dump_json()
        mock_client.chat.completions.create.assert_not_awaited()
        mock_patched.chat.completions.create_with_completion.assert_awaited_once()

    async def test_kling_image_api_model_name_decoupled_end_to_end(self, db_factory):
        # Kling dual-secret overlays reach closures; registry aliases supply decoupled api_model_name.
        await _seed_provider_config(db_factory, "kling", access_key="ak-1", secret_key="sk-1")
        resolver = ConfigResolver(db_factory)
        with captured_backend_construction() as built:
            await assemble_backend(
                provider_id="kling", media_type="image", model_id="kling-v3-omni-image", resolver=resolver
            )
        assert built == [
            {
                "media": "image",
                "backend": "kling",
                "kwargs": {
                    "auth_mode": "jwt",
                    "access_key": "ak-1",
                    "secret_key": "sk-1",
                    "model": "kling-v3-omni-image",
                    "api_model_name": "kling-v3-omni",
                    "base_url": "https://api-beijing.klingai.com/v1",
                },
            }
        ]


def _any_listed_model(provider_id: str, media_type: str) -> str | None:
    """Provider/media defaults use catalog choices, otherwise first nonretired same-media listed model, or None when none exists."""
    default = catalog.default_model(provider_id, media_type)
    if default is not None:
        return default
    return next(
        (
            key
            for key, model in catalog.list_models(provider_id).items()
            if model.media_type == media_type and not model.hidden
        ),
        None,
    )


class TestAssembleBuiltinWithRealSdkClients:
    """Construct every built-in backend through assemble_backend worker threads without substituting SDK clients.

    asyncio.to_thread workers have no event loops; construction-time loop-dependent clients
    such as xai_sdk.AsyncClient grpc.aio channels would fail here.
    """

    @pytest.mark.parametrize(("provider_id", "media_type"), sorted(PROVIDER_SPEC_REGISTRY))
    async def test_builtin_backend_constructs_off_the_event_loop(self, db_factory, tmp_path, provider_id, media_type):
        model_id = _any_listed_model(provider_id, media_type)
        if model_id is None:
            pytest.skip(f"模型目录里没有 {provider_id} 的 {media_type} 模型，无从构造")
        credentials_file = tmp_path / "vertex.json"
        credentials_file.write_text('{"project_id": "demo-project"}', encoding="utf-8")
        await _seed_provider_config(
            db_factory,
            provider_id,
            api_key="secret",
            credentials_path=str(credentials_file),
            gcs_bucket="demo-bucket",
        )
        # Service-account private-key parsing is loop-independent; substitute it rather than generate real private keys.
        with captured_vertex_credentials():
            backend = await assemble_backend(
                provider_id=provider_id,
                media_type=media_type,
                model_id=model_id,
                resolver=ConfigResolver(db_factory),
            )
        assert backend is not None


class TestAssembleCustomEndToEnd:
    async def test_custom_text_provider_keeps_structured_output_capability(self, db_factory):
        from lib.custom_provider import make_provider_id
        from lib.custom_provider.backends import CustomTextBackend
        from lib.db.repositories.custom_provider_repo import CustomProviderRepository

        async with db_factory() as s:
            repo = CustomProviderRepository(s)
            provider = await repo.create_provider(
                display_name="Relay",
                discovery_format="openai",
                base_url="https://relay.test/v1",
                api_key="sk-relay",
                models=[
                    {
                        "model_id": "gpt-5",
                        "display_name": "gpt-5",
                        "endpoint": "openai-chat",
                        "is_enabled": True,
                    }
                ],
            )
            await s.commit()
            pid = make_provider_id(provider.id)

        with captured_openai_clients():
            result = await assemble_backend(
                provider_id=pid,
                media_type="text",
                model_id="gpt-5",
                resolver=ConfigResolver(db_factory),
            )

        assert isinstance(result, CustomTextBackend)
        assert TextCapability.STRUCTURED_OUTPUT in result.capabilities

    @patch("lib.custom_provider.endpoints.OpenAIImageBackend")
    async def test_custom_provider_delegates_to_loader(self, mock_cls, db_factory):
        from lib.custom_provider import make_provider_id
        from lib.custom_provider.backends import CustomImageBackend
        from lib.db.repositories.custom_provider_repo import CustomProviderRepository

        async with db_factory() as s:
            repo = CustomProviderRepository(s)
            provider = await repo.create_provider(
                display_name="Relay",
                discovery_format="openai",
                base_url="https://relay.test/v1",
                api_key="sk-relay",
                models=[
                    {
                        "model_id": "dall-e-3",
                        "display_name": "dall-e-3",
                        "endpoint": "openai-images",
                        "is_enabled": True,
                    }
                ],
            )
            await s.commit()
            pid = make_provider_id(provider.id)

        resolver = ConfigResolver(db_factory)
        result = await assemble_backend(provider_id=pid, media_type="image", model_id="dall-e-3", resolver=resolver)
        assert isinstance(result, CustomImageBackend)
        mock_cls.assert_called_once_with(
            api_key="sk-relay", base_url="https://relay.test/v1", model="dall-e-3", transport_layer=None
        )

    @patch("lib.custom_provider.endpoints.OpenAITextBackend")
    async def test_custom_text_provider_delegates_to_loader(self, mock_cls, db_factory):
        # Custom text/media share load_custom_backend and unified assembly seams.
        from lib.custom_provider import make_provider_id
        from lib.custom_provider.backends import CustomTextBackend
        from lib.db.repositories.custom_provider_repo import CustomProviderRepository

        async with db_factory() as s:
            repo = CustomProviderRepository(s)
            provider = await repo.create_provider(
                display_name="Relay",
                discovery_format="openai",
                base_url="https://relay.test/v1",
                api_key="sk-relay",
                models=[
                    {
                        "model_id": "gpt-5",
                        "display_name": "gpt-5",
                        "endpoint": "openai-chat",
                        "is_enabled": True,
                    }
                ],
            )
            await s.commit()
            pid = make_provider_id(provider.id)

        resolver = ConfigResolver(db_factory)
        result = await assemble_backend(provider_id=pid, media_type="text", model_id="gpt-5", resolver=resolver)
        assert isinstance(result, CustomTextBackend)

    @staticmethod
    async def _seed_relay_image_models(db_factory, models: list[dict]) -> str:
        from lib.custom_provider import make_provider_id
        from lib.db.repositories.custom_provider_repo import CustomProviderRepository

        async with db_factory() as s:
            provider = await CustomProviderRepository(s).create_provider(
                display_name="Relay",
                discovery_format="openai",
                base_url="https://relay.test/v1",
                api_key="sk-relay",
                models=[{"display_name": m["model_id"], "endpoint": "openai-images", **m} for m in models],
            )
            await s.commit()
            return make_provider_id(provider.id)

    @staticmethod
    def _retire_seedream_lite(monkeypatch) -> str:
        """Ark Seedream 5.0 lite retires 2026-11-24, succeeded by Seedream 5.0 pro; today is retirement day. Return entry references."""
        install_catalog(
            monkeypatch,
            with_entry_changes(
                factory_snapshot(),
                "ark",
                SEEDREAM_LITE,
                shutdown_date=date(2026, 11, 24),
                successor="bytedance/doubao-seedream-5-0-pro",
            ),
        )
        set_catalog_today(monkeypatch, date(2026, 11, 24))
        entry = catalog.provider_model_entry("ark", SEEDREAM_LITE, "image")
        assert entry is not None
        return entry.key.ref

    @patch("lib.custom_provider.endpoints.OpenAIImageBackend")
    async def test_explicit_discontinued_custom_model_is_rejected_without_falling_back(
        self, mock_cls, db_factory, monkeypatch
    ):
        """Explicit custom-model bindings to retired entries error with dates/successors, never default-fallback or construct."""
        ref = self._retire_seedream_lite(monkeypatch)
        pid = await self._seed_relay_image_models(
            db_factory,
            [
                {"model_id": "relay-seedream", "entry_binding": ref, "is_enabled": True},
                {"model_id": "dall-e-3", "is_enabled": True, "is_default": True},
            ],
        )
        with pytest.raises(ModelDiscontinuedError) as excinfo:
            await assemble_backend(
                provider_id=pid, media_type="image", model_id="relay-seedream", resolver=ConfigResolver(db_factory)
            )
        assert excinfo.value.params == {
            "provider": pid,
            "model": "relay-seedream",
            "shutdown_date": "2026-11-24",
            "successor": "bytedance/doubao-seedream-5-0-pro",
        }
        mock_cls.assert_not_called()

    @patch("lib.custom_provider.endpoints.OpenAIImageBackend")
    async def test_disabled_discontinued_custom_model_is_still_rejected(self, mock_cls, db_factory, monkeypatch):
        """Disabled models normally default-fallback; retired entries still error so users know to change models."""
        self._retire_seedream_lite(monkeypatch)
        pid = await self._seed_relay_image_models(
            db_factory,
            [
                {"model_id": SEEDREAM_LITE, "is_enabled": False},
                {"model_id": "dall-e-3", "is_enabled": True, "is_default": True},
            ],
        )
        with pytest.raises(ModelDiscontinuedError):
            await assemble_backend(
                provider_id=pid, media_type="image", model_id=SEEDREAM_LITE, resolver=ConfigResolver(db_factory)
            )
        mock_cls.assert_not_called()

    @patch("lib.custom_provider.endpoints.OpenAIImageBackend")
    async def test_default_resolution_skips_a_discontinued_custom_default(self, mock_cls, db_factory, monkeypatch):
        """Custom providers whose sole default matches retired entries by name error as no default when model is omitted, never constructing it."""
        self._retire_seedream_lite(monkeypatch)
        pid = await self._seed_relay_image_models(
            db_factory, [{"model_id": SEEDREAM_LITE, "is_enabled": True, "is_default": True}]
        )
        with pytest.raises(ValueError, match="没有默认 t2i image 模型"):
            await assemble_backend(
                provider_id=pid,
                media_type="image",
                model_id=None,
                resolver=ConfigResolver(db_factory),
                generation_type="t2i",
            )
        mock_cls.assert_not_called()


class TestOutputLimitFacts:
    async def test_builtin_text_model_reads_registry(self, db_factory):
        facts = await output_limit_facts(
            provider_id="dashscope", model_id="qwen-long", resolver=ConfigResolver(db_factory)
        )

        assert facts == OutputLimitFacts(registered_max_output_tokens=8192, custom_model=False)

    async def test_unknown_builtin_model_is_unregistered(self, db_factory):
        facts = await output_limit_facts(
            provider_id="gemini-aistudio", model_id="retired-model", resolver=ConfigResolver(db_factory)
        )

        assert facts == OutputLimitFacts(registered_max_output_tokens=None, custom_model=False)

    @pytest.mark.parametrize(("registered", "expected"), [(8192, 8192), (None, None)])
    async def test_custom_model_reads_its_entry(self, db_factory, registered, expected):
        from lib.custom_provider import make_provider_id
        from lib.db.repositories.custom_provider_repo import CustomProviderRepository

        async with db_factory() as s:
            provider = await CustomProviderRepository(s).create_provider(
                display_name="Relay",
                discovery_format="openai",
                base_url="https://relay.test/v1",
                api_key="sk-relay",
                models=[
                    {
                        "model_id": "my-llm",
                        "display_name": "my-llm",
                        "endpoint": "openai-chat",
                        "is_enabled": True,
                        "capability_overrides": None if registered is None else {"max_output_tokens": registered},
                    }
                ],
            )
            await s.commit()

        facts = await output_limit_facts(
            provider_id=make_provider_id(provider.id), model_id="my-llm", resolver=ConfigResolver(db_factory)
        )

        assert facts == OutputLimitFacts(registered_max_output_tokens=expected, custom_model=True)
