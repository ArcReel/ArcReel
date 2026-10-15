"""AgentCredentialRepository 单元测试。"""

from __future__ import annotations

import pytest

from lib.db.repositories.agent_credential_repo import AgentCredentialRepository, AgentModelPricing


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
    assert active is not None
    assert active.id == a.id

    await repo.set_active(b.id)
    await async_session.flush()
    active = await repo.get_active()
    assert active is not None
    assert active.id == b.id

    a_after = await repo.get(a.id)
    assert a_after is not None
    assert a_after.is_active is False


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


class TestResolvePricing:
    """记账时的计价依据：会话凭证的倍率，加上模型名所在档位的条目绑定。"""

    async def test_takes_the_credential_multiplier_and_the_binding_of_the_tier_the_model_is_in(
        self, async_session
    ) -> None:
        repo = AgentCredentialRepository(async_session)
        cred = await repo.create(
            preset_id="__custom__",
            display_name="网关",
            base_url="u",
            api_key="k",
            model="gw-main",
            haiku_model="GW/Fast.1",
            multiplier=0.5,
        )
        await repo.update(cred.id, tier_bindings={"haiku": "acme/fast-1", "opus": "acme/big"})

        # 模型名比较忽略大小写与命名空间；留空的 opus 档位填成主模型，主模型档位自己没有绑定时取 opus 的绑定
        assert await repo.resolve_pricing(cred.id, "gw/fast-1") == AgentModelPricing("acme/fast-1", 0.5)
        assert await repo.resolve_pricing(cred.id, "gw-main") == AgentModelPricing("acme/big", 0.5)
        assert await repo.resolve_pricing(cred.id, "other") == AgentModelPricing(None, 0.5)

    async def test_falls_back_to_defaults_without_a_credential(self, async_session) -> None:
        repo = AgentCredentialRepository(async_session)

        assert await repo.resolve_pricing(None, "m") == AgentModelPricing()
        assert await repo.resolve_pricing(404, "m") == AgentModelPricing()
