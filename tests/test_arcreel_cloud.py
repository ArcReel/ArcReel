from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest
from sqlalchemy import select

from lib.config.repository import ManagedProviderConfigRepository, SystemSettingRepository
from lib.db.models.agent_credential import AgentAnthropicCredential
from lib.db.models.credential import ProviderCredential
from lib.db.models.user import ArcReelCloudSession, User
from lib.db.repositories.credential_repository import CredentialRepository
from server.services import arcreel_cloud
from server.services.account_center_sync import _apply_snapshot

pytestmark = pytest.mark.integration


def test_cloud_config_uses_bundled_public_defaults(monkeypatch):
    monkeypatch.delenv("ARCREEL_CLOUD_AUTH_URL", raising=False)
    monkeypatch.delenv("ARCREEL_CLOUD_PUBLISHABLE_KEY", raising=False)

    config = arcreel_cloud.cloud_config()

    assert config is not None
    assert config.auth_url == "https://serqlgpuxrznwfapwcya.supabase.co/functions/v1/arcreel-auth"
    assert config.publishable_key.startswith("sb_publishable_")
    assert arcreel_cloud.cloud_enabled() is True


def test_cloud_config_requires_complete_override(monkeypatch):
    monkeypatch.setenv("ARCREEL_CLOUD_AUTH_URL", "https://cloud.example/functions/v1/arcreel-auth")
    monkeypatch.delenv("ARCREEL_CLOUD_PUBLISHABLE_KEY", raising=False)

    with pytest.raises(arcreel_cloud.ArcReelCloudError) as exc_info:
        arcreel_cloud.cloud_config()

    assert exc_info.value.code == "ARCREEL_CLOUD_CONFIG_INVALID"


def test_cloud_config_supports_explicit_development_disable(monkeypatch):
    monkeypatch.setenv("ARCREEL_CLOUD_ENABLED", "false")

    assert arcreel_cloud.cloud_config() is None
    assert arcreel_cloud.cloud_enabled() is False


async def test_cloud_login_creates_stable_shadow_user_and_applies_scoped_credentials(
    db_factory,
    monkeypatch,
):
    monkeypatch.setenv("ARCREEL_CLOUD_AUTH_URL", "https://cloud.example/functions/v1/arcreel-auth")
    monkeypatch.setenv("ARCREEL_CLOUD_PUBLISHABLE_KEY", "publishable-test-key")

    async def fake_request(method, url, config, **kwargs):
        request = httpx.Request(method, url)
        if url.endswith("/login"):
            assert kwargs["json"] == {"username": "alice", "password": "Strong123."}
            return httpx.Response(
                200,
                request=request,
                json={
                    "access_token": "access-token",
                    "refresh_token": "refresh-token",
                    "user": {
                        "id": "7a834ad1-30b4-494d-b780-e1fe47659238",
                        "username": "alice",
                        "display_name": "Alice",
                        "role": "user",
                    },
                },
            )
        return httpx.Response(
            200,
            request=request,
            json={
                "revision": 4,
                "credentials": [
                    {
                        "provider_id": "openai",
                        "name": "中台分配",
                        "api_key": "sk-alice",
                        "base_url": "https://api.example.com/v1",
                        "image_max_workers": "7",
                        "video_max_workers": "3",
                    }
                ],
                "agent_credential": {
                    "preset_id": "deepseek",
                    "display_name": "Alice Agent",
                    "base_url": "https://api.deepseek.com/anthropic",
                    "api_key": "agent-alice-secret",
                    "model": "deepseek-v4-pro",
                    "haiku_model": "deepseek-chat-fast",
                    "sonnet_model": "deepseek-chat",
                    "opus_model": "deepseek-reasoner",
                    "subagent_model": "deepseek-chat-subagent",
                },
                "global_configs": {
                    "character_catalog": {
                        "api_url": "https://catalog.example/functions/v1/export",
                        "api_token": "global-catalog-secret",
                    }
                },
            },
        )

    monkeypatch.setattr(arcreel_cloud, "_request", fake_request)
    async with db_factory() as session:
        first = await arcreel_cloud.login_with_cloud(session, "alice", "Strong123.")
        first_id = first.id
        second = await arcreel_cloud.login_with_cloud(session, "alice", "Strong123.")
        assert second.id == first_id

        users = list((await session.execute(select(User))).scalars())
        assert len(users) == 1
        assert users[0].arcreel_cloud_sub == "7a834ad1-30b4-494d-b780-e1fe47659238"
        assert users[0].display_name == "Alice"

        cloud_session = await session.get(ArcReelCloudSession, first_id)
        assert cloud_session is not None
        assert cloud_session.cloud_user_sub == users[0].arcreel_cloud_sub
        assert cloud_session.config_revision == 4
        assert "refresh-token" not in cloud_session.refresh_token_encrypted

        credentials = list((await session.execute(select(ProviderCredential))).scalars())
        assert len(credentials) == 1
        assert credentials[0].user_id == first_id
        assert credentials[0].provider == "openai"
        assert credentials[0].api_key == "sk-alice"
        assert credentials[0].management_source == "arcreel_cloud"

        managed_config = await ManagedProviderConfigRepository(session, first_id).get_all("openai")
        assert managed_config == {"image_max_workers": "7", "video_max_workers": "3"}

        agent_credentials = list((await session.execute(select(AgentAnthropicCredential))).scalars())
        assert len(agent_credentials) == 1
        assert agent_credentials[0].user_id == first_id
        assert agent_credentials[0].api_key == "agent-alice-secret"
        assert agent_credentials[0].management_source == "arcreel_cloud"
        assert agent_credentials[0].is_active is True
        assert agent_credentials[0].haiku_model == "deepseek-chat-fast"
        assert agent_credentials[0].sonnet_model == "deepseek-chat"
        assert agent_credentials[0].opus_model == "deepseek-reasoner"
        assert agent_credentials[0].subagent_model == "deepseek-chat-subagent"

        settings = await SystemSettingRepository(session).get_all()
        assert settings["croco_characters_api_url"] == "https://catalog.example/functions/v1/export"
        assert settings["croco_characters_api_token"] == "global-catalog-secret"
        assert settings["croco_characters_management_source"] == "arcreel_cloud"


