from __future__ import annotations

import pytest
from sqlalchemy import select

from lib.db.models.credential import ProviderCredential
from lib.db.models.user import User
from server.services.account_center_sync import _apply_snapshot

pytestmark = pytest.mark.integration


async def test_center_snapshot_is_scoped_and_preserves_local_credentials(db_factory):
    async with db_factory() as session:
        session.add_all(
            [
                User(id="u1", username="one", role="user", is_active=True, account_center_sub="center-one"),
                User(id="u2", username="two", role="user", is_active=True, account_center_sub="center-two"),
                ProviderCredential(
                    user_id="u1",
                    provider="ark",
                    name="old center value",
                    api_key="old",
                    is_active=True,
                    management_source="account_center",
                    management_revision=1,
                ),
                ProviderCredential(
                    user_id="u1",
                    provider="ark",
                    name="local ark backup",
                    api_key="local-ark-secret",
                    is_active=False,
                ),
                ProviderCredential(
                    user_id="u1",
                    provider="grok",
                    name="local value",
                    api_key="local-secret",
                    is_active=True,
                ),
                ProviderCredential(
                    user_id="u2",
                    provider="openai",
                    name="other user",
                    api_key="other-secret",
                    is_active=True,
                    management_source="account_center",
                    management_revision=1,
                ),
            ]
        )
        await session.commit()

        await _apply_snapshot(
            session,
            "u1",
            7,
            [
                {
                    "provider_id": "gemini-aistudio",
                    "name": "数据中台分配",
                    "api_key": "center-secret",
                    "base_url": "https://example.com/v1",
                }
            ],
        )
        await session.commit()

        rows = list((await session.execute(select(ProviderCredential).order_by(ProviderCredential.user_id, ProviderCredential.provider))).scalars())
        by_key = {(row.user_id, row.provider): row for row in rows if row.provider != "ark"}
        remaining_ark = [row for row in rows if row.user_id == "u1" and row.provider == "ark"]

        assert len(remaining_ark) == 1
        assert remaining_ark[0].api_key == "local-ark-secret"
        assert remaining_ark[0].is_active is True
        assert by_key[("u1", "gemini-aistudio")].api_key == "center-secret"
        assert by_key[("u1", "gemini-aistudio")].management_source == "account_center"
        assert by_key[("u1", "gemini-aistudio")].management_revision == 7
        assert by_key[("u1", "grok")].api_key == "local-secret"
        assert by_key[("u1", "grok")].management_source is None
        assert by_key[("u2", "openai")].api_key == "other-secret"
