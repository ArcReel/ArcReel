"""Service-to-service per-user provider configuration."""

from __future__ import annotations

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from lib.db.models.user import User
from lib.db.repositories.credential_repository import CredentialRepository
from server.routers.account_center_admin import (
    ManagedCredentialRequest,
    list_user_provider_credentials,
    put_user_provider_credential,
)

pytestmark = pytest.mark.unit


async def test_managed_credentials_are_saved_for_exact_bound_identity(async_session: AsyncSession):
    user = User(
        id="local-user-1",
        username="alice",
        role="user",
        is_active=True,
        account_center_sub="center-sub-1",
    )
    other = User(
        id="local-user-2",
        username="bob",
        role="user",
        is_active=True,
        account_center_sub="center-sub-2",
    )
    async_session.add_all([user, other])
    await async_session.commit()

    result = await put_user_provider_credential(
        "center-sub-1",
        "gemini-aistudio",
        ManagedCredentialRequest(name="中台分配", api_key="secret-for-alice"),
        None,
        async_session,
    )
    assert result["success"] is True

    alice_credential = await CredentialRepository(async_session, user.id).get_active("gemini-aistudio")
    bob_credential = await CredentialRepository(async_session, other.id).get_active("gemini-aistudio")
    assert alice_credential is not None and alice_credential.api_key == "secret-for-alice"
    assert bob_credential is None

    # Partial updates never require the data center to read an existing secret.
    await put_user_provider_credential(
        "center-sub-1",
        "gemini-aistudio",
        ManagedCredentialRequest(name="改名"),
        None,
        async_session,
    )
    await async_session.refresh(alice_credential)
    assert alice_credential.api_key == "secret-for-alice"
    assert alice_credential.name == "改名"

    listing = await list_user_provider_credentials("center-sub-1", None, async_session)
    assert listing["local_user_id"] == user.id
    assert listing["credentials"][0]["api_key_masked"] != "secret-for-alice"
    assert "alice" not in listing["credentials"][0]["api_key_masked"]
