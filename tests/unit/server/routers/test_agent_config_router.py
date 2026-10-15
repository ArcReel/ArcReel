"""Agent config 路由测试。"""

from __future__ import annotations

import pytest
import pytest_asyncio
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from lib.db import get_async_session
from lib.db.repositories.agent_credential_repo import AgentCredentialRepository
from lib.db.repositories.custom_provider_repo import CustomProviderRepository
from server.auth import CurrentUserInfo, get_current_user
from server.routers import agent_config
from tests.auth_deps import AUTH_DEPENDENCIES


def _make_app(session_factory) -> FastAPI:
    app = FastAPI()

    async def override_session():
        async with session_factory() as session:
            yield session
            await session.commit()

    app.dependency_overrides[get_async_session] = override_session
    # 认证依赖挂在注册处，mini app 须与 server/app.py 的挂法一致，否则测不到真实的鉴权行为。
    app.include_router(agent_config.router, prefix="/api/v1", dependencies=AUTH_DEPENDENCIES)
    return app


@pytest_asyncio.fixture
async def agent_config_client(db_factory):
    app = _make_app(db_factory)
    app.dependency_overrides[get_current_user] = lambda: CurrentUserInfo(id="default", sub="testuser", role="admin")
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client


@pytest_asyncio.fixture
async def unauth_client(db_factory, monkeypatch):
    """No dependency override → real auth applies → expects 401/403."""
    # AUTH_ENABLED=false 时 get_current_user 直接返回匿名 admin，本 fixture 就测不到拒绝。
    monkeypatch.setenv("AUTH_ENABLED", "true")
    app = _make_app(db_factory)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client


# ── Task 9: GET /agent/preset-providers ───────────────────────────


@pytest.mark.asyncio
async def test_list_preset_providers_returns_catalog(agent_config_client) -> None:
    resp = await agent_config_client.get("/api/v1/agent/preset-providers")
    assert resp.status_code == 200
    data = resp.json()
    assert "providers" in data
    assert "custom_sentinel_id" in data
    assert data["custom_sentinel_id"] == "__custom__"
    ids = [p["id"] for p in data["providers"]]
    assert "deepseek" in ids
    assert "anthropic-official" in ids
    deepseek = next(p for p in data["providers"] if p["id"] == "deepseek")
    assert deepseek["messages_url"] == "https://api.deepseek.com/anthropic"
    assert deepseek["discovery_url"] == "https://api.deepseek.com"
    assert deepseek["default_model"] == "deepseek-v4-pro"
    assert deepseek["icon_key"] == "DeepSeek"
    arcreel = next(p for p in data["providers"] if p["id"] == "arcreel")
    assert arcreel["is_recommended"] is True
    assert arcreel["api_key_url"] == "https://api.arc-reel.com/"


@pytest.mark.asyncio
async def test_list_preset_providers_requires_auth(unauth_client) -> None:
    resp = await unauth_client.get("/api/v1/agent/preset-providers")
    assert resp.status_code in (401, 403)


# ── Task 10: /agent/credentials CRUD ─────────────────────────────


@pytest.mark.asyncio
async def test_list_credentials_initially_empty(agent_config_client) -> None:
    resp = await agent_config_client.get("/api/v1/agent/credentials")
    assert resp.status_code == 200
    assert resp.json() == {"credentials": []}


@pytest.mark.asyncio
async def test_create_with_preset(agent_config_client) -> None:
    body = {"preset_id": "deepseek", "api_key": "sk-testkey12345"}
    resp = await agent_config_client.post("/api/v1/agent/credentials", json=body)
    assert resp.status_code == 201
    cred = resp.json()
    assert cred["preset_id"] == "deepseek"
    assert cred["base_url"] == "https://api.deepseek.com/anthropic"
    assert cred["model"] == "deepseek-v4-pro"
    assert cred["display_name"] == "DeepSeek"
    assert cred["api_key_masked"].startswith("sk-")
    assert cred["icon_key"] == "DeepSeek"
    # 第一条凭证应自动 active
    assert cred["is_active"] is True


