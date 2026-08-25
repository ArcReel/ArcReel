"""AgentCredentialRepository 单元测试。"""

from __future__ import annotations

import pytest

from lib.db.repositories.agent_credential_repo import AgentCredentialRepository

pytestmark = pytest.mark.integration


@pytest.mark.asyncio
async def test_create_and_get(async_session) -> None:
    repo = AgentCredentialRepository(async_session)
    cred = await repo.create(
        preset_id="deepseek",
        display_name="My DeepSeek",
        base_url="https://api.deepseek.com/anthropic",
        api_key="sk-test",
        model="deepseek-chat",
    )
    await async_session.flush()
    fetched = await repo.get(cred.id)
    assert fetched is not None
    assert fetched.preset_id == "deepseek"
    assert fetched.api_key == "sk-test"
    assert fetched.is_active is False


@pytest.mark.asyncio
async def test_list_orders_by_id(async_session) -> None:
    repo = AgentCredentialRepository(async_session)
    a = await repo.create(preset_id="deepseek", display_name="A", base_url="u", api_key="k1")
    b = await repo.create(preset_id="kimi", display_name="B", base_url="u", api_key="k2")
    await async_session.flush()
    items = await repo.list_for_user()
    ids = [c.id for c in items]
    assert ids == [a.id, b.id]


@pytest.mark.asyncio
async def test_set_active_makes_others_inactive(async_session) -> None:
    repo = AgentCredentialRepository(async_session)
    a = await repo.create(preset_id="x", display_name="A", base_url="u", api_key="k1")
    b = await repo.create(preset_id="y", display_name="B", base_url="u", api_key="k2")
    await async_session.flush()
    await repo.set_active(a.id)
    await async_session.flush()
    active = await repo.get_active()
    assert active is not None and active.id == a.id

    await repo.set_active(b.id)
    await async_session.flush()
    active = await repo.get_active()
    assert active is not None and active.id == b.id

    a_after = await repo.get(a.id)
    assert a_after is not None and a_after.is_active is False


@pytest.mark.asyncio
async def test_delete_active_raises(async_session) -> None:
    repo = AgentCredentialRepository(async_session)
    a = await repo.create(preset_id="x", display_name="A", base_url="u", api_key="k")
    await async_session.flush()
    await repo.set_active(a.id)
    await async_session.flush()
    with pytest.raises(ValueError, match="active"):
        await repo.delete(a.id)


@pytest.mark.asyncio
async def test_delete_inactive_works(async_session) -> None:
    repo = AgentCredentialRepository(async_session)
    a = await repo.create(preset_id="x", display_name="A", base_url="u", api_key="k")
    await async_session.flush()
    assert await repo.delete(a.id) is True
    await async_session.flush()
    assert await repo.get(a.id) is None


@pytest.mark.asyncio
async def test_delete_nonexistent_returns_false(async_session) -> None:
    repo = AgentCredentialRepository(async_session)
    assert await repo.delete(9999) is False


@pytest.mark.asyncio
async def test_set_active_unknown_id_raises(async_session) -> None:
    repo = AgentCredentialRepository(async_session)
    with pytest.raises(ValueError, match="not found"):
        await repo.set_active(9999)


@pytest.mark.asyncio
async def test_update_partial(async_session) -> None:
    repo = AgentCredentialRepository(async_session)
    a = await repo.create(preset_id="x", display_name="A", base_url="u", api_key="k")
    await async_session.flush()
    updated = await repo.update(a.id, display_name="A2", model="m1")
    assert updated is not None
    assert updated.display_name == "A2"
    assert updated.model == "m1"
    assert updated.api_key == "k"  # 未传不动


@pytest.mark.asyncio
async def test_get_active_when_none(async_session) -> None:
    repo = AgentCredentialRepository(async_session)
    assert await repo.get_active() is None


@pytest.mark.asyncio
async def test_account_scoped_operations_cannot_access_another_users_credential(async_session) -> None:
    repo = AgentCredentialRepository(async_session)
    credential = await repo.create(
        preset_id="deepseek",
        display_name="User A",
        base_url="https://api.deepseek.com/anthropic",
        api_key="secret-a",
        user_id="user-a",
    )
    await async_session.flush()

    assert await repo.get(credential.id, "user-b") is None
    assert await repo.update(credential.id, user_id="user-b", display_name="stolen") is None
    assert await repo.delete(credential.id, "user-b") is False
    with pytest.raises(ValueError, match="not found"):
        await repo.set_active(credential.id, "user-b")

    unchanged = await repo.get(credential.id, "user-a")
    assert unchanged is not None
    assert unchanged.display_name == "User A"
