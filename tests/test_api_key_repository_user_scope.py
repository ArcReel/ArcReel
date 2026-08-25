from __future__ import annotations

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from lib.db.base import Base
from lib.db.models.user import User
from lib.db.repositories.api_key_repository import ApiKeyRepository

pytestmark = pytest.mark.unit


async def test_api_key_management_is_private_but_token_lookup_keeps_owner() -> None:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as session:
            session.add_all(
                [
                    User(id="alice", username="alice", role="admin", is_active=True),
                    User(id="bob", username="bob", role="admin", is_active=True),
                ]
            )
            await session.commit()
            alice = ApiKeyRepository(session, user_id="alice")
            bob = ApiKeyRepository(session, user_id="bob")
            created = await alice.create(name="alice-key", key_hash="hash-alice", key_prefix="arc-a")

            assert [row["id"] for row in await alice.list_all()] == [created["id"]]
            assert await bob.list_all() == []
            assert await bob.get_by_id(created["id"]) is None
            assert await bob.delete(created["id"]) is False

            auth_row = await ApiKeyRepository(session).get_by_hash("hash-alice")
            assert auth_row is not None
            assert auth_row["user_id"] == "alice"
    finally:
        await engine.dispose()
