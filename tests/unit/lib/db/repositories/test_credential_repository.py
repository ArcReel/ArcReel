"""ProviderCredential Repository 测试。"""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from lib.db.repositories.credential_repository import CredentialRepository


class TestCredentialRepository:
    async def test_create_and_list(self, db_session: AsyncSession):
        repo = CredentialRepository(db_session)
        cred = await repo.create(provider="gemini-aistudio", name="测试Key", api_key="AIza-test")
        await db_session.flush()
        creds = await repo.list_by_provider("gemini-aistudio")
        assert len(creds) == 1
        assert creds[0].name == "测试Key"
        assert creds[0].api_key == "AIza-test"
        assert creds[0].id == cred.id

    async def test_first_credential_is_active(self, db_session: AsyncSession):
        repo = CredentialRepository(db_session)
        cred = await repo.create(provider="gemini-aistudio", name="第一个", api_key="AIza-1")
        await db_session.flush()
        assert cred.is_active is True

    async def test_second_credential_is_not_active(self, db_session: AsyncSession):
        repo = CredentialRepository(db_session)
        await repo.create(provider="gemini-aistudio", name="第一个", api_key="AIza-1")
        cred2 = await repo.create(provider="gemini-aistudio", name="第二个", api_key="AIza-2")
        await db_session.flush()
        assert cred2.is_active is False

    async def test_activate(self, db_session: AsyncSession):
        repo = CredentialRepository(db_session)
        c1 = await repo.create(provider="gemini-aistudio", name="第一个", api_key="AIza-1")
        c2 = await repo.create(provider="gemini-aistudio", name="第二个", api_key="AIza-2")
        await db_session.flush()

        await repo.activate(c2.id, "gemini-aistudio")
        await db_session.flush()

        creds = await repo.list_by_provider("gemini-aistudio")
        active_map = {c.id: c.is_active for c in creds}
        assert active_map[c1.id] is False
        assert active_map[c2.id] is True

    async def test_get_active(self, db_session: AsyncSession):
        repo = CredentialRepository(db_session)
        await repo.create(provider="gemini-aistudio", name="Key1", api_key="AIza-1")
        await db_session.flush()
        active = await repo.get_active("gemini-aistudio")
        assert active is not None
        assert active.name == "Key1"

    async def test_get_active_returns_none_when_empty(self, db_session: AsyncSession):
        repo = CredentialRepository(db_session)
        active = await repo.get_active("gemini-aistudio")
        assert active is None

    async def test_get_by_id(self, db_session: AsyncSession):
        repo = CredentialRepository(db_session)
        c = await repo.create(provider="gemini-aistudio", name="Key1", api_key="AIza-1")
        await db_session.flush()
        found = await repo.get_by_id(c.id)
        assert found is not None
        assert found.name == "Key1"

    async def test_update(self, db_session: AsyncSession):
        repo = CredentialRepository(db_session)
        c = await repo.create(provider="gemini-aistudio", name="旧名", api_key="AIza-old")
        await db_session.flush()
        await repo.update(c.id, name="新名", api_key="AIza-new")
        await db_session.flush()
        updated = await repo.get_by_id(c.id)
        assert updated is not None
        assert updated.name == "新名"
        assert updated.api_key == "AIza-new"

    async def test_delete(self, db_session: AsyncSession):
        repo = CredentialRepository(db_session)
        c = await repo.create(provider="gemini-aistudio", name="Key1", api_key="AIza-1")
        await db_session.flush()
        await repo.delete(c.id)
        await db_session.flush()
        assert await repo.get_by_id(c.id) is None

    async def test_delete_active_promotes_oldest(self, db_session: AsyncSession):
        repo = CredentialRepository(db_session)
        c1 = await repo.create(provider="gemini-aistudio", name="Key1", api_key="AIza-1")
        await repo.create(provider="gemini-aistudio", name="Key2", api_key="AIza-2")
        await db_session.flush()
        await repo.delete(c1.id)
        await db_session.flush()
        remaining = await repo.list_by_provider("gemini-aistudio")
        assert len(remaining) == 1
        assert remaining[0].is_active is True

    async def test_has_active_credential(self, db_session: AsyncSession):
        repo = CredentialRepository(db_session)
        assert await repo.has_active_credential("gemini-aistudio") is False
        await repo.create(provider="gemini-aistudio", name="Key1", api_key="AIza-1")
        await db_session.flush()
        assert await repo.has_active_credential("gemini-aistudio") is True

    async def test_get_active_credentials_bulk(self, db_session: AsyncSession):
        repo = CredentialRepository(db_session)
        await repo.create(provider="gemini-aistudio", name="K1", api_key="AIza-1")
        await repo.create(provider="ark", name="K2", api_key="ark-key")
        await db_session.flush()
        bulk = await repo.get_active_credentials_bulk()
        assert "gemini-aistudio" in bulk
        assert "ark" in bulk

    async def test_create_and_update_two_secrets(self, db_session: AsyncSession):
        repo = CredentialRepository(db_session)
        c = await repo.create(
            provider="kling",
            name="可灵账号",
            access_key="AK-original",
            secret_key="SK-original",
        )
        await db_session.flush()
        assert c.access_key == "AK-original"
        assert c.secret_key == "SK-original"
        assert c.api_key is None

        await repo.update(c.id, access_key="AK-new")
        await db_session.flush()
        updated = await repo.get_by_id(c.id)
        assert updated is not None
        # 只更新传入的 secret，另一个保持原值
        assert updated.access_key == "AK-new"
        assert updated.secret_key == "SK-original"

    async def test_overlay_config_emits_both_secrets_by_key_name(self, db_session: AsyncSession):
        repo = CredentialRepository(db_session)
        c = await repo.create(
            provider="kling",
            name="可灵账号",
            access_key="AK-1",
            secret_key="SK-1",
        )
        await db_session.flush()
        config: dict[str, str] = {}
        c.overlay_config(config)
        # 列名即 config key，逐字段原样产出
        assert config["access_key"] == "AK-1"
        assert config["secret_key"] == "SK-1"
        assert "api_key" not in config

    async def test_update_can_explicitly_clear_secret_fields(self, db_session: AsyncSession):
        """显式传 None 清空 api_key/access_key/secret_key/base_url；省略参数（默认）不动它们。"""
        repo = CredentialRepository(db_session)
        c = await repo.create(
            provider="kling",
            name="可灵账号",
            api_key="AK-legacy",
            base_url="https://proxy.example.com/v1",
        )
        await db_session.flush()

        # 省略 access_key/secret_key：保持未设置，不受影响
        await repo.update(c.id, name="改名")
        await db_session.flush()
        untouched = await repo.get_by_id(c.id)
        assert untouched is not None
        assert untouched.api_key == "AK-legacy"
        assert untouched.name == "改名"

        # 显式传 None：清空该字段
        await repo.update(c.id, api_key=None, base_url=None, access_key="AK-new", secret_key="SK-new")
        await db_session.flush()
        switched = await repo.get_by_id(c.id)
        assert switched is not None
        assert switched.api_key is None
        assert switched.base_url is None
        assert switched.access_key == "AK-new"
        assert switched.secret_key == "SK-new"

    async def test_base_url_normalized_on_create(self, db_session: AsyncSession):
        repo = CredentialRepository(db_session)
        c = await repo.create(
            provider="gemini-aistudio",
            name="Key",
            api_key="AIza-1",
            base_url="https://proxy.example.com/v1",
        )
        await db_session.flush()
        assert c.base_url == "https://proxy.example.com/v1/"