@pytest.mark.asyncio
async def test_create_custom_requires_base_url(agent_config_client) -> None:
    body = {"preset_id": "__custom__", "api_key": "sk"}
    resp = await agent_config_client.post("/api/v1/agent/credentials", json=body)
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_create_custom_with_base_url(agent_config_client) -> None:
    body = {
        "preset_id": "__custom__",
        "display_name": "My Proxy",
        "base_url": "https://proxy.example.com/anthropic",
        "api_key": "sk",
        "model": "claude-sonnet-4",
    }
    resp = await agent_config_client.post("/api/v1/agent/credentials", json=body)
    assert resp.status_code == 201
    assert resp.json()["base_url"] == "https://proxy.example.com/anthropic"
    assert resp.json()["icon_key"] is None


@pytest.mark.asyncio
async def test_create_normalizes_full_messages_endpoint(agent_config_client) -> None:
    """预设凭证覆盖 base_url 时，整条 messages 端点归一为调用根后入库。"""
    body = {
        "preset_id": "deepseek",
        "base_url": "https://proxy.internal/anthropic/v1/messages",
        "api_key": "sk",
    }
    resp = await agent_config_client.post("/api/v1/agent/credentials", json=body)
    assert resp.status_code == 201
    assert resp.json()["base_url"] == "https://proxy.internal/anthropic"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "base_url",
    [
        "https://relay.example.com/anthropic?api_key=sk-x",
        "https://x/a#frag",
        "https://u:p@x/a",
        "x.example.com",
    ],
)
async def test_create_rejects_unsupported_base_url(agent_config_client, base_url: str) -> None:
    """保存入口拒绝带 query / fragment / userinfo 或缺 scheme 的地址，文案不回显 query 值。"""
    resp = await agent_config_client.post(
        "/api/v1/agent/credentials",
        json={"preset_id": "__custom__", "base_url": base_url, "api_key": "sk"},
    )
    assert resp.status_code == 422
    assert "sk-x" not in resp.text


@pytest.mark.asyncio
async def test_update_normalizes_full_messages_endpoint(agent_config_client) -> None:
    """PATCH 的 base_url 与 POST 走同一归一：整条 messages 端点入库为调用根。"""
    cred = (
        await agent_config_client.post(
            "/api/v1/agent/credentials",
            json={"preset_id": "deepseek", "api_key": "sk"},
        )
    ).json()
    resp = await agent_config_client.patch(
        f"/api/v1/agent/credentials/{cred['id']}",
        json={"base_url": "https://proxy.internal/anthropic/v1/messages/"},
    )
    assert resp.status_code == 200
    assert resp.json()["base_url"] == "https://proxy.internal/anthropic"


@pytest.mark.asyncio
async def test_update_rejects_unsupported_base_url(agent_config_client) -> None:
    cred = (
        await agent_config_client.post(
            "/api/v1/agent/credentials",
            json={"preset_id": "deepseek", "api_key": "sk"},
        )
    ).json()
    resp = await agent_config_client.patch(
        f"/api/v1/agent/credentials/{cred['id']}",
        json={"base_url": "https://relay.example.com/anthropic?api_key=sk-x"},
    )
    assert resp.status_code == 422
    assert "sk-x" not in resp.text


@pytest.mark.asyncio
async def test_test_connection_rejects_unsupported_base_url(agent_config_client) -> None:
    """测试连接入口同样在探测前 422，不发任何出站请求。"""
    resp = await agent_config_client.post(
        "/api/v1/agent/test-connection",
        json={"preset_id": "__custom__", "api_key": "sk", "base_url": "https://x/a?api_key=sk-x"},
    )
    assert resp.status_code == 422
    assert "sk-x" not in resp.text


async def _seed_custom_provider(db_factory, *, base_url: str, api_key: str) -> int:
    async with db_factory() as session:
        provider = await CustomProviderRepository(session).create_provider(
            display_name="Relay",
            discovery_format="anthropic",
            base_url=base_url,
            api_key=api_key,
        )
        await session.commit()
        return provider.id


async def _stored_credential(db_factory, cred_id: int):
    async with db_factory() as session:
        cred = await AgentCredentialRepository(session).get(cred_id)
        assert cred is not None
        return cred


