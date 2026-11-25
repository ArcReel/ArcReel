"""load_builtin_config 单测：内存 SQLite + 真 ConfigResolver。"""

from __future__ import annotations

from lib.backends.backend_assembly.assembler import load_builtin_config
from lib.config.resolver import ConfigResolver
from lib.config.service import ConfigService


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
        config = await load_builtin_config(resolver, "ark", rate_limiter=None)
        assert config.credentials.get("api_key") == "ark-secret"
        assert config.credentials.get("base_url") == "https://relay.test/api/v3"
        assert config.provider_meta is not None
        assert config.provider_meta.default_base_url == "https://ark.cn-beijing.volces.com/api/v3"

    async def test_active_credential_record_id_enters_envelope(self, db_factory):
        """生效凭证记录的 ID 随凭证进信封；凭证只来自供应商配置时没有记录 ID。"""
        from lib.db.repositories.credential_repository import CredentialRepository

        await _seed_provider_config(db_factory, "ark", api_key="TEST-config-key")
        resolver = ConfigResolver(db_factory)
        assert (await load_builtin_config(resolver, "ark", rate_limiter=None)).credential_id is None

        async with db_factory() as session:
            cred = await CredentialRepository(session).create(provider="ark", name="主 Key", api_key="TEST-ark-key")
            await session.commit()
        config = await load_builtin_config(resolver, "ark", rate_limiter=None)
        assert config.credentials.get("api_key") == "TEST-ark-key"
        assert config.credential_id == cred.id

    async def test_rate_limiter_carried_into_envelope(self, db_factory):
        sentinel = object()
        resolver = ConfigResolver(db_factory)
        config = await load_builtin_config(resolver, "grok", rate_limiter=sentinel)
        assert config.rate_limiter is sentinel
