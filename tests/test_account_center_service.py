"""Account-center identity binding invariants."""

from __future__ import annotations

import pytest

import server.services.account_center as service
from lib.db.models.user import User

pytestmark = pytest.mark.unit


def test_role_mapping_is_explicit_and_fail_closed():
    assert service.resolve_local_role([service.ADMIN_ROLE_CODE]) == "admin"
    assert service.resolve_local_role([service.USER_ROLE_CODE]) == "user"
    with pytest.raises(service.AccountCenterError) as exc:
        service.resolve_local_role(["UNRELATED_ROLE"])
    assert exc.value.code == "ROLE_NOT_ASSIGNED"


def test_center_me_identity_requires_this_system_grant():
    payload = {
        "profile": {
            "id": "center-user-id",
            "username": "alice",
            "display_name": "Alice",
            "contact_email": "alice@example.com",
        },
        "systems": [
            {"system_id": "other-system", "role_codes": [service.USER_ROLE_CODE]},
            {"system_id": "crocotv-arc", "role_codes": [service.ADMIN_ROLE_CODE]},
        ],
    }
    identity = service._identity_from_center_me(payload, "crocotv-arc")
    assert identity.sub == "center-user-id"
    assert identity.username == "alice"
    assert identity.roles == (service.ADMIN_ROLE_CODE,)

    with pytest.raises(service.AccountCenterError) as denied:
        service._identity_from_center_me(payload, "missing-system")
    assert denied.value.code == "SYSTEM_ACCESS_DENIED"


async def test_same_username_is_not_implicitly_bound(db_factory, monkeypatch):
    monkeypatch.setattr(service, "async_session_factory", db_factory)
    monkeypatch.setenv("AUTH_TOKEN_SECRET", "account-center-test-token-secret-at-least-32-bytes")
    async with db_factory() as session:
        session.add(User(id="legacy-user", username="alice", role="admin", is_active=True))
        await session.commit()

    identity = service.CenterIdentity(
        sub="center-sub-alice",
        username="alice",
        display_name="Alice",
        contact_email="alice@example.com",
        roles=(service.USER_ROLE_CODE,),
    )
    ticket, purpose = await service.create_login_ticket(identity)
    assert purpose == "setup"

    _token, created = await service.complete_setup(ticket, "auto")
    assert created.id != "legacy-user"
    assert created.username.startswith("alice-")
    assert created.account_center_sub == "center-sub-alice"
    assert created.role == "user"
    with pytest.raises(service.AccountCenterError) as replay:
        await service.complete_setup(ticket, "auto")
    assert replay.value.code == "LOGIN_TICKET_INVALID"

    async with db_factory() as session:
        legacy = await session.get(User, "legacy-user")
        assert legacy is not None and legacy.account_center_sub is None


async def test_setup_accepts_same_identity_bound_after_ticket_was_issued(db_factory, monkeypatch):
    monkeypatch.setattr(service, "async_session_factory", db_factory)
    monkeypatch.setenv("AUTH_TOKEN_SECRET", "account-center-test-token-secret-at-least-32-bytes")
    identity = service.CenterIdentity(
        sub="center-sub-race",
        username="race-user",
        display_name="Race User",
        contact_email=None,
        roles=(service.ADMIN_ROLE_CODE,),
    )
    ticket, purpose = await service.create_login_ticket(identity)
    assert purpose == "setup"

    async with db_factory() as session:
        session.add(
            User(
                id="already-bound",
                username="existing-local-user",
                role="user",
                is_active=True,
                account_center_sub=identity.sub,
            )
        )
        await session.commit()

    _token, user = await service.complete_setup(ticket, "auto")
    assert user.id == "already-bound"
    assert user.role == "admin"
    assert user.display_name == "Race User"


async def test_existing_local_account_cannot_be_shared_by_two_center_identities(db_factory, monkeypatch):
    monkeypatch.setattr(service, "async_session_factory", db_factory)
    monkeypatch.setattr(service, "check_credentials", lambda _username, _password: True)
    async with db_factory() as session:
        session.add(
            User(
                id="occupied-local-user",
                username="admin",
                role="admin",
                is_active=True,
                account_center_sub="center-sub-owner",
            )
        )
        await session.commit()

    identity = service.CenterIdentity(
        sub="center-sub-second",
        username="second-user",
        display_name=None,
        contact_email=None,
        roles=(service.USER_ROLE_CODE,),
    )
    ticket, purpose = await service.create_login_ticket(identity)
    assert purpose == "setup"

    with pytest.raises(service.AccountCenterError) as conflict:
        await service.complete_setup(ticket, "bind", username="admin", password="correct")
    assert conflict.value.code == "LOCAL_ACCOUNT_ALREADY_BOUND"
    assert "自动创建账号" in str(conflict.value)
