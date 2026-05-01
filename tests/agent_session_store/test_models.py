"""ORM smoke tests for AgentSessionEntry / AgentSessionSummary."""

from __future__ import annotations

import pytest
from sqlalchemy import select

from lib.agent_session_store.models import AgentSessionEntry, AgentSessionSummary


@pytest.mark.asyncio
async def test_entry_can_round_trip(session_factory):
    async with session_factory() as session:
        row = AgentSessionEntry(
            project_key="proj-A",
            session_id="sess-1",
            subpath="",
            seq=0,
            uuid="00000000-0000-0000-0000-000000000001",
            entry_type="user",
            payload={"type": "user", "content": "hi"},
            mtime_ms=1714540000000,
            user_id="default",
        )
        session.add(row)
        await session.commit()

        rows = (await session.execute(select(AgentSessionEntry))).scalars().all()
    assert len(rows) == 1
    assert rows[0].payload == {"type": "user", "content": "hi"}


@pytest.mark.asyncio
async def test_summary_pk_dedup(session_factory):
    async with session_factory() as session:
        s1 = AgentSessionSummary(
            project_key="proj-A",
            session_id="sess-1",
            mtime_ms=1,
            data={"v": 1},
            user_id="default",
        )
        session.add(s1)
        await session.commit()

        # 同 PK 二次插入：必须报 IntegrityError
        from sqlalchemy.exc import IntegrityError

        s2 = AgentSessionSummary(
            project_key="proj-A",
            session_id="sess-1",
            mtime_ms=2,
            data={"v": 2},
            user_id="default",
        )
        session.add(s2)
        with pytest.raises(IntegrityError):
            await session.commit()