@pytest.mark.asyncio
async def test_create_from_custom_provider_copies_its_key_and_base_url(agent_config_client, db_factory) -> None:
    """从自定义供应商导入：服务端复制该供应商的 base_url 与 api_key，响应只含掩码。"""
    provider_id = await _seed_custom_provider(
        db_factory, base_url="https://relay.example.com/anthropic", api_key="sk-provider-secret"
    )
    resp = await agent_config_client.post(
        "/api/v1/agent/credentials",
        json={"preset_id": "__custom__", "from_custom_provider_id": provider_id},
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["base_url"] == "https://relay.example.com/anthropic"
    assert "sk-provider-secret" not in resp.text
    stored = await _stored_credential(db_factory, body["id"])
    assert stored.api_key == "sk-provider-secret"
    assert stored.base_url == "https://relay.example.com/anthropic"


@pytest.mark.asyncio
async def test_create_from_custom_provider_prefers_request_base_url(agent_config_client, db_factory) -> None:
    """请求显式给出 base_url 时以请求为准，密钥仍取自供应商。"""
    provider_id = await _seed_custom_provider(db_factory, base_url="https://relay.example.com", api_key="sk-p")
    resp = await agent_config_client.post(
        "/api/v1/agent/credentials",
        json={
            "preset_id": "__custom__",
            "from_custom_provider_id": provider_id,
            "base_url": "https://relay.example.com/anthropic/v1/messages",
        },
    )
    assert resp.status_code == 201
    stored = await _stored_credential(db_factory, resp.json()["id"])
    assert stored.base_url == "https://relay.example.com/anthropic"
    assert stored.api_key == "sk-p"


@pytest.mark.asyncio
async def test_create_from_custom_provider_rejects_explicit_api_key(agent_config_client, db_factory) -> None:
    provider_id = await _seed_custom_provider(db_factory, base_url="https://relay.example.com", api_key="sk-p")
    resp = await agent_config_client.post(
        "/api/v1/agent/credentials",
        json={"preset_id": "__custom__", "from_custom_provider_id": provider_id, "api_key": "sk-other"},
    )
    assert resp.status_code == 422
    listing = (await agent_config_client.get("/api/v1/agent/credentials")).json()
    assert listing == {"credentials": []}


@pytest.mark.asyncio
async def test_create_without_api_key_or_provider_rejected(agent_config_client) -> None:
    resp = await agent_config_client.post(
        "/api/v1/agent/credentials",
        json={"preset_id": "__custom__", "base_url": "https://relay.example.com"},
    )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_create_from_unknown_custom_provider_returns_404(agent_config_client) -> None:
    resp = await agent_config_client.post(
        "/api/v1/agent/credentials",
        json={"preset_id": "__custom__", "from_custom_provider_id": 99999},
    )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_create_from_custom_provider_without_key_rejected(agent_config_client, db_factory) -> None:
    """未配置密钥的供应商不能作为 Agent 凭据来源。"""
    provider_id = await _seed_custom_provider(db_factory, base_url="https://relay.example.com", api_key="")
    resp = await agent_config_client.post(
        "/api/v1/agent/credentials",
        json={"preset_id": "__custom__", "from_custom_provider_id": provider_id},
    )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_create_unknown_preset_rejected(agent_config_client) -> None:
    resp = await agent_config_client.post(
        "/api/v1/agent/credentials",
        json={"preset_id": "nonexistent", "api_key": "sk"},
    )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_patch_credential(agent_config_client) -> None:
    created = (
        await agent_config_client.post(
            "/api/v1/agent/credentials",
            json={"preset_id": "deepseek", "api_key": "sk1"},
        )
    ).json()
    cid = created["id"]
    resp = await agent_config_client.patch(
        f"/api/v1/agent/credentials/{cid}",
        json={"display_name": "Renamed", "api_key": "sk2"},
    )
    assert resp.status_code == 200
    assert resp.json()["display_name"] == "Renamed"


# ── 倍率与档位定价状态 ─────────────────────────────────────────


def _tiers(cred: dict) -> dict[str, dict]:
    return {tier["tier"]: {k: v for k, v in tier.items() if k != "tier"} for tier in cred["tiers"]}


async def test_saved_credential_reports_multiplier_and_the_pricing_status_of_each_tier(agent_config_client) -> None:
    resp = await agent_config_client.post(
        "/api/v1/agent/credentials",
        json={"preset_id": "deepseek", "api_key": "sk", "haiku_model": "my-private-llm", "multiplier": 0},
    )
    assert resp.status_code == 201

    (cred,) = (await agent_config_client.get("/api/v1/agent/credentials")).json()["credentials"]
    assert cred["multiplier"] == 0
    main = {
        "model_name": "deepseek-v4-pro",
        "entry_display_name": "DeepSeek V4 Pro",
        "manually_bound": False,
        "binding_stale": False,
        "priced": True,
    }
    # 非官方预设留空的档位用主模型；对应不到条目的档位未定价
    assert _tiers(cred) == {
        "model": main,
        "haiku": {
            "model_name": "my-private-llm",
            "entry_display_name": None,
            "manually_bound": False,
            "binding_stale": False,
            "priced": False,
        },
        "sonnet": main,
        "opus": main,
        "subagent": main,
    }


async def test_official_preset_leaves_blank_tiers_to_the_cli(agent_config_client) -> None:
    resp = await agent_config_client.post(
        "/api/v1/agent/credentials",
        json={"preset_id": "anthropic-official", "api_key": "sk", "model": "claude-sonnet-4-6"},
    )

    tiers = _tiers(resp.json())
    assert tiers["model"]["entry_display_name"] == "Claude Sonnet 4.6"
    assert tiers["haiku"] == {
        "model_name": None,
        "entry_display_name": None,
        "manually_bound": False,
        "binding_stale": False,
        "priced": False,
    }


async def test_a_tier_binding_takes_precedence_over_the_model_name(agent_config_client, db_factory) -> None:
    created = (
        await agent_config_client.post(
            "/api/v1/agent/credentials",
            json={"preset_id": "__custom__", "base_url": "https://gw.example.com", "api_key": "sk", "model": "gw-llm"},
        )
    ).json()
    async with db_factory() as session:
        # 指向已不在目录里的条目的绑定不生效，回到按模型名对应
        await AgentCredentialRepository(session).update(
            created["id"], tier_bindings={"model": "anthropic/claude-haiku-4-5", "opus": "acme/gone"}
        )
        await session.commit()

    (cred,) = (await agent_config_client.get("/api/v1/agent/credentials")).json()["credentials"]
    tiers = _tiers(cred)
    assert tiers["model"] == {
        "model_name": "gw-llm",
        "entry_display_name": "Claude Haiku 4.5",
        "manually_bound": True,
        "binding_stale": False,
        "priced": True,
    }
    # 失效绑定回到按模型名对应，但绑定还存着，状态里标出来让界面能解除
    assert tiers["opus"]["manually_bound"] is False
    assert tiers["opus"]["binding_stale"] is True


# ── 档位手动绑定 ──────────────────────────────────────────────


async def _create_gateway_credential(client, **extra) -> dict:
    resp = await client.post(
        "/api/v1/agent/credentials",
        json={
            "preset_id": "__custom__",
            "base_url": "https://gw.example.com",
            "api_key": "sk",
            "model": "gw-llm",
            **extra,
        },
    )
    assert resp.status_code == 201
    return resp.json()


async def test_binding_a_catalog_entry_prices_an_unpriced_tier_and_unbinding_reverts_it(agent_config_client) -> None:
    created = await _create_gateway_credential(agent_config_client)
    assert _tiers(created)["model"]["priced"] is False

    bound = await agent_config_client.put(
        f"/api/v1/agent/credentials/{created['id']}/tiers/model/binding",
        json={"entry_binding": "anthropic/claude-haiku-4-5"},
    )

    assert bound.status_code == 200
    tiers = _tiers(bound.json())
    assert tiers["model"] == {
        "model_name": "gw-llm",
        "entry_display_name": "Claude Haiku 4.5",
        "manually_bound": True,
        "binding_stale": False,
        "priced": True,
    }
    # 只绑了主模型；其余档位用主模型名，没有绑定，仍按名字对应
    assert tiers["haiku"]["manually_bound"] is False
    assert tiers["haiku"]["priced"] is False
    (listed,) = (await agent_config_client.get("/api/v1/agent/credentials")).json()["credentials"]
    assert _tiers(listed)["model"]["priced"] is True

    unbound = await agent_config_client.delete(f"/api/v1/agent/credentials/{created['id']}/tiers/model/binding")

    assert unbound.status_code == 200
    assert _tiers(unbound.json())["model"] == {
        "model_name": "gw-llm",
        "entry_display_name": None,
        "manually_bound": False,
        "binding_stale": False,
        "priced": False,
    }


async def test_binding_an_entry_missing_from_the_catalog_is_rejected_and_stores_nothing(agent_config_client) -> None:
    created = await _create_gateway_credential(agent_config_client)

    for ref in ("acme/gone", "not-a-binding", " "):
        resp = await agent_config_client.put(
            f"/api/v1/agent/credentials/{created['id']}/tiers/model/binding", json={"entry_binding": ref}
        )
        assert resp.status_code == 422

    (listed,) = (await agent_config_client.get("/api/v1/agent/credentials")).json()["credentials"]
    assert not any(t["manually_bound"] or t["binding_stale"] for t in listed["tiers"])


async def test_binding_endpoints_return_404_for_an_unknown_credential(agent_config_client) -> None:
    put = await agent_config_client.put(
        "/api/v1/agent/credentials/999/tiers/model/binding", json={"entry_binding": "anthropic/claude-haiku-4-5"}
    )
    delete = await agent_config_client.delete("/api/v1/agent/credentials/999/tiers/model/binding")

    assert (put.status_code, delete.status_code) == (404, 404)


async def test_saving_a_credential_can_submit_tier_bindings(agent_config_client) -> None:
    created = await _create_gateway_credential(
        agent_config_client,
        tier_bindings={"model": "anthropic/claude-haiku-4-5", "opus": "anthropic/claude-sonnet-4-6", "haiku": None},
    )
    tiers = _tiers(created)
    assert tiers["model"]["entry_display_name"] == "Claude Haiku 4.5"
    assert tiers["opus"]["entry_display_name"] == "Claude Sonnet 4.6"
    assert tiers["haiku"]["manually_bound"] is False

    # 更新按档位合并：给出的档位改绑，null 的档位解除，没给出的档位保持不变
    resp = await agent_config_client.patch(
        f"/api/v1/agent/credentials/{created['id']}",
        json={"tier_bindings": {"opus": None, "sonnet": "anthropic/claude-haiku-4-5"}},
    )

    assert resp.status_code == 200
    tiers = _tiers(resp.json())
    assert tiers["model"]["manually_bound"] is True
    assert tiers["opus"]["manually_bound"] is False
    assert tiers["sonnet"]["entry_display_name"] == "Claude Haiku 4.5"


async def test_saving_a_credential_with_an_unknown_binding_is_rejected_without_creating_it(agent_config_client) -> None:
    resp = await agent_config_client.post(
        "/api/v1/agent/credentials",
        json={"preset_id": "deepseek", "api_key": "sk", "tier_bindings": {"model": "acme/gone"}},
    )

    assert resp.status_code == 422
    assert (await agent_config_client.get("/api/v1/agent/credentials")).json()["credentials"] == []


async def test_a_stale_binding_to_a_delisted_entry_can_still_be_unbound(agent_config_client, db_factory) -> None:
    created = await _create_gateway_credential(agent_config_client)
    async with db_factory() as session:
        # 绑定存着，所绑条目已从目录下架
        await AgentCredentialRepository(session).update(created["id"], tier_bindings={"model": "acme/gone"})
        await session.commit()

    (listed,) = (await agent_config_client.get("/api/v1/agent/credentials")).json()["credentials"]
    assert _tiers(listed)["model"]["manually_bound"] is False
    assert _tiers(listed)["model"]["binding_stale"] is True

    # 与这条绑定无关的编辑不被它堵住
    assert (
        await agent_config_client.patch(f"/api/v1/agent/credentials/{created['id']}", json={"multiplier": 2})
    ).status_code == 200

    resp = await agent_config_client.delete(f"/api/v1/agent/credentials/{created['id']}/tiers/model/binding")

    assert resp.status_code == 200
    assert _tiers(resp.json())["model"]["binding_stale"] is False
    async with db_factory() as session:
        assert (await AgentCredentialRepository(session).get(created["id"])).tier_bindings is None


async def test_patch_updates_the_multiplier_and_rejects_negative_values(agent_config_client) -> None:
    cid = (
        await agent_config_client.post("/api/v1/agent/credentials", json={"preset_id": "deepseek", "api_key": "sk"})
    ).json()["id"]

    assert (
        await agent_config_client.patch(f"/api/v1/agent/credentials/{cid}", json={"multiplier": -1})
    ).status_code == 422
    resp = await agent_config_client.patch(f"/api/v1/agent/credentials/{cid}", json={"multiplier": 0.6})
    assert resp.status_code == 200
    assert resp.json()["multiplier"] == 0.6


@pytest.mark.asyncio
async def test_delete_active_blocked(agent_config_client) -> None:
    created = (
        await agent_config_client.post(
            "/api/v1/agent/credentials",
            json={"preset_id": "deepseek", "api_key": "sk"},
        )
    ).json()
    resp = await agent_config_client.delete(f"/api/v1/agent/credentials/{created['id']}")
    assert resp.status_code == 409


@pytest.mark.asyncio
async def test_delete_nonexistent_returns_404(agent_config_client) -> None:
    resp = await agent_config_client.delete("/api/v1/agent/credentials/9999")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_delete_inactive_returns_204(agent_config_client) -> None:
    # 第一条自动 active
    await agent_config_client.post(
        "/api/v1/agent/credentials",
        json={"preset_id": "deepseek", "api_key": "sk-A"},
    )
    # 第二条显式 activate=False，可删
    second = (
        await agent_config_client.post(
            "/api/v1/agent/credentials",
            json={"preset_id": "kimi", "api_key": "sk-B", "activate": False},
        )
    ).json()
    resp = await agent_config_client.delete(f"/api/v1/agent/credentials/{second['id']}")
    assert resp.status_code == 204


# ── Task 11: POST /agent/credentials/{id}/activate ────────────────


@pytest.mark.asyncio
async def test_activate_credential_switches(agent_config_client, monkeypatch) -> None:
    a = (
        await agent_config_client.post(
            "/api/v1/agent/credentials",
            json={"preset_id": "deepseek", "api_key": "sk-A"},
        )
    ).json()
    b = (
        await agent_config_client.post(
            "/api/v1/agent/credentials",
            json={"preset_id": "kimi", "api_key": "sk-B", "activate": False},
        )
    ).json()
    assert a["is_active"] is True
    assert b["is_active"] is False

    resp = await agent_config_client.post(f"/api/v1/agent/credentials/{b['id']}/activate")
    assert resp.status_code == 200
    assert resp.json() == {"active_id": b["id"]}

    listing = (await agent_config_client.get("/api/v1/agent/credentials")).json()["credentials"]
    flags = {c["id"]: c["is_active"] for c in listing}
    assert flags[a["id"]] is False
    assert flags[b["id"]] is True


@pytest.mark.asyncio
async def test_activate_unknown_id(agent_config_client) -> None:
    resp = await agent_config_client.post("/api/v1/agent/credentials/99999/activate")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_test_connection_draft_calls_run_test(agent_config_client, monkeypatch) -> None:
    """POST /agent/test-connection 调 run_test 并把结果序列化为 JSON。"""
    from unittest.mock import AsyncMock

    from lib.config import anthropic_probe as probe_mod

    expected = probe_mod.TestConnectionResponse(
        overall="ok",
        messages_probe=probe_mod.ProbeResult(success=True, status_code=200, latency_ms=10, error=None),
        diagnosis=None,
        suggestion=None,
        messages_url="https://api.deepseek.com/anthropic/v1/messages",
    )
    fake = AsyncMock(return_value=expected)
    monkeypatch.setattr("server.routers.agent_config.run_test", fake)

    resp = await agent_config_client.post(
        "/api/v1/agent/test-connection",
        json={"preset_id": "deepseek", "api_key": "sk", "model": None, "base_url": None},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["overall"] == "ok"
    assert body["messages_probe"]["success"] is True
    assert body["messages_url"] == "https://api.deepseek.com/anthropic/v1/messages"
    assert "discovery_probe" not in body
    assert "derived_discovery_root" not in body
    fake.assert_awaited_once()


@pytest.mark.asyncio
async def test_test_credential_uses_stored(agent_config_client, monkeypatch) -> None:
    from unittest.mock import AsyncMock

    from lib.config import anthropic_probe as probe_mod

    expected = probe_mod.TestConnectionResponse(
        overall="fail",
        messages_probe=probe_mod.ProbeResult(success=False, status_code=401, latency_ms=12, error="bad"),
        diagnosis=probe_mod.DiagnosisCode.AUTH_FAILED,
        suggestion=None,
        messages_url="https://api.deepseek.com/anthropic/v1/messages",
    )
    fake = AsyncMock(return_value=expected)
    monkeypatch.setattr("server.routers.agent_config.run_test", fake)

    cred = (
        await agent_config_client.post(
            "/api/v1/agent/credentials",
            json={"preset_id": "deepseek", "api_key": "sk-stored"},
        )
    ).json()
    resp = await agent_config_client.post(f"/api/v1/agent/credentials/{cred['id']}/test")
    assert resp.status_code == 200
    body = resp.json()
    assert body["overall"] == "fail"
    assert body["diagnosis"] == "auth_failed"
    fake.assert_awaited_once()
    kwargs = fake.await_args.kwargs
    assert kwargs["api_key"] == "sk-stored"
    assert kwargs["preset_id"] == "deepseek"
