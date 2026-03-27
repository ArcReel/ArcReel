"""ProviderCredential Repository 测试。"""

from __future__ import annotations

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from lib.db.base import Base
from lib.db.repositories.credential_repository import CredentialRepository


@pytest.fixture
async def session():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    async_session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with async_session() as s:
        yield s
    await engine.dispose()


class TestCredentialRepository:
    async def test_create_and_list(self, session: AsyncSession):
        repo = CredentialRepository(session)
        cred = await repo.create(provider="gemini-aistudio", name="测试Key", api_key="AIza-test")
        await session.flush()
        creds = await repo.list_by_provider("gemini-aistudio")
        assert len(creds) == 1
        assert creds[0].name == "测试Key"
        assert creds[0].api_key == "AIza-test"
        assert creds[0].id == cred.id

    async def test_first_credential_is_active(self, session: AsyncSession):
        repo = CredentialRepository(session)
        cred = await repo.create(provider="gemini-aistudio", name="第一个", api_key="AIza-1")
        await session.flush()
        assert cred.is_active is True

    async def test_second_credential_is_not_active(self, session: AsyncSession):
        repo = CredentialRepository(session)
        await repo.create(provider="gemini-aistudio", name="第一个", api_key="AIza-1")
        cred2 = await repo.create(provider="gemini-aistudio", name="第二个", api_key="AIza-2")
        await session.flush()
        assert cred2.is_active is False

    async def test_activate(self, session: AsyncSession):
        repo = CredentialRepository(session)
        c1 = await repo.create(provider="gemini-aistudio", name="第一个", api_key="AIza-1")
        c2 = await repo.create(provider="gemini-aistudio", name="第二个", api_key="AIza-2")
        await session.flush()

        await repo.activate(c2.id, "gemini-aistudio")
        await session.flush()

        creds = await repo.list_by_provider("gemini-aistudio")
        active_map = {c.id: c.is_active for c in creds}
        assert active_map[c1.id] is False
        assert active_map[c2.id] is True

    async def test_get_active(self, session: AsyncSession):
        repo = CredentialRepository(session)
        await repo.create(provider="gemini-aistudio", name="Key1", api_key="AIza-1")
        await session.flush()
        active = await repo.get_active("gemini-aistudio")
        assert active is not None
        assert active.name == "Key1"

    async def test_get_active_returns_none_when_empty(self, session: AsyncSession):
        repo = CredentialRepository(session)
        active = await repo.get_active("gemini-aistudio")
        assert active is None

    async def test_get_by_id(self, session: AsyncSession):
        repo = CredentialRepository(session)
        c = await repo.create(provider="gemini-aistudio", name="Key1", api_key="AIza-1")
        await session.flush()
        found = await repo.get_by_id(c.id)
        assert found is not None
        assert found.name == "Key1"

    async def test_update(self, session: AsyncSession):
        repo = CredentialRepository(session)
        c = await repo.create(provider="gemini-aistudio", name="旧名", api_key="AIza-old")
        await session.flush()
        await repo.update(c.id, name="新名", api_key="AIza-new")
        await session.flush()
        updated = await repo.get_by_id(c.id)
        assert updated is not None
        assert updated.name == "新名"
        assert updated.api_key == "AIza-new"

    async def test_delete(self, session: AsyncSession):
        repo = CredentialRepository(session)
        c = await repo.create(provider="gemini-aistudio", name="Key1", api_key="AIza-1")
        await session.flush()
        await repo.delete(c.id)
        await session.flush()
        assert await repo.get_by_id(c.id) is None

    async def test_delete_active_promotes_oldest(self, session: AsyncSession):
        repo = CredentialRepository(session)
        c1 = await repo.create(provider="gemini-aistudio", name="Key1", api_key="AIza-1")
        await repo.create(provider="gemini-aistudio", name="Key2", api_key="AIza-2")
        await session.flush()
        await repo.delete(c1.id)
        await session.flush()
        remaining = await repo.list_by_provider("gemini-aistudio")
        assert len(remaining) == 1
        assert remaining[0].is_active is True

    async def test_has_active_credential(self, session: AsyncSession):
        repo = CredentialRepository(session)
        assert await repo.has_active_credential("gemini-aistudio") is False
        await repo.create(provider="gemini-aistudio", name="Key1", api_key="AIza-1")
        await session.flush()
        assert await repo.has_active_credential("gemini-aistudio") is True

    async def test_get_active_credentials_bulk(self, session: AsyncSession):
        repo = CredentialRepository(session)
        await repo.create(provider="gemini-aistudio", name="K1", api_key="AIza-1")
        await repo.create(provider="ark", name="K2", api_key="ark-key")
        await session.flush()
        bulk = await repo.get_active_credentials_bulk()
        assert "gemini-aistudio" in bulk
        assert "ark" in bulk

    async def test_base_url_normalized_on_create(self, session: AsyncSession):
        repo = CredentialRepository(session)
        c = await repo.create(
            provider="gemini-aistudio", name="Key", api_key="AIza-1",
            base_url="https://proxy.example.com/v1",
        )
        await session.flush()
        assert c.base_url == "https://proxy.example.com/v1/"