async def test_cloud_login_reuses_unbound_same_name_user(db_factory, monkeypatch):
    monkeypatch.setenv("ARCREEL_CLOUD_AUTH_URL", "https://cloud.example/functions/v1/arcreel-auth")
    monkeypatch.setenv("ARCREEL_CLOUD_PUBLISHABLE_KEY", "publishable-test-key")

    async def fake_request(method, url, config, **kwargs):
        request = httpx.Request(method, url)
        if url.endswith("/login"):
            return httpx.Response(
                200,
                request=request,
                json={
                    "access_token": "access-token",
                    "refresh_token": "refresh-token",
                    "user": {"id": "cloud-alice", "username": "alice", "display_name": None, "role": "admin"},
                },
            )
        return httpx.Response(200, request=request, json={"revision": 0, "credentials": []})

    monkeypatch.setattr(arcreel_cloud, "_request", fake_request)
    async with db_factory() as session:
        session.add(User(id="legacy-id", username="alice", role="user", is_active=True))
        await session.commit()
        user = await arcreel_cloud.login_with_cloud(session, "alice", "Strong123.")
        assert user.id == "legacy-id"
        assert user.arcreel_cloud_sub == "cloud-alice"
        assert user.role == "admin"


async def test_vertex_file_and_advanced_settings_are_account_scoped_and_removed(db_factory):
    async with db_factory() as session:
        user = User(id="vertex-user", username="vertex-user", role="user", is_active=True)
        session.add(user)
        await session.flush()
        await _apply_snapshot(
            session,
            user.id,
            8,
            [
                {
                    "provider_id": "gemini-vertex",
                    "name": "中台 Vertex",
                    "credentials_json": '{"type":"service_account","project_id":"managed-project"}',
                    "gcs_bucket": "managed-bucket",
                    "image_rpm": "12.5",
                    "video_max_workers": "4",
                }
            ],
            management_source="arcreel_cloud",
        )
        credential = await CredentialRepository(session, user.id).get_active("gemini-vertex")
        assert credential is not None and credential.credentials_path
        credential_path = Path(credential.credentials_path)
        assert credential_path.is_file()
        assert json.loads(credential_path.read_text(encoding="utf-8"))["project_id"] == "managed-project"
        assert await ManagedProviderConfigRepository(session, user.id).get_all("gemini-vertex") == {
            "gcs_bucket": "managed-bucket",
            "image_rpm": "12.5",
            "video_max_workers": "4",
        }

        await _apply_snapshot(session, user.id, 9, [], management_source="arcreel_cloud")
        assert await CredentialRepository(session, user.id).get_active("gemini-vertex") is None
        assert not credential_path.exists()
        assert await ManagedProviderConfigRepository(session, user.id).get_all("gemini-vertex") == {}
