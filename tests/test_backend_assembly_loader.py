"""assemble_backend 内置（简单族）async 装载段单测：内存 SQLite + 真 ConfigResolver。

镜像 test_config_resolver.py 的内存 DB 范式：不 mock resolver，断言凭证 overlay 真进 LoadedConfig
信封、端到端经 assemble_backend 造出简单族 backend、未登记 provider × media fail-loud。
"""

from __future__ import annotations

from unittest.mock import patch

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from lib.backend_assembly import assemble_backend
from lib.backend_assembly.assembler import _load_builtin_config
from lib.config.resolver import ConfigResolver
from lib.config.service import ConfigService
from lib.db.base import Base


@pytest.fixture()
async def session_factory():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    yield factory
    await engine.dispose()


async def _seed_provider_config(factory, provider: str, **kv: str) -> None:
    async with factory() as session:
        svc = ConfigService(session)
        for key, value in kv.items():
            await svc.set_provider_config(provider, key, value)
        await session.commit()


class TestLoadBuiltinConfig:
    async def test_credential_overlay_enters_envelope(self, session_factory):
        await _seed_provider_config(session_factory, "ark", api_key="ark-secret", base_url="https://relay.test/api/v3")
        resolver = ConfigResolver(session_factory)
        config = await _load_builtin_config(resolver, "ark", rate_limiter=None)
        assert config.credentials.get("api_key") == "ark-secret"
        assert config.credentials.get("base_url") == "https://relay.test/api/v3"
        # registry meta 进信封：用于 default_base_url 回退
        assert config.provider_meta is not None
        assert config.provider_meta.default_base_url == "https://ark.cn-beijing.volces.com/api/v3"

    async def test_rate_limiter_carried_into_envelope(self, session_factory):
        sentinel = object()
        resolver = ConfigResolver(session_factory)
        config = await _load_builtin_config(resolver, "grok", rate_limiter=sentinel)
        assert config.rate_limiter is sentinel


class TestAssembleBuiltinEndToEnd:
    @patch("lib.image_backends.registry.create_backend")
    async def test_simple_image_end_to_end(self, mock_create, session_factory):
        await _seed_provider_config(session_factory, "ark", api_key="ark-secret")
        resolver = ConfigResolver(session_factory)
        await assemble_backend(provider_id="ark", media_type="image", model_id="doubao-x", resolver=resolver)
        # 用户未配 base_url → 回落 registry default；凭证 overlay 经装载真进构造参数
        mock_create.assert_called_once_with(
            "ark", api_key="ark-secret", model="doubao-x", base_url="https://ark.cn-beijing.volces.com/api/v3"
        )

    async def test_unknown_provider_media_fails_loud(self, session_factory):
        resolver = ConfigResolver(session_factory)
        with pytest.raises(ValueError, match="no builtin ProviderSpec"):
            await assemble_backend(provider_id="ark", media_type="audio", model_id="x", resolver=resolver)

    @patch("lib.image_backends.registry.create_backend")
    async def test_gemini_aistudio_image_end_to_end(self, mock_create, session_factory):
        # gemini 特例族：provider_id 直接决定 backend_type，凭证 overlay + 共享 rate_limiter 经装载进闭包
        await _seed_provider_config(
            session_factory, "gemini-aistudio", api_key="g-secret", base_url="https://g.relay.test"
        )
        sentinel = object()
        resolver = ConfigResolver(session_factory)
        await assemble_backend(
            provider_id="gemini-aistudio",
            media_type="image",
            model_id="gemini-3.1-flash-image-preview",
            resolver=resolver,
            rate_limiter=sentinel,
        )
        mock_create.assert_called_once_with(
            "gemini",
            backend_type="aistudio",
            api_key="g-secret",
            base_url="https://g.relay.test",
            rate_limiter=sentinel,
            image_model="gemini-3.1-flash-image-preview",
        )

    @patch("lib.image_backends.registry.create_backend")
    async def test_kling_image_api_model_name_decoupled_end_to_end(self, mock_create, session_factory):
        # kling 特例族：双 secret overlay 真进闭包；api_model_name 解耦从 registry models 读到（别名键）
        await _seed_provider_config(session_factory, "kling", access_key="ak-1", secret_key="sk-1")
        resolver = ConfigResolver(session_factory)
        await assemble_backend(
            provider_id="kling", media_type="image", model_id="kling-v3-omni-image", resolver=resolver
        )
        mock_create.assert_called_once_with(
            "kling",
            auth_mode="jwt",
            access_key="ak-1",
            secret_key="sk-1",
            model="kling-v3-omni-image",
            api_model_name="kling-v3-omni",
            base_url="https://api.klingai.com/v1",
        )


class TestAssembleCustomEndToEnd:
    @patch("lib.custom_provider.endpoints.OpenAIImageBackend")
    async def test_custom_provider_delegates_to_loader(self, mock_cls, session_factory):
        from lib.custom_provider import make_provider_id
        from lib.custom_provider.backends import CustomImageBackend
        from lib.db.repositories.custom_provider_repo import CustomProviderRepository

        async with session_factory() as s:
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

        resolver = ConfigResolver(session_factory)
        result = await assemble_backend(provider_id=pid, media_type="image", model_id="dall-e-3", resolver=resolver)
        assert isinstance(result, CustomImageBackend)
        mock_cls.assert_called_once_with(api_key="sk-relay", base_url="https://relay.test/v1", model="dall-e-3")
